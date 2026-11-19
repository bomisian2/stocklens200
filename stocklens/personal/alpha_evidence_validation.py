"""Structural validation for preserved Alpha Vantage response bytes.

This module validates provider envelopes only.  It does not normalize data,
create parser candidates, or perform investment calculations.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError


ERROR_ENVELOPE_KEYS = ("Note", "Information", "Error Message")
JSON_CONTENT_TYPES = {"application/json", "text/json"}
TERMS_CONTENT_TYPES = {"application/pdf"}
PROVIDER_RESTRICTION_RULE_VERSION = "alpha-vantage-error-semantics-v1"
TERMS_CLAUSE_DETECTION_VERSION = "alpha-vantage-terms-heading-context-v1"
ESTIMATES_ARRAY_SCHEMA_VERSION = "alpha-vantage-estimates-array-v1"
LEGACY_ESTIMATES_SCHEMA_VERSION = "alpha-vantage-estimates-legacy-v1"

_LEGACY_ESTIMATE_KEYS = frozenset({
    "annualEarnings", "annualEstimates", "annual",
    "quarterlyEarnings", "quarterlyEstimates", "quarterly",
})
_ESTIMATES_ARRAY_REQUIRED_NUMERIC_FIELDS = frozenset({
    "eps_estimate_average", "eps_estimate_high", "eps_estimate_low",
    "revenue_estimate_average", "revenue_estimate_high", "revenue_estimate_low",
})
_ESTIMATES_ARRAY_REQUIRED_COUNT_FIELDS = frozenset({
    "eps_estimate_analyst_count", "revenue_estimate_analyst_count",
})
_ESTIMATES_ARRAY_OPTIONAL_NUMERIC_FIELDS = frozenset({
    "eps_estimate_average_7_days_ago",
    "eps_estimate_average_30_days_ago",
    "eps_estimate_average_60_days_ago",
    "eps_estimate_average_90_days_ago",
})
_ESTIMATES_ARRAY_OPTIONAL_COUNT_FIELDS = frozenset({
    "eps_estimate_revision_up_trailing_7_days",
    "eps_estimate_revision_down_trailing_7_days",
    "eps_estimate_revision_up_trailing_30_days",
    "eps_estimate_revision_down_trailing_30_days",
})
_ESTIMATES_ARRAY_ALLOWED_FIELDS = frozenset({"date", "horizon"}).union(
    _ESTIMATES_ARRAY_REQUIRED_NUMERIC_FIELDS,
    _ESTIMATES_ARRAY_REQUIRED_COUNT_FIELDS,
    _ESTIMATES_ARRAY_OPTIONAL_NUMERIC_FIELDS,
    _ESTIMATES_ARRAY_OPTIONAL_COUNT_FIELDS,
)
_ESTIMATES_ARRAY_HORIZONS = frozenset({"fiscal quarter", "fiscal year"})


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _matching_pages(texts: list[str], predicate: Any) -> list[int]:
    return [index + 1 for index, text in enumerate(texts) if predicate(text)]


def _section_heading(text: str, title: str) -> bool:
    """Match a numbered terms section title, not incidental disclaimer prose."""

    escaped = re.escape(title.lower())
    return bool(re.search(rf"(?:^|\s)\d+\.\s*{escaped}\.(?:\s|$)", text))


def inspect_terms_pdf(payload: bytes) -> dict[str, Any]:
    """Return non-verbatim structural and clause evidence for one terms PDF."""

    try:
        reader = PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted or not reader.pages:
            raise PdfReadError("encrypted or empty PDF")
        extracted = [page.extract_text() for page in reader.pages]
    except (PdfReadError, OSError, TypeError, ValueError):
        return {
            "pdf_structure_valid": False,
            "page_count": 0,
            "page_text_sha256": [],
            "text_extraction_complete": False,
            "document_title": None,
            "document_title_verified": False,
            "provider_identifier_verified": False,
            "provider_identifier_pages": [],
            "clause_detection_version": TERMS_CLAUSE_DETECTION_VERSION,
            "clauses": {},
        }

    text_extraction_complete = all(isinstance(text, str) and bool(text.strip()) for text in extracted)
    texts = [_normalized_text(text or "") for text in extracted]
    first_lines = [
        line.strip()
        for line in (extracted[0] or "").splitlines()
        if line.strip()
    ]
    title = first_lines[0] if first_lines else None
    title_verified = bool(title and _normalized_text(title) == "terms of service")
    provider_pages = _matching_pages(texts, lambda text: "alpha vantage" in text)
    clause_pages = {
        "personal_noncommercial_use": _matching_pages(
            texts,
            lambda text: (
                _section_heading(text, "grant of license")
                and bool(re.search(r"personal,?\s+non-commercial\s+use", text))
            ),
        ),
        "termination": _matching_pages(
            texts, lambda text: _section_heading(text, "termination"),
        ),
        "assignment_or_transfer": _matching_pages(
            texts, lambda text: _section_heading(text, "assignment"),
        ),
        "use_restrictions": _matching_pages(
            texts, lambda text: _section_heading(text, "use restrictions"),
        ),
    }
    clauses = {
        clause_id: {"status": "found" if pages else "missing", "pages": pages}
        for clause_id, pages in clause_pages.items()
    }
    return {
        "pdf_structure_valid": True,
        "page_count": len(reader.pages),
        "page_text_sha256": [
            hashlib.sha256((text or "").encode("utf-8")).hexdigest().upper()
            for text in extracted
        ],
        "text_extraction_complete": text_extraction_complete,
        "document_title": title,
        "document_title_verified": title_verified,
        "provider_identifier_verified": bool(provider_pages),
        "provider_identifier_pages": provider_pages,
        "clause_detection_version": TERMS_CLAUSE_DETECTION_VERSION,
        "clauses": clauses,
    }


def classify_provider_error_envelope(value: dict[str, Any]) -> dict[str, str]:
    """Classify an error envelope without returning or persisting provider text."""

    texts = [
        _normalized_text(raw)
        for key in ERROR_ENVELOPE_KEYS
        if isinstance((raw := value.get(key)), str) and raw.strip()
    ]
    combined = " ".join(texts)
    classification = "provider_restriction_unknown"
    if re.search(
        r"invalid\s+(api\s*key|apikey)|(?:api\s*key|apikey)\s+(?:is\s+)?invalid|"
        r"missing\s+(?:api\s*key|apikey)|provide\s+(?:a\s+)?valid\s+(?:api\s*key|apikey)",
        combined,
    ):
        classification = "provider_authentication_error"
    elif re.search(
        r"invalid\s+(?:api\s+call|symbol|parameter|function)|"
        r"unknown\s+(?:symbol|parameter|function)|"
        r"(?:symbol|parameter|function)\s+(?:is\s+)?(?:invalid|not\s+found)",
        combined,
    ):
        classification = "provider_request_error"
    elif re.search(
        r"rate\s+limit|call\s+frequency|calls?\s+per\s+(?:minute|day)|"
        r"requests?\s+per\s+(?:minute|day)|daily\s+(?:request\s+)?limit|"
        r"api\s+call\s+volume|premium|subscription|subscribe",
        combined,
    ):
        classification = "provider_rate_or_plan_limit"
    return {
        "provider_restriction_class": classification,
        "provider_restriction_rule_version": PROVIDER_RESTRICTION_RULE_VERSION,
    }


def _media_type(value: str | None) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def validate_terms_bytes(payload: bytes, content_type: str | None) -> dict[str, Any]:
    media_type = _media_type(content_type)
    inspection = inspect_terms_pdf(payload) if media_type == "application/pdf" else None
    required_clauses = (
        "personal_noncommercial_use", "termination",
        "assignment_or_transfer", "use_restrictions",
    )
    valid_pdf = bool(
        inspection
        and inspection["pdf_structure_valid"]
        and inspection["text_extraction_complete"]
        and inspection["document_title_verified"]
        and inspection["provider_identifier_verified"]
        and all(
            (inspection["clauses"].get(clause_id) or {}).get("status") == "found"
            for clause_id in required_clauses
        )
    )
    valid = bool(payload) and media_type in TERMS_CONTENT_TYPES and valid_pdf
    return {
        "valid_provider_dataset": valid,
        "validation_code": "valid_terms_document" if valid else "invalid_terms_document",
        "content_media_type": media_type or None,
        "accepted_content_types": sorted(TERMS_CONTENT_TYPES),
        "terms_inspection": inspection,
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


def _exact_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _available_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).date()


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed)


def _finite_decimal(value: Any) -> Decimal | None:
    """Return an exact finite number while rejecting bool and null values."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _nonnegative_integer(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return False
    return bool(
        parsed.is_finite()
        and parsed >= 0
        and parsed == parsed.to_integral_value()
    )


