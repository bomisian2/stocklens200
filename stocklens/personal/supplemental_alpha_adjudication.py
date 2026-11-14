"""Offline P5.0.2 adjudication for an immutable supplemental provider run.

The module revalidates preserved bytes without changing the raw response,
capture manifest, or collection ledger.  Structural validity never grants an
accounting basis, point-in-time history, parser candidacy, or investment use.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .alpha_evidence_validation import (
    ESTIMATES_ARRAY_SCHEMA_VERSION,
    validate_dataset_bytes,
)
from .evidence_inventory import EvidenceInventoryError, resolve_repository_private_data
from .offline_alpha_collector import (
    _create_json_exclusive,
    _safe_mkdir,
    _validate_output_path,
)


ADJUDICATION_VERSION = "alpha-vantage-supplemental-adjudication-p5.0.2-v1"
VALIDATOR_VERSION = "alpha-vantage-provider-validator-p5.0.2-v1"
EXPECTED_RESULTS = (
    ("nvda-daily", "NVDA_TIME_SERIES_DAILY.response.raw", "TIME_SERIES_DAILY", "NVDA"),
    ("avgo-estimates", "AVGO_EARNINGS_ESTIMATES.response.raw", "EARNINGS_ESTIMATES", "AVGO"),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceInventoryError(f"invalid adjudication input JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise EvidenceInventoryError(f"adjudication input root must be an object: {path.name}")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceInventoryError(f"{field} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceInventoryError(f"{field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceInventoryError(f"{field} must be a timezone-aware timestamp")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_run_root(private_root: Path, run_id: str) -> Path:
    if not run_id or any(token in run_id for token in ("/", "\\", "..", ":")):
        raise EvidenceInventoryError("run_id must be a simple directory name")
    run_root = private_root / "raw" / "alpha_vantage" / run_id
    _validate_output_path(private_root, run_root, label="supplemental run directory")
    if not run_root.is_dir():
        raise EvidenceInventoryError("supplemental run directory is missing")
    return run_root


def _validate_safe_capture(capture: dict[str, Any], *, request_id: str) -> None:
    if capture.get("request_host") != "www.alphavantage.co" or capture.get("request_path") != "/query":
        raise EvidenceInventoryError(f"capture provider path mismatch for {request_id}")
    parsed = urlsplit(str(capture.get("final_url") or ""))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.alphavantage.co"
        or parsed.path != "/query"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise EvidenceInventoryError(f"capture final URL is unsafe for {request_id}")
    if capture.get("api_key_persisted") is not False or capture.get("request_query_persisted") is not False:
        raise EvidenceInventoryError(f"capture secret-removal contract failed for {request_id}")
    if capture.get("automatic_retries") != 0:
        raise EvidenceInventoryError(f"capture retry contract failed for {request_id}")


def _validate_run_contract(
    ledger: dict[str, Any], *, run_id: str, generated_at: datetime,
) -> list[dict[str, Any]]:
    expected = {
        "run_id": run_id,
        "calls_attempted": 2,
        "calls_completed": 2,
        "automatic_retries": 0,
        "run_state": "failed",
        "failure_code": "estimate_periods_missing",
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    for field, value in expected.items():
        if ledger.get(field) != value:
            raise EvidenceInventoryError(f"supplemental ledger {field} contract mismatch")
    results = ledger.get("results")
    if not isinstance(results, list) or len(results) != len(EXPECTED_RESULTS):
        raise EvidenceInventoryError("supplemental ledger must contain exactly two completed results")
    expected_ids = [row[0] for row in EXPECTED_RESULTS]
    actual_ids = [row.get("request_id") if isinstance(row, dict) else None for row in results]
    if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise EvidenceInventoryError("supplemental ledger request order or uniqueness mismatch")
    started = _timestamp(ledger.get("started_at"), "ledger.started_at")
    finished = _timestamp(ledger.get("finished_at"), "ledger.finished_at")
    if not started <= finished <= generated_at:
        raise EvidenceInventoryError("supplemental ledger completion is after adjudication")
    return results


def _validate_capture_link(
    *, result: dict[str, Any], capture: dict[str, Any], raw_path: Path,
    expected_function: str, expected_ticker: str,
) -> None:
    request_id = str(result.get("request_id") or "")
    comparable = (
        "request_id", "function", "ticker", "content_type", "byte_length",
        "response_sha256", "http_status", "request_started_at",
        "headers_received_at", "response_completed_at", "available_at",
        "capture_state", "validation_code",
    )
    if any(result.get(field) != capture.get(field) for field in comparable):
        raise EvidenceInventoryError(f"ledger/capture mismatch for {request_id}")
    if capture.get("function") != expected_function or capture.get("ticker") != expected_ticker:
        raise EvidenceInventoryError(f"capture function/ticker mismatch for {request_id}")
    if result.get("response_file") != raw_path.name:
        raise EvidenceInventoryError(f"ledger response filename mismatch for {request_id}")
    request_started = _timestamp(capture.get("request_started_at"), f"{request_id}.request_started_at")
    headers_received = _timestamp(capture.get("headers_received_at"), f"{request_id}.headers_received_at")
    response_completed = _timestamp(capture.get("response_completed_at"), f"{request_id}.response_completed_at")
    available_at = _timestamp(capture.get("available_at"), f"{request_id}.available_at")
    if not request_started <= headers_received <= response_completed or available_at != response_completed:
        raise EvidenceInventoryError(f"capture timestamp contract failed for {request_id}")
    if capture.get("timestamp_order_valid") is not True:
        raise EvidenceInventoryError(f"capture timestamp flag failed for {request_id}")
    digest = _sha256(raw_path)
    if digest != str(capture.get("response_sha256") or "").upper():
        raise EvidenceInventoryError(f"capture source hash mismatch for {request_id}")
    if digest != str(result.get("response_sha256") or "").upper():
        raise EvidenceInventoryError(f"ledger source hash mismatch for {request_id}")
    if raw_path.stat().st_size != capture.get("byte_length") or raw_path.stat().st_size != result.get("byte_length"):
        raise EvidenceInventoryError(f"capture byte length mismatch for {request_id}")
    _validate_safe_capture(capture, request_id=request_id)


def adjudicate_supplemental_run(
    *, project_root: str | Path, run_id: str, generated_at: str,
    evidence_origin: str = "local_provider_evidence",
) -> dict[str, Any]:
    """Write one immutable local adjudication; never create a parser candidate."""

    if evidence_origin not in {"local_provider_evidence", "synthetic_fixture"}:
        raise EvidenceInventoryError("unsupported adjudication evidence origin")
    generated = _timestamp(generated_at, "generated_at")
    private_root = resolve_repository_private_data(project_root)
    run_root = _safe_run_root(private_root, run_id)
    ledger_path = run_root / "supplemental-collection-run.json"
    ledger = _read_json(ledger_path)
    results = _validate_run_contract(ledger, run_id=run_id, generated_at=generated)

    output_requests: list[dict[str, Any]] = []
    for result, expected in zip(results, EXPECTED_RESULTS, strict=True):
        request_id, raw_name, function, ticker = expected
        raw_path = run_root / raw_name
        capture_path = run_root / f"{raw_name}.manifest.json"
        _validate_output_path(private_root, raw_path, label=f"{request_id} raw response")
        _validate_output_path(private_root, capture_path, label=f"{request_id} capture manifest")
        if not raw_path.is_file() or not capture_path.is_file():
            raise EvidenceInventoryError(f"supplemental evidence is missing for {request_id}")
        capture = _read_json(capture_path)
        _validate_capture_link(
            result=result,
            capture=capture,
            raw_path=raw_path,
            expected_function=function,
            expected_ticker=ticker,
        )
        validation = validate_dataset_bytes(
            raw_path.read_bytes(),
            str(capture.get("content_type") or ""),
            function=function,
            ticker=ticker,
            available_at=str(capture.get("available_at") or ""),
        )
        if validation.get("valid_provider_dataset") is not True:
            raise EvidenceInventoryError(f"P5.0.2 revalidation failed for {request_id}")
        row: dict[str, Any] = {
            "request_id": request_id,
            "ticker": ticker,
            "function": function,
            "source_relative_path": f"raw/alpha_vantage/{run_id}/{raw_name}",
            "source_hash": _sha256(raw_path),
            "source_bytes": raw_path.stat().st_size,
            "capture_manifest_relative_path": f"raw/alpha_vantage/{run_id}/{capture_path.name}",
            "capture_manifest_hash": _sha256(capture_path),
            "available_at": capture.get("available_at"),
            "validation_code": validation.get("validation_code"),
            "provider_schema_version": validation.get("provider_schema_version"),
            "parser_candidate": False,
            "decision_created": False,
            "operational_eligible": False,
        }
        if function == "EARNINGS_ESTIMATES":
            if validation.get("provider_schema_version") != ESTIMATES_ARRAY_SCHEMA_VERSION:
                raise EvidenceInventoryError("AVGO estimates did not match the approved array schema")
            row.update({
                "record_count": validation.get("record_count"),
                "fiscal_quarter_count": validation.get("fiscal_quarter_count"),
                "fiscal_year_count": validation.get("fiscal_year_count"),
                "forward_period_candidate_count": validation.get("forward_period_candidate_count"),
                "accounting_basis": "unknown",
                "parser_candidate_basis_eligible": False,
                "historical_availability_verified": False,
                "historical_record_classification": "current_snapshot_records_for_past_periods",
                "fy1_ntm_mapping_eligible": False,
            })
        else:
            row.update({
                "record_count": validation.get("record_count"),
                "price_adjustment_policy": validation.get("price_adjustment_policy"),
                "allowed_use": "recent_price_flow_and_integrity_research_only",
                "prohibited_uses": [
                    "adjusted_returns", "long_horizon_backtest", "forward_per",
                    "valuation", "trading_decision",
                ],
            })
        output_requests.append(row)

    decision_root = private_root / "decision"
    _safe_mkdir(private_root, decision_root, label="supplemental adjudication directory")
    output_path = decision_root / f"alpha-vantage-supplemental-adjudication-p5.0.2-{run_id}.json"
    _validate_output_path(private_root, output_path, label="supplemental adjudication output")
    value = {
        "adjudication_version": ADJUDICATION_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "provider": "Alpha Vantage",
        "evidence_origin": evidence_origin,
        "run_id": run_id,
        "generated_at": _iso(generated),
        "collection_run": {
            "relative_path": f"raw/alpha_vantage/{run_id}/{ledger_path.name}",
            "source_hash": _sha256(ledger_path),
        },
        "requests": output_requests,
        "accounting_basis": "unknown",
        "historical_availability_verified": False,
        "parser_candidate_count": 0,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    _create_json_exclusive(private_root, output_path, value)
    return {
        "adjudication_path": output_path,
        "adjudication_sha256": _sha256(output_path),
        "collection_run_sha256": _sha256(ledger_path),
        "requests": output_requests,
    }


__all__ = [
    "ADJUDICATION_VERSION",
    "VALIDATOR_VERSION",
    "adjudicate_supplemental_run",
]
