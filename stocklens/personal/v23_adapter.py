"""Read-only adapter for the approved v23 public research ledger.

The adapter deliberately does not copy or mutate the external ledger.  It
normalizes only AVGO and NVDA into evidence-bearing research records.  SEC
``filingDate`` is retained as a date-only availability proxy because the
source snapshot does not contain acceptance timestamps.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Iterable


APPROVED_LEDGER_SHA256 = "78e70fe13962167034ef2052cda8aeedb8d76f8eca82b2f1e7765c4f3e7b57bd"
APPROVED_LEDGER_VERSION = "V23.5.2"
SUPPORTED_TICKERS = ("AVGO", "NVDA")
EXPECTED_HISTORICAL_ROWS = {"AVGO": 21, "NVDA": 36}
RESEARCH_STATUS = "reconstructed_pit_research_only"
REQUIRED_EVIDENCE_KEYS = {
    "source",
    "source_hash",
    "as_of",
    "available_at",
    "unit",
    "calculation_basis",
    "status",
    "operational_eligible",
}
SELF_HISTORY_PERCENTILES = (10, 25, 30, 35, 40, 50, 60, 70, 75, 85, 90)
SELF_HISTORY_MINIMUM_SAMPLE = 20


class V23LedgerError(ValueError):
    """Raised when an external v23 ledger fails the approved contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence(
    value: Any,
    *,
    source: str,
    source_hash: str,
    as_of: str | None,
    available_at: str | None,
    unit: str,
    calculation_basis: str,
    status: str = RESEARCH_STATUS,
) -> dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "source_hash": source_hash,
        "as_of": as_of,
        "available_at": available_at,
        "unit": unit,
        "calculation_basis": calculation_basis,
        "status": status,
        "operational_eligible": False,
    }


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise V23LedgerError(f"{label} must be an object")
    return value