def _validate_estimates_array(
    value: dict[str, Any], *, available_at: str | None,
) -> dict[str, Any]:
    """Validate the current provider shape without granting accounting basis."""

    if any(key in value for key in _LEGACY_ESTIMATE_KEYS):
        return {"validation_code": "estimate_schema_ambiguous"}
    rows = value.get("estimates")
    if not isinstance(rows, list):
        return {"validation_code": "estimate_array_invalid"}
    if not rows:
        return {"validation_code": "estimate_periods_missing"}

    parsed_dates: list[date] = []
    horizons: list[str] = []
    period_keys: set[tuple[date, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            return {"validation_code": "estimate_row_not_object"}
        unexpected = set(row).difference(_ESTIMATES_ARRAY_ALLOWED_FIELDS)
        if unexpected:
            return {
                "validation_code": "estimate_field_not_allowed",
                "unexpected_field_count": len(unexpected),
            }
        period_date = _exact_iso_date(row.get("date"))
        if period_date is None:
            return {"validation_code": "estimate_date_invalid"}
        horizon = row.get("horizon")
        if horizon not in _ESTIMATES_ARRAY_HORIZONS:
            return {"validation_code": "estimate_horizon_invalid"}
        period_key = (period_date, str(horizon))
        if period_key in period_keys:
            return {"validation_code": "estimate_period_duplicate"}
        period_keys.add(period_key)
        missing = [
            field for field in (
                _ESTIMATES_ARRAY_REQUIRED_NUMERIC_FIELDS
                | _ESTIMATES_ARRAY_REQUIRED_COUNT_FIELDS
            )
            if field not in row or row.get(field) is None
        ]
        if missing:
            return {
                "validation_code": "estimate_required_field_missing",
                "missing_field_count": len(missing),
            }
        if any(
            not _finite_number(row.get(field))
            for field in _ESTIMATES_ARRAY_REQUIRED_NUMERIC_FIELDS
        ):
            return {"validation_code": "estimate_numeric_invalid"}
        for prefix, failure_code in (
            ("eps", "estimate_eps_range_invalid"),
            ("revenue", "estimate_revenue_range_invalid"),
        ):
            high = _finite_decimal(row.get(f"{prefix}_estimate_high"))
            average = _finite_decimal(row.get(f"{prefix}_estimate_average"))
            low = _finite_decimal(row.get(f"{prefix}_estimate_low"))
            if high is None or average is None or low is None:
                return {"validation_code": "estimate_numeric_invalid"}
            if not high >= average >= low:
                return {"validation_code": failure_code}
        if any(
            row.get(field) is not None and not _finite_number(row.get(field))
            for field in _ESTIMATES_ARRAY_OPTIONAL_NUMERIC_FIELDS
        ):
            return {"validation_code": "estimate_optional_numeric_invalid"}
        if any(
            not _nonnegative_integer(row.get(field))
            for field in _ESTIMATES_ARRAY_REQUIRED_COUNT_FIELDS
        ):
            return {"validation_code": "estimate_analyst_count_invalid"}
        if any(
            row.get(field) is not None and not _nonnegative_integer(row.get(field))
            for field in _ESTIMATES_ARRAY_OPTIONAL_COUNT_FIELDS
        ):
            return {"validation_code": "estimate_revision_count_invalid"}
        parsed_dates.append(period_date)
        horizons.append(str(horizon))

    captured_date = _available_date(available_at)
    if available_at is not None and captured_date is None:
        return {"validation_code": "estimate_available_at_invalid"}
    forward_count = (
        sum(period > captured_date for period in parsed_dates)
        if captured_date is not None else None
    )
    return {
        "valid_provider_dataset": True,
        "validation_code": "valid_estimates_structure",
        "provider_schema_version": ESTIMATES_ARRAY_SCHEMA_VERSION,
        "record_count": len(rows),
        "fiscal_quarter_count": horizons.count("fiscal quarter"),
        "fiscal_year_count": horizons.count("fiscal year"),
        "period_fields_present": True,
        "analyst_count_present": True,
        "accounting_basis": "unknown",
        "parser_candidate_basis_eligible": False,
        "parser_candidate": False,
        "historical_availability_verified": False,
        "historical_record_classification": "current_snapshot_records_for_past_periods",
        "record_available_at": available_at,
        "forward_period_candidate_count": forward_count,
        "fy1_ntm_mapping_eligible": False,
        "decision_created": False,
        "operational_eligible": False,
    }


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
    available_at: str | None = None,
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
        if error == "provider_error_envelope" and value is not None:
            return {**base, **classify_provider_error_envelope(value)}
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
        if "estimates" in value:
            modern = _validate_estimates_array(value, available_at=available_at)
            return {**base, **modern}
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
            "provider_schema_version": LEGACY_ESTIMATES_SCHEMA_VERSION,
            "annual_period_count": len(annual),
            "quarterly_period_count": len(quarterly),
            "period_fields_present": periods_present,
            "analyst_count_present": analysts_present,
            "accounting_basis": basis,
            "parser_candidate_basis_eligible": basis in {"GAAP", "NON_GAAP"},
            "parser_candidate": False,
            "historical_availability_verified": False,
            "fy1_ntm_mapping_eligible": False,
            "decision_created": False,
            "operational_eligible": False,
        }
    return {**base, "validation_code": "unsupported_function"}
