from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.financials import (  # noqa: E402
    SEC_COMPANY_TICKERS_URL,
    KNOWN_CIKS,
    FinancialDataError,
    FinancialProvider,
    SecClient,
    SecApiProvider,
    build_cik_map,
    merge_cik_maps,
)
from stocklens.macro import MacroDataError, fetch_ten_year_rate  # noqa: E402
from stocklens.ir_files import IssuerIrFileProvider  # noqa: E402
from stocklens.sec_files import SecFileProvider  # noqa: E402
from stocklens.storage import SnapshotStore  # noqa: E402
from stocklens.ttm import append_latest_ttm  # noqa: E402
from stocklens.valuation import MODEL_VERSION, ValuationResult, evaluate_company  # noqa: E402
from stocklens.valuation_evidence import (  # noqa: E402
    ValuationEvidenceError,
    load_valuation_evidence,
)
from stocklens.valuation_storage import ValuationStore  # noqa: E402


MAX_FINANCIAL_AGE_DAYS = 190


def _safe_error(ticker: str, exc: Exception) -> dict:
    if isinstance(exc, FinancialDataError):
        return {"ticker": ticker, **exc.diagnostic.to_dict()}
    return {"ticker": ticker, "category": "unexpected", "error_type": type(exc).__name__}


def _stale_prior_result(prior: dict, reason: str) -> ValuationResult:
    values = {}
    nullable_numbers = {
        "financials_as_of_utc", "conservative_value", "base_value", "optimistic_value",
        "upside_to_base", "safety_margin", "favorable_buy_low", "favorable_buy_high",
        "prior_base_value", "base_value_change",
    }
    for field in fields(ValuationResult):
        value = prior.get(field.name, field.default)
        if field.name in nullable_numbers and pd.isna(value):
            value = None
        values[field.name] = value
    values.update({
        "status": "stale",
        "judgement": "판단 보류",
        "change_reason": reason,
    })
    return ValuationResult(**values)


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


