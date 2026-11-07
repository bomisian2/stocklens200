"""Offline adjudication of an immutable Alpha Vantage evidence run.

The adjudication never alters provider bytes, capture manifests, or the run
ledger.  It records a second-layer QA classification without copying provider
error text and without promoting any artifact into a parser candidate.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .alpha_evidence_validation import (
    PROVIDER_RESTRICTION_RULE_VERSION,
    inspect_terms_pdf,
    validate_dataset_bytes,
    validate_terms_bytes,
)
from .evidence_inventory import EvidenceInventoryError, resolve_repository_private_data


ADJUDICATION_VERSION = "alpha-vantage-collection-adjudication-p4.3.1-v1"
TERMS_REVIEW_VERSION = "alpha-vantage-terms-review-p4.3.1-v1"
OUTPUT_VERSION_TAG = "p4.3.1"
APPROVED_REQUESTS = {
    "alpha-vantage-terms": ("terms-of-service.response.raw", "captured_valid"),
    "avgo-daily": ("AVGO_TIME_SERIES_DAILY.response.raw", "captured_unadjusted_daily"),
    "nvda-daily": ("NVDA_TIME_SERIES_DAILY.response.raw", "invalid_provider_dataset"),
    "avgo-estimates": ("AVGO_EARNINGS_ESTIMATES.response.raw", "invalid_provider_dataset"),
    "nvda-estimates": ("NVDA_EARNINGS_ESTIMATES.response.raw", "invalid_provider_dataset"),
}
APPROVED_REQUEST_ORDER = tuple(APPROVED_REQUESTS)
APPROVED_REQUEST_METADATA = {
    "alpha-vantage-terms": (None, None, "/terms_of_service/"),
    "avgo-daily": ("TIME_SERIES_DAILY", "AVGO", "/query"),
    "nvda-daily": ("TIME_SERIES_DAILY", "NVDA", "/query"),
    "avgo-estimates": ("EARNINGS_ESTIMATES", "AVGO", "/query"),
    "nvda-estimates": ("EARNINGS_ESTIMATES", "NVDA", "/query"),
}
APPROVED_PROVIDER_HOST = "www.alphavantage.co"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceInventoryError(f"invalid adjudication input: {path.name}") from exc
    if not isinstance(value, dict):
        raise EvidenceInventoryError(f"adjudication input root must be an object: {path.name}")
    return value


def _iso_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceInventoryError("generated_at must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceInventoryError("generated_at must be a timezone-aware ISO timestamp")
    return parsed.isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceInventoryError(f"{field} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceInventoryError(f"{field} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceInventoryError(f"{field} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def _validate_safe_provider_location(value: dict[str, Any], expected_path: str) -> None:
    if value.get("request_host") != APPROVED_PROVIDER_HOST or value.get("request_path") != expected_path:
        raise EvidenceInventoryError("capture manifest provider host/path mismatch")
    parsed = urlsplit(str(value.get("final_url") or ""))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != APPROVED_PROVIDER_HOST
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise EvidenceInventoryError("capture manifest final URL is not the approved query-free endpoint")
    if value.get("request_query_persisted") is not False or value.get("api_key_persisted") is not False:
        raise EvidenceInventoryError("capture manifest did not prove query and API key removal")
    redirects = value.get("redirect_chain")
    if not isinstance(redirects, list):
        raise EvidenceInventoryError("capture manifest redirect chain must be a list")
    for redirect in redirects:
        if not isinstance(redirect, dict):
            raise EvidenceInventoryError("capture manifest redirect entry must be an object")
        redirect_host = str(redirect.get("host") or redirect.get("request_host") or "").lower()
        redirect_path = str(redirect.get("path") or redirect.get("request_path") or "")
        if redirect_host != APPROVED_PROVIDER_HOST or redirect_path not in {"/query", "/terms_of_service/"}:
            raise EvidenceInventoryError("capture manifest contains an unapproved redirect")
        if redirect.get("query") not in (None, ""):
            raise EvidenceInventoryError("capture manifest redirect persisted a query")


def _validate_collection_ledger(
    ledger: dict[str, Any], *, run_id: str, generated_at: str,
) -> list[dict[str, Any]]:
    if ledger.get("run_id") != run_id:
        raise EvidenceInventoryError("collection ledger run_id mismatch")
    expected_scalars = {
        "calls_attempted": 5,
        "calls_completed": 5,
        "automatic_retries": 0,
        "run_state": "failed",
        "failure_code": "data_collection_incomplete",
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    for field, expected in expected_scalars.items():
        if ledger.get(field) != expected:
            raise EvidenceInventoryError(f"collection ledger {field} contract mismatch")
    results = ledger.get("results")
    if not isinstance(results, list) or len(results) != len(APPROVED_REQUEST_ORDER):
        raise EvidenceInventoryError("collection ledger must contain exactly five results")
    if not all(isinstance(row, dict) for row in results):
        raise EvidenceInventoryError("collection ledger results must be objects")
    request_ids = [str(row.get("request_id") or "") for row in results]
    if len(set(request_ids)) != len(request_ids):
        raise EvidenceInventoryError("collection ledger contains duplicate request IDs")
    if request_ids != list(APPROVED_REQUEST_ORDER):
        raise EvidenceInventoryError("collection ledger request order does not match approval")

    started = _timestamp(ledger.get("started_at"), "ledger.started_at")
    finished = _timestamp(ledger.get("finished_at"), "ledger.finished_at")
    generated = _timestamp(generated_at, "generated_at")
    previous_completed: datetime | None = None
    for result in results:
        request_id = str(result["request_id"])
        expected_function, expected_ticker, _ = APPROVED_REQUEST_METADATA[request_id]
        if result.get("function") != expected_function or result.get("ticker") != expected_ticker:
            raise EvidenceInventoryError(f"collection ledger function/ticker mismatch for {request_id}")
        request_started = _timestamp(result.get("request_started_at"), f"{request_id}.request_started_at")
        headers_received = _timestamp(result.get("headers_received_at"), f"{request_id}.headers_received_at")
        response_completed = _timestamp(result.get("response_completed_at"), f"{request_id}.response_completed_at")
        if not started <= request_started <= headers_received <= response_completed <= finished:
            raise EvidenceInventoryError(f"collection ledger timestamp order mismatch for {request_id}")
        if previous_completed is not None and request_started < previous_completed:
            raise EvidenceInventoryError("collection ledger approved requests overlap or are out of order")
        previous_completed = response_completed
    if generated < finished or (previous_completed is not None and generated < previous_completed):
        raise EvidenceInventoryError("generated_at precedes collection completion")
    return results


def _validate_capture_contract(
    *, result: dict[str, Any], capture: dict[str, Any], raw_name: str,
) -> None:
    request_id = str(result["request_id"])
    expected_function, expected_ticker, expected_path = APPROVED_REQUEST_METADATA[request_id]
    comparable_fields = (
        "request_id", "function", "ticker", "content_type", "byte_length",
        "response_sha256", "http_status", "request_started_at",
        "headers_received_at", "response_completed_at", "capture_state",
        "validation_code",
    )
    for field in comparable_fields:
        if result.get(field) != capture.get(field):
            raise EvidenceInventoryError(f"ledger/capture {field} mismatch for {request_id}")
    if result.get("response_file") != raw_name:
        raise EvidenceInventoryError(f"collection ledger response file mismatch for {request_id}")
    if capture.get("function") != expected_function or capture.get("ticker") != expected_ticker:
        raise EvidenceInventoryError(f"capture function/ticker mismatch for {request_id}")
    request_started = _timestamp(capture.get("request_started_at"), f"{request_id}.capture.request_started_at")
    headers_received = _timestamp(capture.get("headers_received_at"), f"{request_id}.capture.headers_received_at")
    response_completed = _timestamp(capture.get("response_completed_at"), f"{request_id}.capture.response_completed_at")
    if not request_started <= headers_received <= response_completed:
        raise EvidenceInventoryError(f"capture timestamp order mismatch for {request_id}")
    if capture.get("timestamp_order_valid") is not True:
        raise EvidenceInventoryError(f"capture timestamp flag is not valid for {request_id}")
    if _timestamp(capture.get("available_at"), f"{request_id}.capture.available_at") != response_completed:
        raise EvidenceInventoryError(f"capture availability mismatch for {request_id}")
    _validate_safe_provider_location(capture, expected_path)


def _safe_run_root(private_root: Path, run_id: str) -> Path:
    if not run_id or any(value in run_id for value in ("/", "\\", "..", ":")):
        raise EvidenceInventoryError("run_id must be a simple directory name")
    root = private_root.resolve(strict=True)
    run_root = private_root / "raw" / "alpha_vantage" / run_id
    resolved = run_root.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EvidenceInventoryError("Alpha Vantage run resolves outside private_data") from exc
    return run_root


def _write_immutable_json(path: Path, value: dict[str, Any]) -> Path:
    root = path.parents[1].resolve(strict=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = path.parent.resolve(strict=True)
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise EvidenceInventoryError("adjudication output resolves outside private_data") from exc
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise EvidenceInventoryError(f"immutable adjudication output already exists: {path.name}") from exc
    return path


def build_terms_review_packet(
    *, run_id: str, generated_at: str, terms_path: Path, capture_path: Path,
) -> dict[str, Any]:
    """Build a page-grounded review packet without granting usage rights."""

    inspection = inspect_terms_pdf(terms_path.read_bytes())
    required_clause_ids = (
        "personal_noncommercial_use", "termination",
        "assignment_or_transfer", "use_restrictions",
    )
    if not (
        inspection["pdf_structure_valid"]
        and inspection["text_extraction_complete"]
        and inspection["document_title_verified"]
        and inspection["provider_identifier_verified"]
        and all(
            (inspection["clauses"].get(clause_id) or {}).get("status") == "found"
            for clause_id in required_clause_ids
        )
    ):
        raise EvidenceInventoryError("terms PDF failed structural or semantic validation")
    clauses = inspection["clauses"]
    personal_pages = clauses["personal_noncommercial_use"]["pages"]
    restriction_pages = sorted({
        page
        for clause_id in ("assignment_or_transfer", "use_restrictions")
        for page in clauses[clause_id]["pages"]
    })
    termination_pages = clauses["termination"]["pages"]
    return {
        "review_version": TERMS_REVIEW_VERSION,
        "provider": "Alpha Vantage",
        "run_id": run_id,
        "generated_at": _iso_timestamp(generated_at),
        "clause_detection_version": inspection["clause_detection_version"],
        "terms_document": {
            "relative_path": f"raw/alpha_vantage/{run_id}/{terms_path.name}",
            "pdf_sha256": _sha256(terms_path),
            "capture_manifest_hash": _sha256(capture_path),
            "page_count": inspection["page_count"],
            "page_text_sha256": inspection["page_text_sha256"],
            "document_title": inspection["document_title"],
            "document_title_verified": inspection["document_title_verified"],
            "provider_identifier_verified": inspection["provider_identifier_verified"],
            "provider_identifier_pages": inspection["provider_identifier_pages"],
        },
        "clauses": clauses,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_status": "pending_independent_qa",
        "scope_assessments": {
            "personal_noncommercial_use": {
                "status": "text_supports_limited_license",
                "clause_ids": ["personal_noncommercial_use"],
                "pages": personal_pages,
            },
            "local_storage": {
                "status": "manual_review_required",
                "clause_ids": ["use_restrictions", "termination"],
                "pages": sorted(set(restriction_pages + termination_pages)),
            },
            "derived_analysis": {
                "status": "manual_review_required",
                "clause_ids": ["personal_noncommercial_use", "use_restrictions"],
                "pages": sorted(set(personal_pages + restriction_pages)),
            },
            "local_display": {
                "status": "manual_review_required",
                "clause_ids": ["personal_noncommercial_use", "use_restrictions"],
                "pages": sorted(set(personal_pages + restriction_pages)),
            },
            "public_display": {
                "status": "not_authorized",
                "clause_ids": ["use_restrictions", "assignment_or_transfer"],
                "pages": restriction_pages,
            },
            "redistribution": {
                "status": "not_authorized",
                "clause_ids": ["assignment_or_transfer", "use_restrictions"],
                "pages": restriction_pages,
            },
            "retention_and_deletion": {
                "status": "manual_review_required",
                "clause_ids": ["termination"],
                "pages": termination_pages,
            },
            "attribution": {
                "status": "manual_review_required",
                "clause_ids": ["use_restrictions"],
                "pages": clauses["use_restrictions"]["pages"],
            },
        },
        "public_display_permission": "not_authorized",
        "parser_candidate": False,
        "operational_eligible": False,
    }


def adjudicate_alpha_run(
    *, project_root: str | Path, run_id: str, generated_at: str,
) -> dict[str, Any]:
    """Create immutable local adjudication and terms-review files for one run."""

    private_root = resolve_repository_private_data(project_root)
    run_root = _safe_run_root(private_root, run_id)
    ledger_path = run_root / "collection-run.json"
    ledger = _read_json(ledger_path)
    results = _validate_collection_ledger(ledger, run_id=run_id, generated_at=generated_at)

    requests: list[dict[str, Any]] = []
    for result in results:
        request_id = str(result["request_id"])
        raw_name, adjudication = APPROVED_REQUESTS[request_id]
        raw_path = run_root / raw_name
        capture_path = run_root / f"{raw_name}.manifest.json"
        capture = _read_json(capture_path)
        _validate_capture_contract(result=result, capture=capture, raw_name=raw_name)
        raw_hash = _sha256(raw_path)
        if raw_hash != str(result.get("response_sha256") or "").upper():
            raise EvidenceInventoryError(f"raw SHA-256 mismatch for {request_id}")
        if raw_hash != str(capture.get("response_sha256") or "").upper():
            raise EvidenceInventoryError(f"capture SHA-256 mismatch for {request_id}")
        if raw_path.stat().st_size != result.get("byte_length") or raw_path.stat().st_size != capture.get("byte_length"):
            raise EvidenceInventoryError(f"raw byte length mismatch for {request_id}")
        if request_id == "alpha-vantage-terms":
            validation = validate_terms_bytes(raw_path.read_bytes(), str(capture.get("content_type") or ""))
        else:
            validation = validate_dataset_bytes(
                raw_path.read_bytes(), str(capture.get("content_type") or ""),
                function=str(result.get("function")), ticker=str(result.get("ticker")),
            )
        if adjudication == "invalid_provider_dataset":
            if validation.get("validation_code") != "provider_error_envelope":
                raise EvidenceInventoryError(f"expected provider error envelope for {request_id}")
            if validation.get("provider_restriction_class") not in {
                "provider_rate_or_plan_limit", "provider_authentication_error",
                "provider_request_error", "provider_restriction_unknown",
            }:
                raise EvidenceInventoryError(f"provider restriction classification missing for {request_id}")
        elif validation.get("valid_provider_dataset") is not True:
            raise EvidenceInventoryError(f"expected valid provider artifact for {request_id}")

        row: dict[str, Any] = {
            "request_id": request_id,
            "ticker": result.get("ticker"),
            "function": result.get("function"),
            "source_relative_path": f"raw/alpha_vantage/{run_id}/{raw_name}",
            "source_hash": raw_hash,
            "source_bytes": raw_path.stat().st_size,
            "capture_manifest_relative_path": f"raw/alpha_vantage/{run_id}/{capture_path.name}",
            "capture_manifest_hash": _sha256(capture_path),
            "adjudication": adjudication,
            "validation_code": validation.get("validation_code"),
            "parser_candidate": False,
            "operational_eligible": False,
        }
        if adjudication == "invalid_provider_dataset":
            row.update({
                "restriction_class": validation["provider_restriction_class"],
                "restriction_rule_version": validation["provider_restriction_rule_version"],
                "error_message_persisted": False,
                "counts_toward_same_date_required_raw": False,
            })
        elif request_id == "avgo-daily":
            payload = _read_json(raw_path)
            series = payload.get("Time Series (Daily)")
            dates = sorted(series) if isinstance(series, dict) else []
            row.update({
                "output_size": "compact",
                "record_count": len(dates),
                "first_market_date": dates[0] if dates else None,
                "last_market_date": dates[-1] if dates else None,
                "price_adjustment_policy": "as_traded_unadjusted",
                "allowed_uses_before_rights_review": [],
                "prohibited_uses": [
                    "split_or_dividend_adjusted_returns",
                    "long_horizon_backtest",
                    "forward_per_ledger",
                    "investment_decision",
                ],
                "counts_toward_same_date_required_raw": True,
            })
        else:
            row["counts_toward_same_date_required_raw"] = False
        requests.append(row)

    terms_path = run_root / "terms-of-service.response.raw"
    terms_capture = run_root / "terms-of-service.response.raw.manifest.json"
    packet = build_terms_review_packet(
        run_id=run_id, generated_at=generated_at,
        terms_path=terms_path, capture_path=terms_capture,
    )
    decision_root = private_root / "decision"
    terms_output = decision_root / f"alpha-vantage-terms-review-{OUTPUT_VERSION_TAG}-{run_id}.json"
    adjudication_output = decision_root / f"alpha-vantage-adjudication-{OUTPUT_VERSION_TAG}-{run_id}.json"
    if terms_output.exists() or adjudication_output.exists():
        raise EvidenceInventoryError("immutable P4.3 output already exists")
    _write_immutable_json(terms_output, packet)
    adjudication_value = {
        "adjudication_version": ADJUDICATION_VERSION,
        "provider": "Alpha Vantage",
        "run_id": run_id,
        "generated_at": _iso_timestamp(generated_at),
        "collection_run": {
            "relative_path": f"raw/alpha_vantage/{run_id}/collection-run.json",
            "source_hash": _sha256(ledger_path),
        },
        "terms_review": {
            "relative_path": f"decision/{terms_output.name}",
            "source_hash": _sha256(terms_output),
            "review_status": "pending_independent_qa",
        },
        "run_state": ledger.get("run_state"),
        "failure_code": ledger.get("failure_code"),
        "restriction_rule_version": PROVIDER_RESTRICTION_RULE_VERSION,
        "restriction_classes": sorted({
            row["restriction_class"] for row in requests if "restriction_class" in row
        }),
        "requests": requests,
        "raw_provider_error_text_persisted": False,
        "parser_candidate_count": 0,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    try:
        _write_immutable_json(adjudication_output, adjudication_value)
    except Exception:
        terms_output.unlink(missing_ok=True)
        raise
    return {
        "adjudication_path": adjudication_output,
        "adjudication_sha256": _sha256(adjudication_output),
        "terms_review_path": terms_output,
        "terms_review_sha256": _sha256(terms_output),
        "collection_run_sha256": _sha256(ledger_path),
        "requests": requests,
    }
