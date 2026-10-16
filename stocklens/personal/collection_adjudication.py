"""Offline adjudication of an immutable Alpha Vantage evidence run.

The adjudication never alters provider bytes, capture manifests, or the run
ledger.  It records a second-layer QA classification without copying provider
error text and without promoting any artifact into a parser candidate.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .alpha_evidence_validation import validate_dataset_bytes, validate_terms_bytes
from .evidence_inventory import EvidenceInventoryError, resolve_repository_private_data


ADJUDICATION_VERSION = "alpha-vantage-collection-adjudication-p4.3-v1"
TERMS_REVIEW_VERSION = "alpha-vantage-terms-review-p4.3-v1"
APPROVED_REQUESTS = {
    "alpha-vantage-terms": ("terms-of-service.response.raw", "captured_valid"),
    "avgo-daily": ("AVGO_TIME_SERIES_DAILY.response.raw", "captured_unadjusted_daily"),
    "nvda-daily": ("NVDA_TIME_SERIES_DAILY.response.raw", "invalid_provider_dataset"),
    "avgo-estimates": ("AVGO_EARNINGS_ESTIMATES.response.raw", "invalid_provider_dataset"),
    "nvda-estimates": ("NVDA_EARNINGS_ESTIMATES.response.raw", "invalid_provider_dataset"),
}
RESTRICTION_CLASS = "provider_rate_or_plan_limit"


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

    return {
        "review_version": TERMS_REVIEW_VERSION,
        "provider": "Alpha Vantage",
        "run_id": run_id,
        "generated_at": _iso_timestamp(generated_at),
        "terms_document": {
            "relative_path": f"raw/alpha_vantage/{run_id}/{terms_path.name}",
            "source_hash": _sha256(terms_path),
            "capture_manifest_hash": _sha256(capture_path),
            "page_count": 4,
        },
        "reviewed_by": None,
        "reviewed_at": None,
        "review_status": "pending_independent_qa",
        "scope_assessments": {
            "personal_noncommercial_use": {
                "status": "text_supports_limited_license",
                "pages": [1, 2],
                "basis": "Personal, private and individual investment analysis or research is within the stated limited-use context.",
            },
            "local_storage": {
                "status": "manual_review_required",
                "pages": [1, 2, 3],
                "basis": "No explicit local caching, retention period, or stored-data disposition grant was identified.",
            },
            "derived_analysis": {
                "status": "manual_review_required",
                "pages": [1, 2],
                "basis": "Private investment analysis is mentioned, but derivative-data rights are not expressly granted.",
            },
            "local_display": {
                "status": "manual_review_required",
                "pages": [1, 2],
                "basis": "Personal device display is addressed for the platform, but local display of stored market data remains ambiguous.",
            },
            "public_display": {
                "status": "not_authorized",
                "pages": [1, 2],
                "basis": "The captured terms do not grant public display, and third-party access can fall within commercial use.",
            },
            "redistribution": {
                "status": "not_authorized",
                "pages": [1, 2],
                "basis": "The license is non-sublicensable and non-transferable; no redistribution grant was identified.",
            },
            "retention_and_deletion": {
                "status": "manual_review_required",
                "pages": [3],
                "basis": "Termination addresses access deactivation but does not state a complete retained-data deletion policy.",
            },
            "attribution": {
                "status": "manual_review_required",
                "pages": [1, 2, 3, 4],
                "basis": "No explicit attribution rule was identified in the captured four-page document.",
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
    if ledger.get("run_id") != run_id:
        raise EvidenceInventoryError("collection ledger run_id mismatch")
    results = ledger.get("results")
    if not isinstance(results, list) or {row.get("request_id") for row in results if isinstance(row, dict)} != set(APPROVED_REQUESTS):
        raise EvidenceInventoryError("collection ledger does not contain the exact approved request set")

    requests: list[dict[str, Any]] = []
    for result in results:
        request_id = str(result["request_id"])
        raw_name, adjudication = APPROVED_REQUESTS[request_id]
        raw_path = run_root / raw_name
        capture_path = run_root / f"{raw_name}.manifest.json"
        capture = _read_json(capture_path)
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
                "restriction_class": RESTRICTION_CLASS,
                "restriction_detail": "daily_quota_or_plan_entitlement_undetermined",
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
    terms_output = decision_root / f"alpha-vantage-terms-review-{run_id}.json"
    adjudication_output = decision_root / f"alpha-vantage-adjudication-{run_id}.json"
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
        "restriction_class": RESTRICTION_CLASS,
        "restriction_detail": "daily_quota_or_plan_entitlement_undetermined",
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
