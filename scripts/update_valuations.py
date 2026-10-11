from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.financials import (  # noqa: E402
    SEC_COMPANY_TICKERS_URL,
    KNOWN_CIKS,
    FinancialDataError,
    SecClient,
    build_cik_map,
    fetch_company_periods,
)
from stocklens.macro import MacroDataError, fetch_ten_year_rate  # noqa: E402
from stocklens.storage import SnapshotStore  # noqa: E402
from stocklens.valuation import MODEL_VERSION, ValuationResult, evaluate_company  # noqa: E402
from stocklens.valuation_storage import ValuationStore  # noqa: E402


def _safe_error(ticker: str, exc: Exception) -> str:
    return f"{ticker}: {type(exc).__name__}"


def _hold_result(row: pd.Series, evaluated_at: datetime, reason: str) -> ValuationResult:
    return ValuationResult(
        ticker=str(row.ticker),
        company_name=str(row["name"]),
        model_version=MODEL_VERSION,
        evaluated_at_utc=evaluated_at.replace(microsecond=0).isoformat(),
        price=float(row.price),
        price_as_of_utc=str(row.market_time_utc),
        financials_as_of_utc=None,
        conservative_value=None,
        base_value=None,
        optimistic_value=None,
        upside_to_base=None,
        safety_margin=None,
        favorable_buy_low=None,
        favorable_buy_high=None,
        judgement="판단 보류",
        confidence_score=0,
        uncertainty="very_high",
        valuation_family="unavailable",
        methods_used="none",
        model_details_json="[]",
        growth_drivers="검증 가능한 재무 데이터 확보 후 산출",
        risk_factors=reason,
        eps_revision_trend="unavailable: no redistribution-safe consensus source configured",
        status="hold",
        change_reason=reason,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh point-in-time SEC financials and valuations")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--tickers", nargs="*", help="Optional development subset")
    parser.add_argument("--max-companies", type=int, default=None)
    parser.add_argument("--allow-partial", action="store_true", help="Allow fewer than 200 outputs for development")
    args = parser.parse_args()

    snapshot_store = SnapshotStore(args.data_dir)
    universe = snapshot_store.load()
    universe_status = snapshot_store.load_status()
    if universe_status.get("freshness_state") != "verified" or universe_status.get("data_state") not in {"healthy", "degraded"}:
        raise RuntimeError("universe snapshot is not eligible for a new valuation run")

    if args.tickers:
        requested = {ticker.upper() for ticker in args.tickers}
        universe = universe[universe["ticker"].isin(requested)]
    if args.max_companies:
        universe = universe.head(args.max_companies)
    if universe.empty:
        raise RuntimeError("no universe rows selected")

    market_as_of = pd.to_datetime(universe_status["market_data_as_of_utc"], utc=True).to_pydatetime()
    evaluated_at = market_as_of.astimezone(timezone.utc)
    valuation_store = ValuationStore(args.data_dir)
    cached_periods = valuation_store.load_financials()
    cached_by_ticker: dict[str, list] = {}
    for period in cached_periods:
        cached_by_ticker.setdefault(period.ticker, []).append(period)

    sec = SecClient()
    errors: list[str] = []
    try:
        cik_map = {**KNOWN_CIKS, **build_cik_map(sec.get_json(SEC_COMPANY_TICKERS_URL))}
    except FinancialDataError as exc:
        cik_map = dict(KNOWN_CIKS)
        errors.append(_safe_error("SEC ticker map", exc))
    try:
        risk_free = fetch_ten_year_rate(as_of_date=date.fromisoformat(str(universe_status["market_session_date"])))
    except MacroDataError as exc:
        previous = valuation_store.load_status().get("risk_free_rate") or {}
        if previous.get("rate") and str(previous.get("as_of") or "") <= str(universe_status["market_session_date"]):
            risk_free = type("CachedRate", (), previous)()
            errors.append(_safe_error("Treasury cached", exc))
        else:
            risk_free = None
            errors.append(_safe_error("Treasury", exc))

    previous = valuation_store.load_valuations()
    if previous.empty is False and (not cik_map or risk_free is None):
        prior_status = valuation_store.load_status()
        valuation_store.save_status({
            **prior_status,
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "last_refresh_attempt_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "data_state": "degraded",
            "used_cache": True,
            "provider_error_count": len(errors),
            "provider_errors": errors,
            "eligible_for_new_valuation": False,
        })
        print(json.dumps({"preserved_last_good": True, "provider_error_count": len(errors)}, sort_keys=True))
        return 0

    refreshed_by_ticker: dict[str, list] = {}
    cached_tickers: set[str] = set()
    for row in universe.itertuples(index=False):
        ticker = str(row.ticker)
        cik = cik_map.get(ticker.replace("-", "."))
        if cik is None:
            refreshed_by_ticker[ticker] = [replace(period, validation_state="degraded", validation_notes="cached after CIK lookup failure") for period in cached_by_ticker.get(ticker, [])]
            cached_tickers.add(ticker)
            errors.append(f"{ticker}: SEC CIK unavailable")
            continue
        try:
            periods = fetch_company_periods(sec, ticker, cik, as_of=evaluated_at)
            refreshed_by_ticker[ticker] = periods or cached_by_ticker.get(ticker, [])
            if not periods:
                errors.append(f"{ticker}: no eligible point-in-time annual facts")
        except FinancialDataError as exc:
            refreshed_by_ticker[ticker] = [replace(period, validation_state="degraded", validation_notes="cached after SEC provider failure") for period in cached_by_ticker.get(ticker, [])]
            cached_tickers.add(ticker)
            errors.append(_safe_error(ticker, exc))

    selected_tickers = set(universe["ticker"])
    retained = [period for period in cached_periods if period.ticker not in selected_tickers]
    all_periods = retained + [period for periods in refreshed_by_ticker.values() for period in periods]
    valuation_store.save_financials(all_periods)

    previous_by_ticker = previous.set_index("ticker").to_dict(orient="index") if not previous.empty else {}
    results: list[ValuationResult] = []
    for _, row in universe.iterrows():
        ticker = str(row.ticker)
        if risk_free is None:
            result = _hold_result(row, evaluated_at, "official risk-free rate unavailable")
        else:
            result = evaluate_company(
                ticker=ticker,
                company_name=str(row["name"]),
                sector=str(row["sector"]),
                industry=str(row["industry"]),
                price=float(row["price"]),
                price_as_of_utc=str(row["market_time_utc"]),
                periods=refreshed_by_ticker.get(ticker, []),
                risk_free_rate=float(risk_free.rate),
                evaluated_at=evaluated_at,
                independent_financial_crosscheck=False,
            )
        prior = previous_by_ticker.get(ticker, {})
        prior_base = prior.get("base_value")
        if pd.notna(prior_base) and result.base_value is not None:
            changed = result.base_value / float(prior_base) - 1
            reason = "financial filing, official rate, or model-version input changed"
            result = ValuationResult(**{**result.to_dict(), "prior_base_value": float(prior_base), "base_value_change": round(changed, 6), "change_reason": reason})
        results.append(result)

    output = valuation_store.save_valuations(
        results,
        require_200=not (args.allow_partial or args.tickers or args.max_companies),
    )
    evaluated = int(output["status"].eq("evaluated").sum())
    status = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "valuation_as_of_market_utc": universe_status["market_data_as_of_utc"],
        "universe_session_date": universe_status["market_session_date"],
        "company_count": int(len(output)),
        "evaluated_count": evaluated,
        "hold_count": int(len(output) - evaluated),
        "financial_period_count": int(sum(len(value) for value in refreshed_by_ticker.values())),
        "financial_source": "SEC EDGAR companyfacts/submissions",
        "independent_financial_crosscheck": "not_configured",
        "consensus_source": "not_configured",
        "risk_free_rate": None if risk_free is None else {
            "as_of": risk_free.as_of,
            "tenor": risk_free.tenor,
            "rate": risk_free.rate,
            "source": risk_free.source,
            "source_url": risk_free.source_url,
        },
        "provider_error_count": len(errors),
        "provider_errors": errors[:200],
        "cached_ticker_count": len(cached_tickers),
        "used_cache": bool(cached_tickers),
        "data_state": "degraded" if errors else "healthy",
        "eligible_for_new_valuation": risk_free is not None and evaluated > 0,
        "eligible_for_display": evaluated > 0,
        "limitations": [
            "Consensus EPS/revenue revisions remain unavailable without a redistribution-safe free source.",
            "Management guidance text is not inferred from XBRL and remains unavailable until a traceable filing-text extractor is validated.",
            "Independent financial-statement provider cross-check is not configured; SEC internal EPS consistency checks are used.",
            "IFRS and verified REIT FFO/AFFO mappings remain on hold.",
        ],
    }
    valuation_store.save_status(status)
    print(json.dumps({key: status[key] for key in ("company_count", "evaluated_count", "hold_count", "financial_period_count", "provider_error_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