def _latest_available_at(periods: list) -> datetime | None:
    values = []
    for period in periods:
        try:
            values.append(datetime.fromisoformat(period.available_at_utc).astimezone(timezone.utc))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def _latest_valuation_input_available_at(periods: list) -> datetime | None:
    """Return freshness for verified FY or derived TTM model inputs."""
    ttm = [period for period in periods if period.period_type == "TTM"]
    return _latest_available_at(ttm or [period for period in periods if period.period_type == "FY"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh point-in-time SEC financials and valuations")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--tickers", nargs="*", help="Optional development subset")
    parser.add_argument("--max-companies", type=int, default=None)
    parser.add_argument("--allow-partial", action="store_true", help="Allow fewer than 200 outputs for development")
    parser.add_argument(
        "--financial-source", choices=("offline", "issuer-ir", "sec-api"), default="offline",
        help="Offline is the safe default; sec-api must be selected explicitly",
    )
    parser.add_argument("--sec-manifest", type=Path, default=None, help="Hash-verified manifest for official SEC files")
    parser.add_argument("--ir-manifest", type=Path, default=None, help="Hash-verified manifest for official issuer IR PDFs")
    parser.add_argument(
        "--valuation-evidence",
        type=Path,
        default=None,
        help="Point-in-time P/E, EPS-quality, forecast and comparable evidence JSON",
    )
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
    valuation_evidence = {}
    if args.valuation_evidence is not None:
        try:
            valuation_evidence = load_valuation_evidence(
                args.valuation_evidence,
                evaluated_at=evaluated_at,
                allow_fixture=False,
            )
        except (OSError, ValuationEvidenceError) as exc:
            raise RuntimeError(f"valuation evidence rejected: {exc}") from exc
    valuation_store = ValuationStore(args.data_dir)
    cached_periods = valuation_store.load_financials()
    cached_by_ticker: dict[str, list] = {}
    for period in cached_periods:
        cached_by_ticker.setdefault(period.ticker, []).append(period)

    errors: list[dict] = []
    providers: list[FinancialProvider] = []
    offline_provider: SecFileProvider | IssuerIrFileProvider | None = None
    cik_map = dict(KNOWN_CIKS)
    if args.financial_source == "sec-api":
        sec = SecClient()
        try:
            cik_map = merge_cik_maps(
                cik_map,
                build_cik_map(sec.get_json(SEC_COMPANY_TICKERS_URL)),
                provider="sec_api",
            )
        except FinancialDataError as exc:
            errors.append(_safe_error("SEC ticker map", exc))
        providers.append(SecApiProvider(sec))
    elif args.financial_source == "issuer-ir" and args.ir_manifest is None:
        errors.append({
            "ticker": "issuer IR import",
            "provider": "issuer_ir_official_files",
            "category": "manifest_not_configured",
        })
    elif args.financial_source == "issuer-ir":
        try:
            offline_provider = IssuerIrFileProvider.from_manifest(args.ir_manifest)
            cik_map = merge_cik_maps(
                cik_map, offline_provider.cik_map(), provider="issuer_ir_official_files"
            )
            providers.append(offline_provider)
        except (ValueError, FinancialDataError) as exc:
            errors.append(_safe_error("issuer IR import", exc))
    elif args.sec_manifest is None:
        errors.append({
            "ticker": "offline import",
            "provider": "sec_offline_files",
            "category": "manifest_not_configured",
        })
    else:
        try:
            offline_provider = SecFileProvider.from_manifest(args.sec_manifest)
            cik_map = merge_cik_maps(
                cik_map, offline_provider.cik_map(), provider="sec_offline_files"
            )
            providers.append(offline_provider)
        except (ValueError, FinancialDataError) as exc:
            errors.append(_safe_error("offline import", exc))
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
            "financial_freshness_state": "stale",
            "used_cache": True,
            "provider_error_count": len(errors),
            "provider_errors": errors,
            "eligible_for_new_valuation": False,
            "eligible_for_new_signals": False,
        })
        print(json.dumps({"preserved_last_good": True, "provider_error_count": len(errors)}, sort_keys=True))
        return 0

    refreshed_by_ticker: dict[str, list] = {}
    cached_tickers: set[str] = set()
    unavailable_tickers: set[str] = set()
    stale_tickers: set[str] = set()
    fresh_tickers: set[str] = set()
    degraded_period_tickers: set[str] = set()
    for row in universe.itertuples(index=False):
        ticker = str(row.ticker)
        cik = cik_map.get(ticker.replace("-", "."))
        if cik is None:
            refreshed_by_ticker[ticker] = list(cached_by_ticker.get(ticker, []))
            if refreshed_by_ticker[ticker]:
                cached_tickers.add(ticker)
            unavailable_tickers.add(ticker)
            errors.append({"ticker": ticker, "provider": "sec", "category": "cik_unavailable"})
            continue
        periods = []
        for provider in providers:
            try:
                periods = provider.fetch_periods(ticker, cik, as_of=evaluated_at)
                if args.financial_source == "issuer-ir":
                    periods = append_latest_ttm(periods)
                if periods:
                    break
                errors.append({"ticker": ticker, "provider": provider.name, "category": "no_eligible_point_in_time_facts"})
            except FinancialDataError as exc:
                errors.append(_safe_error(ticker, exc))
        if periods:
            refreshed_by_ticker[ticker] = periods
            if any(period.validation_state != "verified" for period in periods):
                degraded_period_tickers.add(ticker)
            latest = _latest_valuation_input_available_at(periods)
            if latest is None or evaluated_at - latest > timedelta(days=MAX_FINANCIAL_AGE_DAYS):
                stale_tickers.add(ticker)
                errors.append({
                    "ticker": ticker,
                    "provider": providers[0].name if providers else args.financial_source,
                    "category": "stale_valuation_inputs",
                    "latest_available_at_utc": None if latest is None else latest.isoformat(),
                    "latest_provider_period_at_utc": (
                        None if _latest_available_at(periods) is None
                        else _latest_available_at(periods).isoformat()
                    ),
                    "maximum_age_days": MAX_FINANCIAL_AGE_DAYS,
                })
            else:
                fresh_tickers.add(ticker)
        else:
            refreshed_by_ticker[ticker] = list(cached_by_ticker.get(ticker, []))
            if refreshed_by_ticker[ticker]:
                cached_tickers.add(ticker)
            unavailable_tickers.add(ticker)

    if not fresh_tickers and (not previous.empty or cached_periods):
        prior_status = valuation_store.load_status()
        valuation_store.save_status({
            **prior_status,
            "last_refresh_attempt_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "data_state": "degraded",
            "financial_freshness_state": "stale",
            "used_cache": True,
            "provider_error_count": len(errors),
            "provider_errors": errors[:200],
            "stale_ticker_count": len(stale_tickers),
            "eligible_for_new_valuation": False,
            "eligible_for_new_signals": False,
        })
        print(json.dumps({"preserved_last_good": True, "provider_error_count": len(errors)}, sort_keys=True))
        return 0

    selected_tickers = set(universe["ticker"])
    retained = [period for period in cached_periods if period.ticker not in selected_tickers]
    all_periods = retained + [period for periods in refreshed_by_ticker.values() for period in periods]
    valuation_store.save_financials(all_periods)
    if offline_provider is not None:
        imported_periods = [period for periods in refreshed_by_ticker.values() for period in periods]
        provenance = offline_provider.provenance(periods=imported_periods)
        provenance["valuation_as_of_utc"] = evaluated_at.replace(microsecond=0).isoformat()
        provenance["stale_tickers"] = sorted(stale_tickers)
        valuation_store.save_provenance(provenance)

    previous_by_ticker = previous.set_index("ticker").to_dict(orient="index") if not previous.empty else {}
    results: list[ValuationResult] = []
    for _, row in universe.iterrows():
        ticker = str(row.ticker)
        prior = previous_by_ticker.get(ticker, {})
        if ticker in unavailable_tickers and prior:
            result = _stale_prior_result(prior, "provider refresh failed; prior verified valuation retained and blocked from new signals")
        elif ticker in unavailable_tickers:
            result = _hold_result(row, evaluated_at, "no verified financial provider and no prior snapshot")
        elif ticker in stale_tickers:
            latest = _latest_valuation_input_available_at(refreshed_by_ticker.get(ticker, []))
            reason = (
                f"valuation inputs are stale; latest consumed annual availability "
                f"{latest.isoformat() if latest else 'unverified'} exceeds {MAX_FINANCIAL_AGE_DAYS} days"
            )
            result = _hold_result(row, evaluated_at, reason)
        elif risk_free is None:
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
                valuation_evidence=valuation_evidence.get(ticker),
                prior_judgement=str(prior.get("judgement")) if prior else None,
            )
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
    provisional = int(output["status"].eq("provisional").sum())
    status = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "valuation_as_of_market_utc": universe_status["market_data_as_of_utc"],
        "universe_session_date": universe_status["market_session_date"],
        "company_count": int(len(output)),
        "evaluated_count": evaluated,
        "provisional_count": provisional,
        "hold_count": int(len(output) - evaluated - provisional),
        "financial_period_count": int(sum(len(value) for value in refreshed_by_ticker.values())),
        "financial_source": (
            "SEC EDGAR official offline files" if args.financial_source == "offline"
            else "Official issuer IR financial-report PDFs" if args.financial_source == "issuer-ir"
            else "SEC EDGAR companyfacts/submissions API"
        ),
        "financial_source_mode": args.financial_source,
        "independent_financial_crosscheck": "not_configured",
        "consensus_source": "not_configured",
        "valuation_evidence_configured": args.valuation_evidence is not None,
        "valuation_evidence_ticker_count": len(valuation_evidence),
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
        "stale_ticker_count": len(stale_tickers),
        "degraded_period_ticker_count": len(degraded_period_tickers),
        "degraded_period_count": int(sum(
            period.validation_state != "verified"
            for periods in refreshed_by_ticker.values()
            for period in periods
        )),
        "used_cache": bool(cached_tickers),
        "financial_freshness_state": "stale" if cached_tickers or stale_tickers else "unverified" if unavailable_tickers else "verified",
        "data_state": "degraded" if errors or degraded_period_tickers else "healthy",
        "eligible_for_new_valuation": risk_free is not None and evaluated > 0 and not unavailable_tickers and not stale_tickers,
        "eligible_for_new_signals": risk_free is not None and evaluated > 0 and not unavailable_tickers and not stale_tickers,
        "eligible_for_display": evaluated + provisional > 0,
        "limitations": [
            "Consensus EPS/revenue revisions remain unavailable without a redistribution-safe free source.",
            "Management guidance text is not inferred from XBRL and remains unavailable until a traceable filing-text extractor is validated.",
            "An independent financial-statement provider cross-check is not configured; internal statement arithmetic checks are used.",
            "Issuer IR PDFs use conservative next-day availability when an exact publication timestamp is unavailable.",
            "A TTM flow bridge is consumed only when FY and matching current/prior YTD periods reconcile to four single-quarter rows; operational TTM EPS remains unavailable because weighted diluted denominators are not additive.",
            "IFRS and verified REIT FFO/AFFO mappings remain on hold.",
            "P/E is disabled unless a 5+ year point-in-time price/EPS distribution and EPS-quality evidence are supplied.",
            "Growth/ROIC multiple adjustments are disabled unless an independently held-out calibration is accepted.",
        ],
    }
    valuation_store.save_status(status)
    print(json.dumps({key: status[key] for key in ("company_count", "evaluated_count", "provisional_count", "hold_count", "financial_period_count", "provider_error_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