def _require_iso_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise V23LedgerError(f"{label} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise V23LedgerError(f"{label} must be an ISO date") from exc
    return value


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise V23LedgerError(f"{label} must be a SHA-256 hash")
    try:
        int(value, 16)
    except ValueError as exc:
        raise V23LedgerError(f"{label} must be a SHA-256 hash") from exc
    return value.lower()


def _maximum_date(values: Iterable[str]) -> str:
    return max(values, key=date.fromisoformat)


def _normalize_historical_row(
    ticker: str,
    row: dict[str, Any],
    *,
    source_ledger: dict[str, Any],
) -> dict[str, Any]:
    filing_date = _require_iso_date(row.get("filingDate"), f"{ticker}.filingDate")
    price_date = _require_iso_date(row.get("priceDate"), f"{ticker}.priceDate")
    fiscal_period_end = _require_iso_date(row.get("fiscalPeriodEnd"), f"{ticker}.fiscalPeriodEnd")
    if price_date > filing_date:
        raise V23LedgerError(f"{ticker} historical price uses a future date")
    if fiscal_period_end > filing_date:
        raise V23LedgerError(f"{ticker} fiscal period ends after filing date")

    derived_hash = _require_hash(row.get("rawPayloadHash"), f"{ticker}.row.rawPayloadHash")
    sec_hash = _require_hash(row.get("secRawPayloadHash"), f"{ticker}.row.secRawPayloadHash")
    price_hash = _require_hash(row.get("priceRawPayloadHash"), f"{ticker}.row.priceRawPayloadHash")
    split_basis_factor = row.get("splitBasisFactor")
    if not isinstance(split_basis_factor, (int, float)) or isinstance(split_basis_factor, bool):
        raise V23LedgerError(f"{ticker}.row.splitBasisFactor must be numeric")
    split_basis_factor = float(split_basis_factor)
    if not math.isfinite(split_basis_factor) or split_basis_factor <= 0:
        raise V23LedgerError(f"{ticker}.row.splitBasisFactor must be positive")
    filing_url = str(row.get("canonicalUrl") or source_ledger.get("secCompanyFactsUrl") or "")
    price_url = str(source_ledger.get("priceUrl") or "")

    return {
        "filing_date": filing_date,
        "fiscal_period_end": fiscal_period_end,
        "accession": row.get("accession"),
        "availability_precision": "date_only_no_acceptance_timestamp",
        "split_basis_factor": split_basis_factor,
        "price": _evidence(
            row.get("basisPrice"),
            source=f"v23 Yahoo chart payload · {price_url}",
            source_hash=price_hash,
            as_of=price_date,
            available_at=filing_date,
            unit="USD per share",
            calculation_basis=(
                "latest close on or before filingDate, multiplied by the future split factor "
                "recorded by the v23 builder"
            ),
        ),
        "ttm_eps": _evidence(
            row.get("ttmEps"),
            source=f"v23 SEC Company Facts/Submissions · {filing_url}",
            source_hash=sec_hash,
            as_of=fiscal_period_end,
            available_at=filing_date,
            unit="USD diluted EPS per share",
            calculation_basis="sum of the four diluted-EPS quarters available by filingDate",
        ),
        "ttm_per": _evidence(
            row.get("ttmPe"),
            source="v23 reconstructed point-in-time PER ledger",
            source_hash=derived_hash,
            as_of=price_date,
            available_at=filing_date,
            unit="multiple",
            calculation_basis="split-basis price divided by trailing-four-quarter diluted EPS",
        ),
        "history_percentile": _evidence(
            row.get("historyPercentile"),
            source="v23 reconstructed point-in-time PER ledger",
            source_hash=derived_hash,
            as_of=filing_date,
            available_at=filing_date,
            unit="percentile",
            calculation_basis="percentile uses only PER rows with asOfDate on or before this filingDate",
        ),
    }


def _normalize_ticker(
    ticker: str,
    stock: dict[str, Any],
    *,
    ledger_hash: str,
    generated_at: str,
    expected_rows: int | None,
) -> dict[str, Any]:
    if stock.get("scope") != "HISTORICAL_PER_ONLY":
        raise V23LedgerError(f"{ticker} scope must be HISTORICAL_PER_ONLY")
    source_ledger = _require_mapping(stock.get("sourceLedger"), f"{ticker}.sourceLedger")
    historical_raw = stock.get("historicalPer")
    if not isinstance(historical_raw, list):
        raise V23LedgerError(f"{ticker}.historicalPer must be an array")
    if expected_rows is not None and len(historical_raw) != expected_rows:
        raise V23LedgerError(
            f"{ticker} historical row count {len(historical_raw)} does not match approved {expected_rows}"
        )
    historical = [
        _normalize_historical_row(ticker, _require_mapping(row, f"{ticker}.historicalPer"), source_ledger=source_ledger)
        for row in historical_raw
    ]

    price = _require_mapping(stock.get("price"), f"{ticker}.price")
    current_price = _require_mapping(price.get("current"), f"{ticker}.price.current")
    valuation = _require_mapping(stock.get("valuation"), f"{ticker}.valuation")
    actuals = _require_mapping(stock.get("actuals"), f"{ticker}.actuals")
    quarters = actuals.get("quarters")
    if not isinstance(quarters, list):
        raise V23LedgerError(f"{ticker}.actuals.quarters must be an array")
    latest_four = quarters[:4]
    eps_values = [row.get("dilutedEps") for row in latest_four if isinstance(row, dict)]
    ttm_eps = sum(float(value) for value in eps_values) if len(eps_values) == 4 and all(
        isinstance(value, (int, float)) for value in eps_values
    ) else None
    filing_dates = [
        _require_iso_date(row.get("filingDate"), f"{ticker}.quarter.filingDate")
        for row in latest_four
        if isinstance(row, dict) and row.get("filingDate")
    ]
    eps_available_at = _maximum_date(filing_dates) if len(filing_dates) == 4 else None
    eps_period_end = valuation.get("epsPeriodEnd")
    price_as_of = _require_iso_date(current_price.get("tradingDate"), f"{ticker}.price.current.tradingDate")
    price_hash = _require_hash(source_ledger.get("priceHash"), f"{ticker}.sourceLedger.priceHash")
    sec_hash = _require_hash(source_ledger.get("secCompanyFactsHash"), f"{ticker}.sourceLedger.secCompanyFactsHash")
    pe_available_at = _maximum_date(value for value in (price_as_of, eps_available_at) if value)
    latest_quarter = latest_four[0] if latest_four else {}

    current = {
        "price": _evidence(
            current_price.get("confirmedClose"),
            source=f"v23 Yahoo chart payload · {source_ledger.get('priceUrl', '')}",
            source_hash=price_hash,
            as_of=price_as_of,
            available_at=price_as_of,
            unit="USD per share",
            calculation_basis="latest confirmed close in the v23 snapshot; availability is date-only",
        ),
        "ttm_eps": _evidence(
            ttm_eps,
            source=f"v23 SEC Company Facts/Submissions · {source_ledger.get('secCompanyFactsUrl', '')}",
            source_hash=sec_hash,
            as_of=eps_period_end,
            available_at=eps_available_at,
            unit="USD diluted EPS per share",
            calculation_basis="sum of the first four verified diluted-EPS quarter rows in the v23 snapshot",
            status=RESEARCH_STATUS if ttm_eps is not None else "unavailable",
        ),
        "ttm_per": _evidence(
            valuation.get("ttmPe"),
            source="v23 current valuation snapshot",
            source_hash=ledger_hash,
            as_of=price_as_of,
            available_at=pe_available_at,
            unit="multiple",
            calculation_basis="latest confirmed close divided by v23 trailing-four-quarter diluted EPS",
        ),
        "reported_current_percentile": _evidence(
            _require_mapping(valuation.get("history10"), f"{ticker}.valuation.history10").get("percentile"),
            source="v23 current valuation snapshot",
            source_hash=ledger_hash,
            as_of=price_as_of,
            available_at=pe_available_at,
            unit="percentile",
            calculation_basis=(
                "v23-reported current PER percentile; independently recomputed by the StockLens "
                "self-history engine before comparison"
            ),
        ),
        "fcf_yield": _evidence(
            valuation.get("fcfYield"),
            source="v23 current valuation snapshot",
            source_hash=ledger_hash,
            as_of=valuation.get("fcfAsOf"),
            available_at=eps_available_at,
            unit="ratio",
            calculation_basis="source ledger field; no substitute is generated when null",
            status="unavailable" if valuation.get("fcfYield") is None else RESEARCH_STATUS,
        ),
        "latest_reported_fcf": _evidence(
            latest_quarter.get("fcf") if isinstance(latest_quarter, dict) else None,
            source=f"v23 SEC Company Facts/Submissions · {latest_quarter.get('canonicalUrl', '') if isinstance(latest_quarter, dict) else ''}",
            source_hash=sec_hash,
            as_of=latest_quarter.get("periodEnd") if isinstance(latest_quarter, dict) else None,
            available_at=latest_quarter.get("filingDate") if isinstance(latest_quarter, dict) else None,
            unit="USD",
            calculation_basis="latest v23 quarter FCF field; null is preserved without reconstruction",
            status=(
                "unavailable"
                if not isinstance(latest_quarter, dict) or latest_quarter.get("fcf") is None
                else RESEARCH_STATUS
            ),
        ),
        "acceptance_timestamp": _evidence(
            None,
            source=f"v23 SEC Submissions payload · {source_ledger.get('secSubmissionsUrl', '')}",
            source_hash=_require_hash(
                source_ledger.get("secSubmissionsHash"), f"{ticker}.sourceLedger.secSubmissionsHash"
            ),
            as_of=eps_period_end,
            available_at=eps_available_at,
            unit="UTC timestamp",
            calculation_basis="not present in the approved ledger; filingDate is retained only as a date proxy",
            status="unavailable",
        ),
    }
    return {
        "ticker": ticker,
        "scope": "HISTORICAL_PER_ONLY",
        "version": APPROVED_LEDGER_VERSION,
        "generated_at": generated_at,
        "ledger_sha256": ledger_hash,
        "status": RESEARCH_STATUS,
        "operational_eligible": False,
        "availability_precision": "date_only_no_acceptance_timestamp",
        "historical_row_count": len(historical),
        "historical_period": {
            "start": historical[0]["filing_date"] if historical else None,
            "end": historical[-1]["filing_date"] if historical else None,
        },
        "current": current,
        "historical_per": historical,
    }


def load_v23_public_ledger(
    path: str | Path,
    *,
    expected_sha256: str | None = APPROVED_LEDGER_SHA256,
    expected_rows: dict[str, int] | None = EXPECTED_HISTORICAL_ROWS,
) -> dict[str, Any]:
    """Load and normalize the external approved ledger without writing to it."""

    ledger_path = Path(path)
    if not ledger_path.is_file():
        raise V23LedgerError("v23 ledger path does not exist")
    ledger_hash = sha256_file(ledger_path)
    if expected_sha256 is not None and ledger_hash != expected_sha256.lower():
        raise V23LedgerError("v23 ledger SHA-256 does not match the approved snapshot")
    with ledger_path.open("r", encoding="utf-8") as stream:
        raw = json.load(stream)
    root = _require_mapping(raw, "ledger")
    if root.get("version") != APPROVED_LEDGER_VERSION:
        raise V23LedgerError("v23 ledger version is not approved")
    generated_at = str(root.get("generatedAt") or "")
    tickers = _require_mapping(root.get("tickers"), "ledger.tickers")
    normalized = {
        ticker: _normalize_ticker(
            ticker,
            _require_mapping(tickers.get(ticker), f"ledger.tickers.{ticker}"),
            ledger_hash=ledger_hash,
            generated_at=generated_at,
            expected_rows=(expected_rows or {}).get(ticker),
        )
        for ticker in SUPPORTED_TICKERS
    }
    for ticker_record in normalized.values():
        ticker_record["self_history"] = build_self_history_market_premium(ticker_record)
    return {
        "version": APPROVED_LEDGER_VERSION,
        "generated_at": generated_at,
        "ledger_sha256": ledger_hash,
        "status": RESEARCH_STATUS,
        "operational_eligible": False,
        "tickers": normalized,
    }


def _linear_quantile(sorted_values: list[float], percentile: int) -> float:
    """Return the linearly interpolated sample quantile (NumPy method='linear')."""

    if not sorted_values:
        raise ValueError("quantile requires at least one value")
    position = (len(sorted_values) - 1) * percentile / 100
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    weight = position - lower_index
    return sorted_values[lower_index] * (1 - weight) + sorted_values[upper_index] * weight


def _five_year_cutoff(as_of: str) -> str:
    value = date.fromisoformat(as_of)
    try:
        return value.replace(year=value.year - 5).isoformat()
    except ValueError:
        return value.replace(year=value.year - 5, day=28).isoformat()


def _per_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_values = [row.get("ttm_per", {}).get("value") for row in rows]
    finite_positive = [
        float(value)
        for value in raw_values
        if isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0
    ]
    invalid_count = len(raw_values) - len(finite_positive)
    ordered_valid = sorted(finite_positive)
    if len(ordered_valid) >= 4:
        q1 = _linear_quantile(ordered_valid, 25)
        q3 = _linear_quantile(ordered_valid, 75)
        iqr = q3 - q1
        lower_fence = max(0.0, q1 - 1.5 * iqr)
        upper_fence = q3 + 1.5 * iqr
        used = [value for value in ordered_valid if lower_fence <= value <= upper_fence]
    else:
        q1 = q3 = lower_fence = upper_fence = None
        used = ordered_valid
    outlier_count = len(ordered_valid) - len(used)
    percentiles = {
        f"P{percentile}": _linear_quantile(used, percentile)
        for percentile in SELF_HISTORY_PERCENTILES
    } if used else {}
    return {
        "sample_count_total": len(raw_values),
        "sample_count_finite_positive": len(ordered_valid),
        "sample_count_used": len(used),
        "excluded_count": invalid_count + outlier_count,
        "excluded_invalid_count": invalid_count,
        "excluded_outlier_count": outlier_count,
        "tukey_lower_fence": lower_fence,
        "tukey_upper_fence": upper_fence,
        "values_used": used,
        "percentiles": percentiles,
    }


def _price_candidate(
    eps: float | None,
    percentiles: dict[str, float],
    low_percentile: int,
    high_percentile: int,
    *,
    eligible: bool,
) -> dict[str, Any]:
    low_multiple = percentiles.get(f"P{low_percentile}")
    high_multiple = percentiles.get(f"P{high_percentile}")
    if not eligible or eps is None or not math.isfinite(eps) or eps <= 0:
        low = high = None
        status = "unavailable"
    elif low_multiple is None or high_multiple is None:
        low = high = None
        status = "unavailable"
    else:
        low = eps * low_multiple
        high = eps * high_multiple
        status = RESEARCH_STATUS
    return {
        "low": low,
        "high": high,
        "unit": "USD per share",
        "basis_percentiles": [f"P{low_percentile}", f"P{high_percentile}"],
        "status": status,
        "operational_eligible": False,
    }


def build_self_history_market_premium(
    v23_ticker: dict[str, Any],
    *,
    as_of: str | None = None,
    minimum_sample: int = SELF_HISTORY_MINIMUM_SAMPLE,
) -> dict[str, Any]:
    """Build a non-operational self-history PER read model.

    Only rows available on or before ``as_of`` participate.  DCF, discount
    rates, macro inputs, peers, and StockLens prices are intentionally absent
    from the function signature so they cannot influence the result.
    """

    ticker = str(v23_ticker.get("ticker") or "")
    current = _require_mapping(v23_ticker.get("current"), f"{ticker}.current")
    current_price = _require_mapping(current.get("price"), f"{ticker}.current.price")
    current_eps_field = _require_mapping(current.get("ttm_eps"), f"{ticker}.current.ttm_eps")
    current_per_field = _require_mapping(current.get("ttm_per"), f"{ticker}.current.ttm_per")
    calculation_as_of = _require_iso_date(as_of or current_price.get("as_of"), f"{ticker}.self_history.as_of")
    historical = v23_ticker.get("historical_per")
    if not isinstance(historical, list):
        raise V23LedgerError(f"{ticker}.historical_per must be an array")
    eligible_rows = [
        row
        for row in historical
        if isinstance(row, dict)
        and row.get("filing_date")
        and _require_iso_date(row["filing_date"], f"{ticker}.historical.filing_date") <= calculation_as_of
    ]
    full = _per_distribution(eligible_rows)
    cutoff = _five_year_cutoff(calculation_as_of)
    recent_rows = [row for row in eligible_rows if row["filing_date"] >= cutoff]
    recent = _per_distribution(recent_rows)
    sample_gate_passed = full["sample_count_used"] >= minimum_sample

    current_eps = current_eps_field.get("value")
    current_eps_number = float(current_eps) if isinstance(current_eps, (int, float)) else None
    current_per = current_per_field.get("value")
    current_per_number = float(current_per) if isinstance(current_per, (int, float)) else None
    current_percentile = None
    if sample_gate_passed and current_per_number is not None and math.isfinite(current_per_number):
        current_percentile = (
            sum(value <= current_per_number for value in full["values_used"])
            / len(full["values_used"])
            * 100
        )

    # Reproduce the v23 builder's disclosed current-percentile policy solely
    # for cross-checking: finite 0 < PER < 500, without Tukey filtering.
    v23_policy_values = sorted(
        value
        for value in (
            row.get("ttm_per", {}).get("value")
            for row in eligible_rows
        )
        if isinstance(value, (int, float)) and math.isfinite(float(value)) and 0 < float(value) < 500
    )
    recomputed_v23_percentile = None
    if current_per_number is not None and v23_policy_values:
        recomputed_v23_percentile = (
            sum(value <= current_per_number for value in v23_policy_values)
            / len(v23_policy_values)
            * 100
        )
    reported_field = current.get("reported_current_percentile", {})
    reported_percentile = reported_field.get("value") if isinstance(reported_field, dict) else None
    crosscheck_delta = None
    if isinstance(reported_percentile, (int, float)) and recomputed_v23_percentile is not None:
        crosscheck_delta = recomputed_v23_percentile - float(reported_percentile)

    percentiles = full["percentiles"]
    candidates = {
        "aggressive_interest_candidate": _price_candidate(
            current_eps_number, percentiles, 25, 35, eligible=sample_gate_passed
        ),
        "normal_interest_candidate": _price_candidate(
            current_eps_number, percentiles, 35, 50, eligible=sample_gate_passed
        ),
        "neutral_hold_candidate": _price_candidate(
            current_eps_number, percentiles, 50, 70, eligible=sample_gate_passed
        ),
        "high_expectation_candidate": _price_candidate(
            current_eps_number, percentiles, 70, 85, eligible=sample_gate_passed
        ),
    }
    current_location = "unavailable"
    if current_per_number is not None and sample_gate_passed:
        if current_per_number < percentiles["P25"]:
            current_location = "below_aggressive_interest_candidate"
        elif current_per_number <= percentiles["P35"]:
            current_location = "aggressive_interest_candidate"
        elif current_per_number <= percentiles["P50"]:
            current_location = "normal_interest_candidate"
        elif current_per_number <= percentiles["P70"]:
            current_location = "neutral_hold_candidate"
        elif current_per_number <= percentiles["P85"]:
            current_location = "high_expectation_candidate"
        else:
            current_location = "above_high_expectation_candidate"

    confidence = "insufficient_sample"
    if sample_gate_passed:
        confidence = "low_research_confidence" if full["sample_count_used"] < 30 else "moderate_research_confidence"
    available_at = max(
        str(current_price.get("available_at") or ""),
        str(current_eps_field.get("available_at") or ""),
    ) or None
    limitations = [
        "reconstructed PIT: filingDate is a date-only proxy because SEC acceptance timestamps are absent",
        "source payload hashes were collected in 2026 and are not contemporaneous historical vintages",
        "Tukey fences remove statistical extremes transparently but do not prove that the excluded observations were economically invalid",
        "PER dispersion can reflect EPS denominator volatility and business-structure change; the ledger alone cannot separate those causes",
        "recent-five-year distribution is displayed separately and is not promoted when its used sample is below 20",
        "candidate prices are historical-multiple observations, not formal fair value, target price, or an operating signal",
    ]
    if full["sample_count_used"] == minimum_sample:
        limitations.append("the full-history used sample only meets the minimum threshold exactly")

    result = {
        "source": "approved v23 historicalPer reconstructed point-in-time ledger",
        "source_hash": str(v23_ticker.get("ledger_sha256") or ""),
        "as_of": calculation_as_of,
        "available_at": available_at,
        "unit": "PER multiple; candidate prices in USD per share",
        "multiple_basis": "ttm_per",
        "eps_basis": "reported_ttm_unknown",
        "calculation_basis": (
            "linear-interpolated empirical PER quantiles after finite-positive validation and "
            "1.5×IQR Tukey filtering; candidate price = current v23 TTM diluted EPS × PER quantile"
        ),
        "status": RESEARCH_STATUS if sample_gate_passed else "blocked_insufficient_sample",
        "operational_eligible": False,
        "minimum_sample": minimum_sample,
        "sample_gate_passed": sample_gate_passed,
        "sample_confidence": confidence,
        "sample_count_total": full["sample_count_total"],
        "sample_count_used": full["sample_count_used"],
        "excluded_count": full["excluded_count"],
        "exclusion_policy": {
            "invalid": "exclude non-numeric, non-finite, zero, and negative PER",
            "outliers": "exclude values outside full-history Tukey fences Q1−1.5×IQR and Q3+1.5×IQR",
            "lower_fence": full["tukey_lower_fence"],
            "upper_fence": full["tukey_upper_fence"],
            "invalid_count": full["excluded_invalid_count"],
            "outlier_count": full["excluded_outlier_count"],
        },
        "full_history_percentiles": full["percentiles"],
        "recent_5y_percentiles": {
            "cutoff": cutoff,
            "sample_count_total": recent["sample_count_total"],
            "sample_count_used": recent["sample_count_used"],
            "excluded_count": recent["excluded_count"],
            "sample_gate_passed": recent["sample_count_used"] >= minimum_sample,
            "values": recent["percentiles"],
        },
        "current_ttm_eps": current_eps_number,
        "current_ttm_per": current_per_number,
        "current_percentile": current_percentile,
        "current_percentile_crosscheck": {
            "reported_v23": reported_percentile,
            "recomputed_v23_policy": recomputed_v23_percentile,
            "delta_percentage_points": crosscheck_delta,
            "policy": "finite 0 < PER < 500, matching the disclosed v23 current-percentile rule",
        },
        "current_location": current_location,
        **candidates,
        "limitations": limitations,
    }
    return result


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _json_hash(path: Path) -> str:
    return sha256_file(path) if path.is_file() else ""


def _stocklens_financial_fields(financial_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not financial_path.is_file():
        missing = _evidence(
            None,
            source="StockLens normalized official-financial snapshot",
            source_hash="",
            as_of=None,
            available_at=None,
            unit="USD per share",
            calculation_basis="no normalized financial snapshot is available",
            status="unavailable",
        )
        return missing, {**missing, "unit": "multiple"}
    financial_hash = _json_hash(financial_path)
    with financial_path.open("r", encoding="utf-8") as stream:
        financial = json.load(stream)
    periods = financial.get("periods", []) if isinstance(financial, dict) else []
    ttm = next((row for row in periods if isinstance(row, dict) and row.get("kind") == "ttm"), {})
    metrics = ttm.get("metrics", {}) if isinstance(ttm, dict) else {}
    eps = metrics.get("diluted_eps") if isinstance(metrics, dict) else None
    documents = financial.get("documents", []) if isinstance(financial, dict) else []
    published = [
        str(row.get("published_at"))
        for row in documents
        if isinstance(row, dict) and row.get("published_at")
    ]
    available_at = max(published) if published else financial.get("collected_at_utc")
    eps_field = _evidence(
        eps,
        source="StockLens normalized official-financial snapshot",
        source_hash=financial_hash,
        as_of=ttm.get("period_end") if isinstance(ttm, dict) else None,
        available_at=available_at,
        unit="USD diluted EPS per share",
        calculation_basis=(
            str(ttm.get("note")) if isinstance(ttm, dict) and ttm.get("note") else
            "normalized TTM diluted EPS; missing values remain null"
        ),
        status="research_only" if eps is not None else "unavailable",
    )
    per_field = _evidence(
        None,
        source="StockLens normalized official-financial snapshot + local market cache",
        source_hash=financial_hash,
        as_of=ttm.get("period_end") if isinstance(ttm, dict) else None,
        available_at=available_at,
        unit="multiple",
        calculation_basis="not calculated unless StockLens has a consistent TTM diluted EPS denominator",
        status="unavailable",
    )
    return eps_field, per_field


def compare_v23_with_stocklens(
    v23_ticker: dict[str, Any],
    *,
    market_path: str | Path,
    manifest_path: str | Path | None = None,
    financial_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compare definitions without averaging, overwriting, or aligning unlike dates."""

    ticker = str(v23_ticker.get("ticker") or "")
    current = _require_mapping(v23_ticker.get("current"), f"{ticker}.current")
    v23_price = _require_mapping(current.get("price"), f"{ticker}.current.price")
    target_date = str(v23_price.get("as_of") or "")
    market_file = Path(market_path)
    rows = _load_csv_rows(market_file)
    exact = next((row for row in rows if row.get("date") == target_date), None)
    latest = rows[-1] if rows else None
    selected = exact or latest

    manifest: dict[str, Any] = {}
    manifest_file = Path(manifest_path) if manifest_path is not None else None
    if manifest_file is not None and manifest_file.is_file():
        with manifest_file.open("r", encoding="utf-8") as stream:
            loaded = json.load(stream)
        if isinstance(loaded, dict):
            manifest = loaded
    market_hash = str(manifest.get("sha256") or (_json_hash(market_file) if market_file.is_file() else ""))
    selected_date = selected.get("date") if selected else None
    selected_close = None
    if selected and selected.get("close") not in (None, ""):
        selected_close = float(selected["close"])

    match_status = "unavailable"
    day_gap: int | None = None
    if selected_date:
        day_gap = abs((date.fromisoformat(selected_date) - date.fromisoformat(target_date)).days)
        match_status = "exact_date_match" if exact is not None else "date_mismatch"
    stocklens_price = _evidence(
        selected_close,
        source=f"StockLens local {manifest.get('provider', 'market')} cache",
        source_hash=market_hash,
        as_of=selected_date,
        available_at=str(manifest.get("collected_at_utc") or selected_date or "") or None,
        unit="USD per share",
        calculation_basis=str(manifest.get("price_policy") or "stored local close"),
        status=("research_only" if selected_close is not None else "unavailable"),
    )
    financial_file = Path(financial_path) if financial_path is not None else Path("__missing__")
    stocklens_eps, stocklens_per = _stocklens_financial_fields(financial_file)
    if exact is not None and stocklens_eps.get("value") not in (None, 0):
        stocklens_per = _evidence(
            selected_close / float(stocklens_eps["value"]),
            source="StockLens normalized official-financial snapshot + exact-date local market cache",
            source_hash=stocklens_eps["source_hash"],
            as_of=target_date,
            available_at=max(str(stocklens_eps.get("available_at") or ""), str(stocklens_price.get("available_at") or "")),
            unit="multiple",
            calculation_basis="exact-date close divided by StockLens TTM diluted EPS",
            status="research_only",
        )

    difference = None
    if exact is not None and selected_close is not None and isinstance(v23_price.get("value"), (int, float)):
        difference = selected_close - float(v23_price["value"])
    difference_field = _evidence(
        difference,
        source="StockLens-v23 exact-date cross-check",
        source_hash=market_hash or v23_price.get("source_hash", ""),
        as_of=target_date if exact is not None else None,
        available_at=stocklens_price.get("available_at") if exact is not None else None,
        unit="USD per share",
        calculation_basis=(
            "StockLens close minus v23 confirmed close on the exact same date"
            if exact is not None else
            "not calculated because the two sources do not share the comparison date"
        ),
        status="research_only" if exact is not None else "unavailable",
    )

    return {
        "ticker": ticker,
        "comparison_status": match_status,
        "date_gap_days": day_gap,
        "operational_eligible": False,
        "normal_interest_range": None,
        "stocklens": {
            "price": stocklens_price,
            "ttm_eps": stocklens_eps,
            "ttm_per": stocklens_per,
        },
        "v23": {
            "price": v23_price,
            "ttm_eps": current["ttm_eps"],
            "ttm_per": current["ttm_per"],
        },
        "exact_date_price_difference": difference_field,
    }


def validate_evidence_shape(value: dict[str, Any]) -> bool:
    """Return whether one normalized value carries the full evidence contract."""

    return REQUIRED_EVIDENCE_KEYS.issubset(value)
