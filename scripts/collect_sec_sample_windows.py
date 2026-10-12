from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.financials import FinancialDataError, KNOWN_CIKS  # noqa: E402
from stocklens.macro import MacroDataError, fetch_ten_year_rate  # noqa: E402
from stocklens.sec_files import SecFileProvider  # noqa: E402
from stocklens.sec_windows import SAMPLE_TICKERS, SecWindowsDownloader  # noqa: E402
from stocklens.storage import SnapshotStore  # noqa: E402
from stocklens.valuation import evaluate_company  # noqa: E402


PROJECT_URL = "https://github.com/bomisian2/stocklens200"


def _safe_error(exc: Exception) -> dict:
    if isinstance(exc, FinancialDataError):
        return exc.diagnostic.to_dict()
    return {"category": "unexpected", "error_type": type(exc).__name__}


def _write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Probe official SEC access once on Windows and, only after success, "
            "collect and validate the four-company sample"
        )
    )
    parser.add_argument(
        "--contact",
        default=os.environ.get("STOCKLENS_SEC_CONTACT"),
        help=(
            "Monitored contact email for the SEC User-Agent; alternatively set "
            "STOCKLENS_SEC_CONTACT"
        ),
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "sec-import")
    parser.add_argument("--minimum-interval", type=float, default=0.6)
    args = parser.parse_args()

    if not args.contact or "@" not in args.contact or "noreply" in args.contact.lower():
        parser.error(
            "provide a monitored contact email with --contact or STOCKLENS_SEC_CONTACT"
        )

    user_agent = f"StockLens200/2.2 ({PROJECT_URL}; mailto:{args.contact})"
    snapshot_store = SnapshotStore(args.data_dir)
    universe = snapshot_store.load()
    universe_status = snapshot_store.load_status()
    if universe_status.get("freshness_state") != "verified":
        raise RuntimeError("universe snapshot must be verified before sample valuation")
    market_as_of = pd.to_datetime(
        universe_status["market_data_as_of_utc"], utc=True
    ).to_pydatetime()

    downloader = SecWindowsDownloader(
        user_agent=user_agent,
        minimum_interval=args.minimum_interval,
    )
    attempt_path = args.output_root / "last_attempt.json"
    try:
        collected = downloader.collect(
            output_root=args.output_root,
            tickers=SAMPLE_TICKERS,
            as_of=market_as_of,
        )
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "status": "access_failed",
            "attempted_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "retry_count": 0,
            "request_count": downloader.request_count,
            "company_requests_started": downloader.company_requests_started,
            "diagnostic": _safe_error(exc),
            "policy": "no retry, proxy, IP rotation, or access-control bypass",
        }
        _write_report(attempt_path, failure)
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
        return 2

    run_dir = Path(collected["run_directory"])
    provider = SecFileProvider.from_manifest(collected["manifest"])
    risk_free = None
    risk_free_error = None
    try:
        risk_free = fetch_ten_year_rate(
            as_of_date=date.fromisoformat(str(universe_status["market_session_date"]))
        )
    except MacroDataError as exc:
        risk_free_error = type(exc).__name__

    rows_by_ticker = {str(row.ticker): row for row in universe.itertuples(index=False)}
    periods = []
    valuations = []
    for ticker in SAMPLE_TICKERS:
        ticker_periods = provider.fetch_periods(
            ticker, KNOWN_CIKS[ticker], as_of=market_as_of
        )
        periods.extend(ticker_periods)
        row = rows_by_ticker.get(ticker)
        if row is None or risk_free is None:
            continue
        valuations.append(evaluate_company(
            ticker=ticker,
            company_name=str(row.name),
            sector=str(row.sector),
            industry=str(row.industry),
            price=float(row.price),
            price_as_of_utc=str(row.market_time_utc),
            periods=ticker_periods,
            risk_free_rate=float(risk_free.rate),
            evaluated_at=market_as_of,
            independent_financial_crosscheck=False,
        ))

    financial_frame = pd.DataFrame([row.to_dict() for row in periods])
    valuation_frame = pd.DataFrame([row.to_dict() for row in valuations])
    financial_frame.to_csv(run_dir / "financials.csv", index=False, encoding="utf-8")
    valuation_frame.to_csv(run_dir / "valuations.csv", index=False, encoding="utf-8")
    evaluated_count = (
        int(valuation_frame["status"].eq("evaluated").sum())
        if not valuation_frame.empty else 0
    )
    completed = {
        **collected,
        "status": "completed",
        "risk_free_rate": None if risk_free is None else {
            "as_of": risk_free.as_of,
            "rate": risk_free.rate,
            "source": risk_free.source,
            "source_url": risk_free.source_url,
        },
        "risk_free_error": risk_free_error,
        "actual_financial_ticker_count": len({row.ticker for row in periods}),
        "valuation_output_count": len(valuations),
        "evaluated_count": evaluated_count,
        "hold_count": len(valuations) - evaluated_count,
        "valuations": [row.to_dict() for row in valuations],
    }
    _write_report(run_dir / "result.json", completed)
    _write_report(attempt_path, {
        "status": "completed",
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "run_directory": str(run_dir),
        "manifest": collected["manifest"],
        "actual_financial_ticker_count": completed["actual_financial_ticker_count"],
        "evaluated_count": evaluated_count,
        "hold_count": completed["hold_count"],
    })
    print(json.dumps({
        "status": completed["status"],
        "run_directory": str(run_dir),
        "actual_financial_ticker_count": completed["actual_financial_ticker_count"],
        "financial_period_count": len(periods),
        "evaluated_count": evaluated_count,
        "hold_count": completed["hold_count"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if evaluated_count == len(SAMPLE_TICKERS) else 3


if __name__ == "__main__":
    raise SystemExit(main())

