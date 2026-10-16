"""Structural validation for preserved Alpha Vantage response bytes.

This module validates provider envelopes only.  It does not normalize data,
create parser candidates, or perform investment calculations.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError


ERROR_ENVELOPE_KEYS = ("Note", "Information", "Error Message")
JSON_CONTENT_TYPES = {"application/json", "text/json"}
TERMS_CONTENT_TYPES = {"application/pdf"}
PROVIDER_RESTRICTION_RULE_VERSION = "alpha-vantage-error-semantics-v1"


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _matching_pages(texts: list[str], predicate: Any) -> list[int]:
    return [index + 1 for index, text in enumerate(texts) if predicate(text)]


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
            lambda text: "personal" in text and "non-commercial use" in text,
        ),
        "termination": _matching_pages(
            texts,
            lambda text: "termination" in text or "terminate" in text,
        ),
        "assignment_or_transfer": _matching_pages(
            texts,
            lambda text: "assign" in text or "transfer" in text,
        ),
        "use_restrictions": _matching_pages(
            texts,
            lambda text: "restriction" in text or "prohibit" in text or "may not" in text,
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
