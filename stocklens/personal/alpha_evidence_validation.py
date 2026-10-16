"""Structural validation for preserved Alpha Vantage response bytes.

This module validates provider envelopes only.  It does not normalize data,
create parser candidates, or perform investment calculations.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any


ERROR_ENVELOPE_KEYS = ("Note", "Information", "Error Message")
JSON_CONTENT_TYPES = {"application/json", "text/json"}
TERMS_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "application/pdf"}


def _media_type(value: str | None) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def validate_terms_bytes(payload: bytes, content_type: str | None) -> dict[str, Any]:
    media_type = _media_type(content_type)
    valid = bool(payload) and media_type in TERMS_CONTENT_TYPES
    return {
        "valid_provider_dataset": valid,
        "validation_code": "valid_terms_document" if valid else "invalid_terms_document",
        "content_media_type": media_type or None,
        "accepted_content_types": sorted(TERMS_CONTENT_TYPES),
    }


def _json_object(payload: bytes, content_type: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if _media_type(content_type) not in JSON_CONTENT_TYPES:
        return None, "invalid_json_content_type"
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid_json_body"
    if not isinstance(value, dict):
        return None, "invalid_json_root"
    for key in ERROR_ENVELOPE_KEYS:
        if key in value:
            return value, "provider_error_envelope"
    return value, None


def _declared_symbol(value: dict[str, Any]) -> str | None:
    direct = value.get("symbol") or value.get("Symbol")
    if isinstance(direct, str) and direct.strip():
        return direct.strip().upper()
    metadata = value.get("Meta Data")
    if isinstance(metadata, dict):
        for key in ("2. Symbol", "Symbol", "symbol"):
            candidate = metadata.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip().upper()
    return None


def _valid_ohlcv_row(day: Any, row: Any) -> bool:
    if not isinstance(day, str) or not isinstance(row, dict):
        return False
    try:
        date.fromisoformat(day)
    except ValueError:
        return False
    required = ("1. open", "2. high", "3. low", "4. close", "5. volume")
    try:
        values = [float(row[key]) for key in required]
    except (KeyError, TypeError, ValueError):
        return False
    open_, high, low, close, volume = values
    return min(open_, high, low, close) > 0 and high >= low and volume >= 0


def _estimate_rows(value: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    annual_keys = ("annualEarnings", "annualEstimates", "annual")
    quarterly_keys = ("quarterlyEarnings", "quarterlyEstimates", "quarterly")
    annual = next((value[key] for key in annual_keys if isinstance(value.get(key), list)), [])
    quarterly = next((value[key] for key in quarterly_keys if isinstance(value.get(key), list)), [])
    return (
        [row for row in annual if isinstance(row, dict)],
        [row for row in quarterly if isinstance(row, dict)],
    )


def _estimate_period_present(row: dict[str, Any]) -> bool:
    return any(
        isinstance(row.get(key), str) and bool(str(row.get(key)).strip())
        for key in ("fiscalDateEnding", "fiscal_date_ending", "period", "horizon")
    )


def _analyst_count_present(row: dict[str, Any]) -> bool:
    return any(
        row.get(key) not in (None, "")
        for key in (
            "numberAnalystsEstimatedEps", "numberAnalystsEstimatedRevenue",
            "analystCount", "analyst_count",
        )
    )


def _accounting_basis(value: dict[str, Any]) -> str:
    raw = value.get("accountingBasis") or value.get("accounting_basis") or value.get("epsBasis")
    normalized = str(raw or "").strip().upper().replace("-", "_")
    if normalized in {"GAAP", "NON_GAAP", "NONGAAP"}:
        return "NON_GAAP" if normalized in {"NON_GAAP", "NONGAAP"} else "GAAP"
    return "unknown"


def validate_dataset_bytes(
    payload: bytes,
    content_type: str | None,
    *,
    function: str,
    ticker: str,
) -> dict[str, Any]:
    value, error = _json_object(payload, content_type)
    base: dict[str, Any] = {
        "valid_provider_dataset": False,
        "validation_code": error,
        "accounting_basis": None,
        "analyst_count_present": None,
        "period_fields_present": None,
    }
    if error or value is None:
        return base
    symbol = _declared_symbol(value)
    if symbol != ticker.upper():
        return {**base, "validation_code": "ticker_mismatch", "declared_symbol": symbol}
    if function == "TIME_SERIES_DAILY":
        metadata = value.get("Meta Data")
        series = value.get("Time Series (Daily)")
        if not isinstance(metadata, dict) or not isinstance(series, dict) or not series:
            return {**base, "validation_code": "daily_structure_missing"}
        invalid_rows = sum(not _valid_ohlcv_row(day, row) for day, row in series.items())
        if invalid_rows:
            return {
                **base,
                "validation_code": "daily_ohlcv_invalid",
                "record_count": len(series),
                "invalid_record_count": invalid_rows,
            }
        return {
            **base,
            "valid_provider_dataset": True,
            "validation_code": "valid_unadjusted_daily",
            "record_count": len(series),
            "invalid_record_count": 0,
            "price_adjustment_policy": "as_traded_unadjusted",
        }
    if function == "EARNINGS_ESTIMATES":
        annual, quarterly = _estimate_rows(value)
        rows = [*annual, *quarterly]
        if not rows:
            return {**base, "validation_code": "estimate_periods_missing"}
        periods_present = all(_estimate_period_present(row) for row in rows)
        analysts_present = any(_analyst_count_present(row) for row in rows)
        basis = _accounting_basis(value)
        valid = periods_present
        return {
            **base,
            "valid_provider_dataset": valid,
            "validation_code": "valid_estimates_structure" if valid else "estimate_period_field_missing",
            "annual_period_count": len(annual),
            "quarterly_period_count": len(quarterly),
            "period_fields_present": periods_present,
            "analyst_count_present": analysts_present,
            "accounting_basis": basis,
            "parser_candidate_basis_eligible": basis in {"GAAP", "NON_GAAP"},
        }
    return {**base, "validation_code": "unsupported_function"}
