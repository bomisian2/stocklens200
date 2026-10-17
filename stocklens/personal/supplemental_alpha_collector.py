"""Three-call, fail-fast Alpha Vantage supplemental evidence collector.

This collector never requests the already preserved terms document or AVGO daily
history.  It only preserves provider bytes and redacted manifests in a new,
immutable run directory.  Parsing, promotion, snapshots, decisions, and trading
outputs are deliberately out of scope.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .alpha_evidence_validation import validate_terms_bytes
from .evidence_inventory import resolve_repository_private_data
from .offline_alpha_collector import (
    API_ENDPOINT,
    EvidenceRequest,
    OfflineCollectorError,
    _ExclusiveCollectorLock,
    _capture_one,
    _create_json_exclusive,
    _failure_code,
    _iso,
    _safe_mkdir,
    _safe_url,
    _utc,
    _validate_output_path,
    _write_json,
    api_key_from_environment,
)


COLLECTOR_VERSION = "alpha-vantage-supplemental-evidence-p5.0.1-v1"
MAX_HTTP_REQUESTS = 3
REQUEST_INTERVAL_SECONDS = 60
BASELINE_TERMS_RUN_ID = "p422-8c6d282-approved-01"
BASELINE_TERMS_RELATIVE_PATH = (
    f"raw/alpha_vantage/{BASELINE_TERMS_RUN_ID}/terms-of-service.response.raw"
)
BASELINE_TERMS_SHA256 = (
    "88ED3D22FE0F3624E76E53A210DB3498B4A90712AB0D934356CC84B19ED1468A"
)

REQUEST_PLAN = (
    EvidenceRequest("nvda-daily", "dataset", API_ENDPOINT, "TIME_SERIES_DAILY", "NVDA"),
    EvidenceRequest("avgo-estimates", "dataset", API_ENDPOINT, "EARNINGS_ESTIMATES", "AVGO"),
    EvidenceRequest("nvda-estimates", "dataset", API_ENDPOINT, "EARNINGS_ESTIMATES", "NVDA"),
)


def exact_supplemental_request_plan() -> list[dict[str, Any]]:
    """Return the query-redacted, immutable three-request execution plan."""

    return [
        {
            "position": index,
            "request_id": row.request_id,
            "provider": "Alpha Vantage",
            "scheme": "https",
            "host": "www.alphavantage.co",
            "path": "/query",
            "function": row.function,
            "ticker": row.ticker,
            "fixed_parameters": (
                {"outputsize": "compact"}
                if row.function == "TIME_SERIES_DAILY" else {}
            ),
            "secret_parameter_names": ["apikey"],
        }
        for index, row in enumerate(REQUEST_PLAN, start=1)
    ]


def _raw_name(row: EvidenceRequest) -> str:
    return f"{row.ticker}_{row.function}.response.raw"


def _planned_paths(private_root: Path, run_id: str) -> dict[str, Any]:
    raw_root = private_root / "raw"
    provider_root = raw_root / "alpha_vantage"
    run_root = provider_root / run_id
    rights_root = private_root / "rights" / "alpha_vantage" / run_id
    requests_by_id: dict[str, dict[str, Path]] = {}
    for row in REQUEST_PLAN:
        raw = run_root / _raw_name(row)
        requests_by_id[row.request_id] = {
            "raw": raw,
            "partial": raw.with_suffix(raw.suffix + ".partial"),
            "manifest": raw.with_suffix(raw.suffix + ".manifest.json"),
        }
    return {
        "raw_root": raw_root,
        "provider_root": provider_root,
        "lock": provider_root / ".collector.lock",
        "run_root": run_root,
        "run_ledger": run_root / "supplemental-collection-run.json",
        "rights_root": rights_root,
        "terms_link": rights_root / "provider-terms-link.json",
        "requests": requests_by_id,
    }


def _all_output_paths(paths: dict[str, Any]) -> list[tuple[str, Path]]:
    values = [
        ("raw directory", paths["raw_root"]),
        ("provider directory", paths["provider_root"]),
        ("collector lock", paths["lock"]),
        ("run directory", paths["run_root"]),
        ("supplemental ledger", paths["run_ledger"]),
        ("rights directory", paths["rights_root"]),
        ("terms link", paths["terms_link"]),
    ]
    for request_id, request_paths in paths["requests"].items():
        values.extend(
            (f"{request_id} {kind}", path)
            for kind, path in request_paths.items()
        )
    return values


def _validate_paths(private_root: Path, paths: dict[str, Any]) -> None:
    for label, path in _all_output_paths(paths):
        _validate_output_path(private_root, path, label=label)


def _preflight_collisions(paths: dict[str, Any]) -> None:
    checked = [paths["run_root"], paths["run_ledger"], paths["terms_link"]]
    for request_paths in paths["requests"].values():
        checked.extend(request_paths.values())
    if any(path.exists() for path in checked):
        raise OfflineCollectorError(
            "supplemental collection output collision detected before any network request"
        )


def _verify_baseline_terms(
    private_root: Path, *, expected_sha256: str,
) -> dict[str, Any]:
    terms_path = private_root / BASELINE_TERMS_RELATIVE_PATH
    capture_path = terms_path.with_suffix(terms_path.suffix + ".manifest.json")
    for label, path in (("baseline terms", terms_path), ("baseline terms capture", capture_path)):
        _validate_output_path(private_root, path, label=label)
        if not path.is_file():
            raise OfflineCollectorError(f"{label} is missing")
    actual_hash = hashlib.sha256(terms_path.read_bytes()).hexdigest().upper()
    if actual_hash != expected_sha256.upper() or actual_hash != BASELINE_TERMS_SHA256:
        raise OfflineCollectorError("baseline terms SHA-256 mismatch")
    try:
        capture = json.loads(capture_path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineCollectorError("baseline terms capture manifest is invalid") from exc
    if not isinstance(capture, dict):
        raise OfflineCollectorError("baseline terms capture manifest root is invalid")
    if capture.get("response_sha256") != actual_hash:
        raise OfflineCollectorError("baseline terms capture hash does not match source bytes")
    if capture.get("request_query_persisted") is not False or capture.get("api_key_persisted") is not False:
        raise OfflineCollectorError("baseline terms capture did not preserve secret-free metadata")
    if _safe_url(str(capture.get("final_url") or "")) != "https://www.alphavantage.co/terms_of_service/":
        raise OfflineCollectorError("baseline terms final URL is not the approved official path")
    validation = validate_terms_bytes(
        terms_path.read_bytes(), str(capture.get("content_type") or ""),
    )
    if validation.get("valid_provider_dataset") is not True:
        raise OfflineCollectorError("baseline terms document is not structurally valid")
    return {
        "run_id": BASELINE_TERMS_RUN_ID,
        "relative_path": BASELINE_TERMS_RELATIVE_PATH,
        "source_sha256": actual_hash,
        "capture_manifest_relative_path": (
            f"{BASELINE_TERMS_RELATIVE_PATH}.manifest.json"
        ),
        "capture_manifest_sha256": hashlib.sha256(capture_path.read_bytes()).hexdigest().upper(),
        "review_status": "manual_review_required",
        "public_display_permission": "not_authorized",
    }


def _capture_succeeded(result: dict[str, Any]) -> bool:
    """Return true only for a complete, semantically valid provider capture."""

    return bool(
        result.get("http_status") == 200
        and result.get("capture_state") == "captured"
        and result.get("valid_provider_dataset") is True
        and result.get("failure_code") is None
        and result.get("provider_restriction_class") is None
        and result.get("timestamp_order_valid") is True
        and isinstance(result.get("response_sha256"), str)
        and len(result["response_sha256"]) == 64
        and isinstance(result.get("byte_length"), int)
        and result["byte_length"] > 0
    )


def _capture_failure(result: dict[str, Any]) -> str:
    """Return a normalized fail-fast code without copying provider text."""

    for field in ("provider_restriction_class", "failure_code", "validation_code"):
        value = result.get(field)
        if isinstance(value, str) and value and value not in {"valid_dataset", "valid"}:
            return value
    status = result.get("http_status")
    if isinstance(status, int) and status != 200:
        return f"http_{status}"
    return "data_collection_incomplete"


def collect_alpha_supplemental_evidence(
    *,
    project_root: str | Path,
    api_key: str,
    run_id: str,
    approval_reference: str,
    personal_noncommercial_confirmed: bool,
    current_provider_limit_review_confirmed: bool,
    max_calls: int = MAX_HTTP_REQUESTS,
    request_interval_seconds: int = REQUEST_INTERVAL_SECONDS,
    expected_terms_sha256: str = BASELINE_TERMS_SHA256,
    session: requests.Session | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute the separately approved three-request supplemental run."""

    if not personal_noncommercial_confirmed:
        raise OfflineCollectorError("personal non-commercial Alpha Vantage use must be confirmed")
    if not current_provider_limit_review_confirmed:
        raise OfflineCollectorError("current provider limit review must be confirmed")
    if not api_key.strip():
        raise OfflineCollectorError("ALPHAVANTAGE_API_KEY is required before any request")
    if max_calls != MAX_HTTP_REQUESTS:
        raise OfflineCollectorError("supplemental budget must be exactly three HTTP requests")
    if request_interval_seconds != REQUEST_INTERVAL_SECONDS:
        raise OfflineCollectorError("supplemental request interval must be exactly 60 seconds")
    if not approval_reference.strip():
        raise OfflineCollectorError("a separate execution approval reference is required")
    if not run_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in run_id
    ):
        raise OfflineCollectorError("run_id must use only letters, digits, hyphen, or underscore")

    private_root = resolve_repository_private_data(project_root, require_exists=True)
    terms_link = _verify_baseline_terms(
        private_root, expected_sha256=expected_terms_sha256,
    )
    effective_clock = clock or (lambda: datetime.now(timezone.utc))
    if now is not None and now.tzinfo is None:
        raise OfflineCollectorError("run start timestamp must be timezone-aware")
    started_at = now.astimezone(timezone.utc) if now else _utc(effective_clock)
    started_text = _iso(started_at)
    paths = _planned_paths(private_root, run_id)
    _validate_paths(private_root, paths)
    _safe_mkdir(private_root, paths["provider_root"], label="provider directory")
    _validate_paths(private_root, paths)
    client = session or requests.Session()
    report: dict[str, Any] = {
        "collector_version": COLLECTOR_VERSION,
        "run_id": run_id,
        "approval_reference": approval_reference,
        "started_at": started_text,
        "finished_at": None,
        "logical_operation_count": len(REQUEST_PLAN),
        "approved_actual_http_request_budget": MAX_HTTP_REQUESTS,
        "request_interval_seconds": REQUEST_INTERVAL_SECONDS,
        "concurrency": 1,
        "calls_attempted": 0,
        "calls_completed": 0,
        "automatic_retries": 0,
        "run_state": "initializing",
        "failure_code": None,
        "active_request_id": None,
        "results": [],
        "baseline_terms": terms_link,
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }

    with _ExclusiveCollectorLock(private_root, paths["lock"], run_id, started_text):
        _validate_paths(private_root, paths)
        _preflight_collisions(paths)
        _safe_mkdir(private_root, paths["run_root"], label="supplemental run directory")
        _safe_mkdir(private_root, paths["rights_root"], label="supplemental rights directory")
        _validate_paths(private_root, paths)
        _create_json_exclusive(private_root, paths["run_ledger"], report)
        _create_json_exclusive(private_root, paths["terms_link"], {
            "schema_version": "alpha-vantage-terms-link-p5-v1",
            "supplemental_run_id": run_id,
            **terms_link,
            "storage_permission": "manual_review_required",
            "derived_analysis_permission": "manual_review_required",
            "local_display_permission": "manual_review_required",
            "parser_candidate": False,
            "operational_eligible": False,
        })

        def persist() -> None:
            _write_json(private_root, paths["run_ledger"], report)

        def before_http(request_id: str, attempted_at: str) -> None:
            if report["calls_attempted"] >= MAX_HTTP_REQUESTS:
                raise OfflineCollectorError("supplemental HTTP request budget exhausted")
            report["calls_attempted"] += 1
            report["active_request_id"] = request_id
            report["last_attempted_at"] = attempted_at
            report["run_state"] = "running"
            persist()

        def after_http_completed() -> None:
            report["calls_completed"] += 1
            persist()

        try:
            for index, row in enumerate(REQUEST_PLAN):
                if index:
                    sleeper(float(REQUEST_INTERVAL_SECONDS))
                result = _capture_one(
                    row,
                    paths=paths["requests"][row.request_id],
                    api_key=api_key,
                    session=client,
                    clock=effective_clock,
                    before_http_request=before_http,
                    after_http_request_completed=after_http_completed,
                    private_root=private_root,
                )
                report["results"].append(result)
                report["active_request_id"] = None
                persist()
                if not _capture_succeeded(result):
                    report["run_state"] = "failed"
                    report["failure_code"] = _capture_failure(result)
                    persist()
                    break
            if report["run_state"] != "failed":
                invalid = [
                    row for row in report["results"]
                    if row.get("capture_state") != "captured"
                    or row.get("valid_provider_dataset") is not True
                ]
                if invalid or len(report["results"]) != len(REQUEST_PLAN):
                    report["run_state"] = "failed"
                    report["failure_code"] = "data_collection_incomplete"
                else:
                    report["run_state"] = "completed"
        except KeyboardInterrupt:
            report["run_state"] = "interrupted"
            report["failure_code"] = "interrupted"
            raise
        except BaseException as exc:
            report["run_state"] = "failed"
            report["failure_code"] = _failure_code(exc)
            raise
        finally:
            report["finished_at"] = _iso(_utc(effective_clock))
            persist()
    return report


def supplemental_report_is_complete(report: dict[str, Any]) -> bool:
    expected = [row.request_id for row in REQUEST_PLAN]
    results = report.get("results")
    return bool(
        isinstance(results, list)
        and report.get("run_state") == "completed"
        and report.get("failure_code") is None
        and report.get("logical_operation_count") == 3
        and report.get("approved_actual_http_request_budget") == 3
        and report.get("calls_attempted") == 3
        and report.get("calls_completed") == 3
        and report.get("automatic_retries") == 0
        and report.get("concurrency") == 1
        and [row.get("request_id") for row in results if isinstance(row, dict)] == expected
        and all(isinstance(row, dict) and _capture_succeeded(row) for row in results)
    )


__all__ = [
    "BASELINE_TERMS_SHA256",
    "MAX_HTTP_REQUESTS",
    "REQUEST_INTERVAL_SECONDS",
    "OfflineCollectorError",
    "api_key_from_environment",
    "collect_alpha_supplemental_evidence",
    "exact_supplemental_request_plan",
    "supplemental_report_is_complete",
]
