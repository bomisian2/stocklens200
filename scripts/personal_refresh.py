from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.personal import (
    AlphaVantagePersonalClient,
    collect_pilot_financials,
    import_saved_extended_financials,
)
from stocklens.personal.storage import atomic_write_json, load_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh local-only StockLens personal research data")
    parser.add_argument("--data-dir", default=ROOT / "private_data", help="Ignored local data directory")
    parser.add_argument(
        "--market-tickers",
        nargs="*",
        default=[],
        help="Optional Alpha Vantage EOD tickers (requires ALPHAVANTAGE_API_KEY)",
    )
    parser.add_argument(
        "--saved-only",
        action="store_true",
        help="Do not download issuer files; normalize only hash-pinned files already in private_data/raw",
    )
    parser.add_argument(
        "--max-market-calls",
        type=int,
        default=0,
        help="Explicit Alpha Vantage call budget. Zero makes market refresh read-only.",
    )
    return parser.parse_args()


def _merge_market_results(existing: list[dict], attempts: list[dict]) -> list[dict]:
    """Merge attempts without erasing a previously verified local snapshot."""

    by_ticker = {str(row.get("ticker", "")).upper(): dict(row) for row in existing if row.get("ticker")}
    for attempt in attempts:
        ticker = str(attempt.get("ticker", "")).upper()
        prior = by_ticker.get(ticker)
        if prior and prior.get("status") == "verified" and attempt.get("status") != "verified":
            prior["last_attempt"] = attempt
            prior["last_good_preserved"] = True
            by_ticker[ticker] = prior
        else:
            by_ticker[ticker] = attempt
    return [by_ticker[ticker] for ticker in sorted(by_ticker)]


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).resolve()
    if args.saved_only:
        status = load_json(data_dir / "status.json") or {"financial_results": []}
    else:
        status = collect_pilot_financials(data_dir)
    extended = import_saved_extended_financials(data_dir)
    status["extended_financials"] = extended
    available_files = list((data_dir / "normalized" / "financials").glob("*.json"))
    available_periods = 0
    verified_tickers = 0
    partial_tickers = 0
    financial_inventory = []
    for path in available_files:
        snapshot = load_json(path) or {}
        snapshot_status = snapshot.get("status", "unavailable")
        periods = len(snapshot.get("periods", []))
        available_periods += periods
        verified_tickers += snapshot_status == "verified"
        partial_tickers += snapshot_status == "partial"
        financial_inventory.append(
            {
                "ticker": snapshot.get("ticker", path.stem),
                "status": snapshot_status,
                "periods": periods,
                "latest_period_end": max(
                    (period.get("period_end", "") for period in snapshot.get("periods", [])),
                    default=None,
                ),
                "accounting_standard": snapshot.get("accounting_standard"),
                "currency": snapshot.get("currency"),
                "parser_version": snapshot.get("parser_version"),
            }
        )
    audit_files = [
        path
        for path in (data_dir / "audit" / "financials").glob("*.json")
        if path.name != "summary.json"
    ]
    audits = [load_json(path) or {} for path in audit_files]
    audit_checks_passed = sum(int(audit.get("checks_passed", 0)) for audit in audits)
    audit_checks_total = sum(int(audit.get("checks_total", 0)) for audit in audits)
    status["financial_tickers_available"] = len(available_files)
    status["financial_periods_available"] = available_periods
    status["financial_tickers_verified"] = verified_tickers
    status["financial_tickers_partial"] = partial_tickers
    status["financial_audits_verified"] = sum(
        audit.get("status") == "verified" for audit in audits
    )
    status["financial_audit_checks_passed"] = audit_checks_passed
    status["financial_audit_checks_total"] = audit_checks_total
    status["financial_inventory"] = sorted(financial_inventory, key=lambda row: row["ticker"])
    status["registered_pilot_tickers"] = 10
    atomic_write_json(data_dir / "status.json", status)
    if args.market_tickers:
        requested = list(dict.fromkeys(ticker.upper().strip() for ticker in args.market_tickers if ticker.strip()))
        existing_market = status.get("market") if isinstance(status.get("market"), dict) else {}
        existing_results = existing_market.get("results") if isinstance(existing_market.get("results"), list) else []
        attempt_summary = {
            "requested_tickers": requested,
            "call_budget": args.max_market_calls,
            "calls_made": 0,
        }
        key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
        if args.max_market_calls < len(requested):
            attempt_summary.update({
                "status": "blocked",
                "reason": "Explicit call budget is smaller than the requested ticker count; no calls made",
            })
            status["market"] = {**existing_market, "last_attempt": attempt_summary}
        elif not key:
            attempt_summary.update({
                "status": "blocked",
                "reason": "ALPHAVANTAGE_API_KEY is missing; no calls made",
            })
            status["market"] = {**existing_market, "last_attempt": attempt_summary}
        else:
            client = AlphaVantagePersonalClient(key)
            results = [client.collect_daily(ticker, data_dir) for ticker in requested]
            attempts = [item.__dict__ for item in results]
            merged = _merge_market_results(existing_results, attempts)
            attempt_summary.update({
                "status": "completed",
                "calls_made": len(results),
                "results": attempts,
            })
            status["market"] = {
                "status": "verified" if all(item.status == "verified" for item in results) else "partial",
                "tickers_verified": sum(item.get("status") == "verified" for item in merged),
                "results": merged,
                "last_attempt": attempt_summary,
                "scope": "local_personal_only",
                "note": "Free TIME_SERIES_DAILY returns up to 100 raw sessions per call; repeated runs merge the ignored local cache.",
            }
        atomic_write_json(data_dir / "status.json", status)
    persisted = load_json(data_dir / "status.json") or status
    print(json.dumps(persisted, ensure_ascii=False, indent=2))
    return 0 if persisted.get("financial_tickers_available", persisted.get("financial_tickers_verified", 0)) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
