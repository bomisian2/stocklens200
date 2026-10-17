"""One-call, fail-closed continuation collector for NVDA estimates.

The collector is intentionally separate from the failed three-operation
supplemental run.  It verifies immutable predecessor evidence before the only
allowed HTTP request and never parses, promotes, snapshots, or decides.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

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
    _utc,
    _validate_output_path,
    _write_json,
    api_key_from_environment,
)
from .supplemental_alpha_collector import _capture_failure, _capture_succeeded


COLLECTOR_VERSION = "alpha-vantage-continuation-evidence-p5.0.3-v1"
MAX_HTTP_REQUESTS = 1
PREVIOUS_RUN_ID = "p501-e0f7040-supplemental-approved-01"
PREVIOUS_LEDGER_RELATIVE_PATH = (
    f"raw/alpha_vantage/{PREVIOUS_RUN_ID}/supplemental-collection-run.json"
)
AVGO_RAW_RELATIVE_PATH = (
    f"raw/alpha_vantage/{PREVIOUS_RUN_ID}/AVGO_EARNINGS_ESTIMATES.response.raw"
)
AVGO_MANIFEST_RELATIVE_PATH = f"{AVGO_RAW_RELATIVE_PATH}.manifest.json"
P502_ADJUDICATION_RELATIVE_PATH = (
    "decision/alpha-vantage-supplemental-adjudication-p5.0.2-"
    f"{PREVIOUS_RUN_ID}.json"
)
PREVIOUS_LEDGER_SHA256 = (
    "4D41B0C9BA23FDC15F999635BFF37EBF2BB2578031E2C4074AA876058D0B53AE"
)
AVGO_RAW_SHA256 = (
    "C3BA32A6CBF603C2E3137F39A47641912AD4E63EE0938F8C7177DA4B297AA5DF"
)
AVGO_MANIFEST_SHA256 = (
    "3AD16F23A447287F3C192C680D76DBA92119BC197E80C87FE93EA5114552DFA3"
)
P502_ADJUDICATION_SHA256 = (
    "F7F65E8B3FED11EC2C1EE2CB3280F2B313E375429FE96517D887178EA4127D01"
)

REQUEST = EvidenceRequest(
    "nvda-estimates", "dataset", API_ENDPOINT, "EARNINGS_ESTIMATES", "NVDA",
)


def exact_continuation_request_plan() -> list[dict[str, Any]]:
    """Return the secret-free, immutable one-request execution plan."""

    return [{
        "position": 1,
        "request_id": REQUEST.request_id,
        "provider": "Alpha Vantage",
        "scheme": "https",
        "host": "www.alphavantage.co",
        "path": "/query",
        "function": REQUEST.function,
        "ticker": REQUEST.ticker,
        "fixed_parameters": {},
        "secret_parameter_names": ["apikey"],
    }]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise OfflineCollectorError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise OfflineCollectorError(f"{label} root must be an object")
    return value


def _verify_hash(
    private_root: Path,
    relative_path: str,
    expected_hash: str,
    *,
    label: str,
) -> tuple[Path, str]:
    path = private_root / relative_path
    _validate_output_path(private_root, path, label=label)
    if not path.is_file():
        raise OfflineCollectorError(f"{label} is missing")
    actual = _sha256(path)
    if actual != expected_hash.upper():
        raise OfflineCollectorError(f"{label} SHA-256 mismatch")
    return path, actual


def _verify_predecessor_evidence(
    private_root: Path,
    *,
    expected_ledger_sha256: str,
    expected_avgo_raw_sha256: str,
    expected_avgo_manifest_sha256: str,
    expected_adjudication_sha256: str,
) -> dict[str, Any]:
    """Verify the failed run and prove NVDA estimates were never captured."""

    ledger_path, ledger_hash = _verify_hash(
        private_root, PREVIOUS_LEDGER_RELATIVE_PATH, expected_ledger_sha256,
        label="previous supplemental ledger",
    )
    avgo_raw_path, avgo_raw_hash = _verify_hash(
        private_root, AVGO_RAW_RELATIVE_PATH, expected_avgo_raw_sha256,
        label="previous AVGO estimates raw",
    )
    avgo_manifest_path, avgo_manifest_hash = _verify_hash(
        private_root, AVGO_MANIFEST_RELATIVE_PATH, expected_avgo_manifest_sha256,
        label="previous AVGO estimates manifest",
    )
    adjudication_path, adjudication_hash = _verify_hash(
        private_root, P502_ADJUDICATION_RELATIVE_PATH, expected_adjudication_sha256,
        label="P5.0.2 adjudication",
    )
    ledger = _load_json(ledger_path, label="previous supplemental ledger")
    results = ledger.get("results")
    if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
        raise OfflineCollectorError("previous supplemental ledger results are invalid")
    request_ids = [row.get("request_id") for row in results]
    if request_ids != ["nvda-daily", "avgo-estimates"]:
        raise OfflineCollectorError("previous supplemental ledger request history mismatch")
    if "nvda-estimates" in request_ids:
        raise OfflineCollectorError("previous run already contains NVDA estimates")
    expected_ledger_fields = {
        "run_id": PREVIOUS_RUN_ID,
        "run_state": "failed",
        "failure_code": "estimate_periods_missing",
        "calls_attempted": 2,
        "calls_completed": 2,
        "automatic_retries": 0,
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    for field, expected in expected_ledger_fields.items():
        if ledger.get(field) != expected:
            raise OfflineCollectorError(f"previous supplemental ledger {field} mismatch")

    manifest = _load_json(avgo_manifest_path, label="previous AVGO estimates manifest")
    expected_manifest = {
        "request_id": "avgo-estimates",
        "function": "EARNINGS_ESTIMATES",
        "ticker": "AVGO",
        "response_sha256": avgo_raw_hash,
        "api_key_persisted": False,
        "request_query_persisted": False,
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            raise OfflineCollectorError(f"previous AVGO manifest {field} mismatch")

    adjudication = _load_json(adjudication_path, label="P5.0.2 adjudication")
    if adjudication.get("run_id") != PREVIOUS_RUN_ID:
        raise OfflineCollectorError("P5.0.2 adjudication run_id mismatch")
    collection_run = adjudication.get("collection_run")
    if not isinstance(collection_run, dict) or collection_run.get("source_hash") != ledger_hash:
        raise OfflineCollectorError("P5.0.2 adjudication ledger hash link mismatch")
    adjudicated_requests = adjudication.get("requests")
    if not isinstance(adjudicated_requests, list):
        raise OfflineCollectorError("P5.0.2 adjudication requests are invalid")
    avgo_rows = [
        row for row in adjudicated_requests
        if isinstance(row, dict) and row.get("request_id") == "avgo-estimates"
    ]
    if len(avgo_rows) != 1:
        raise OfflineCollectorError("P5.0.2 adjudication AVGO evidence mismatch")
    if (
        avgo_rows[0].get("source_hash") != avgo_raw_hash
        or avgo_rows[0].get("capture_manifest_hash") != avgo_manifest_hash
    ):
        raise OfflineCollectorError("P5.0.2 adjudication AVGO hash link mismatch")
    for field, expected in {
        "historical_availability_verified": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }.items():
        if adjudication.get(field) != expected:
            raise OfflineCollectorError(f"P5.0.2 adjudication {field} mismatch")

    return {
        "previous_run_id": PREVIOUS_RUN_ID,
        "supplemental_ledger": {
            "relative_path": PREVIOUS_LEDGER_RELATIVE_PATH,
            "source_sha256": ledger_hash,
        },
        "avgo_estimates_raw": {
            "relative_path": AVGO_RAW_RELATIVE_PATH,
            "source_sha256": avgo_raw_hash,
        },
        "avgo_estimates_manifest": {
            "relative_path": AVGO_MANIFEST_RELATIVE_PATH,
            "source_sha256": avgo_manifest_hash,
        },
        "p502_adjudication": {
            "relative_path": P502_ADJUDICATION_RELATIVE_PATH,
            "source_sha256": adjudication_hash,
        },
        "nvda_estimates_previously_captured": False,
    }


def _planned_paths(private_root: Path, run_id: str) -> dict[str, Path]:
    provider_root = private_root / "raw" / "alpha_vantage"
    run_root = provider_root / run_id
    raw = run_root / "NVDA_EARNINGS_ESTIMATES.response.raw"
    return {
        "provider_root": provider_root,
        "lock": provider_root / ".collector.lock",
        "run_root": run_root,
        "raw": raw,
        "partial": raw.with_suffix(raw.suffix + ".partial"),
        "manifest": raw.with_suffix(raw.suffix + ".manifest.json"),
        "ledger": run_root / "continuation-collection-run.json",
    }


def _validate_paths(private_root: Path, paths: dict[str, Path]) -> None:
    for label, path in paths.items():
        _validate_output_path(private_root, path, label=f"continuation {label}")


def _preflight_collisions(paths: dict[str, Path]) -> None:
    if any(paths[key].exists() for key in ("run_root", "raw", "partial", "manifest", "ledger")):
        raise OfflineCollectorError(
            "continuation run_id reuse or output collision detected before any request"
        )


def collect_alpha_continuation_evidence(
    *,
    project_root: str | Path,
    api_key: str,
    run_id: str,
    approval_reference: str,
    personal_noncommercial_confirmed: bool,
    current_provider_limit_review_confirmed: bool,
    max_calls: int = MAX_HTTP_REQUESTS,
    expected_ledger_sha256: str = PREVIOUS_LEDGER_SHA256,
    expected_avgo_raw_sha256: str = AVGO_RAW_SHA256,
    expected_avgo_manifest_sha256: str = AVGO_MANIFEST_SHA256,
    expected_adjudication_sha256: str = P502_ADJUDICATION_SHA256,
    session: requests.Session | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Capture exactly one NVDA estimates response after strict preflight."""

    if not personal_noncommercial_confirmed:
        raise OfflineCollectorError("personal non-commercial Alpha Vantage use must be confirmed")
    if not current_provider_limit_review_confirmed:
        raise OfflineCollectorError("current provider limit review must be confirmed")
    if not api_key.strip():
        raise OfflineCollectorError("ALPHAVANTAGE_API_KEY is required before any request")
    if max_calls != MAX_HTTP_REQUESTS:
        raise OfflineCollectorError("continuation budget must be exactly one HTTP request")
    if not approval_reference.strip():
        raise OfflineCollectorError("a separate P5.0.3 execution approval reference is required")
    if not run_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in run_id
    ):
        raise OfflineCollectorError("run_id must use only letters, digits, hyphen, or underscore")
    if run_id == PREVIOUS_RUN_ID:
        raise OfflineCollectorError("continuation run_id must differ from the previous run")

    private_root = resolve_repository_private_data(project_root, require_exists=True)
    predecessor = _verify_predecessor_evidence(
        private_root,
        expected_ledger_sha256=expected_ledger_sha256,
        expected_avgo_raw_sha256=expected_avgo_raw_sha256,
        expected_avgo_manifest_sha256=expected_avgo_manifest_sha256,
        expected_adjudication_sha256=expected_adjudication_sha256,
    )
    paths = _planned_paths(private_root, run_id)
    _validate_paths(private_root, paths)
    _safe_mkdir(private_root, paths["provider_root"], label="continuation provider directory")
    _validate_paths(private_root, paths)
    effective_clock = clock or (lambda: datetime.now(timezone.utc))
    if now is not None and now.tzinfo is None:
        raise OfflineCollectorError("run start timestamp must be timezone-aware")
    started_at = now.astimezone(timezone.utc) if now else _utc(effective_clock)
    started_text = _iso(started_at)
    report: dict[str, Any] = {
        "collector_version": COLLECTOR_VERSION,
        "run_id": run_id,
        "approval_reference": approval_reference,
        "started_at": started_text,
        "finished_at": None,
        "logical_operation_count": 1,
        "approved_actual_http_request_budget": 1,
        "concurrency": 1,
        "calls_attempted": 0,
        "calls_completed": 0,
        "automatic_retries": 0,
        "redirects_allowed": False,
        "run_state": "initializing",
        "failure_code": None,
        "active_request_id": None,
        "results": [],
        "predecessor_evidence": predecessor,
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    client = session or requests.Session()

    with _ExclusiveCollectorLock(private_root, paths["lock"], run_id, started_text):
        _validate_paths(private_root, paths)
        _preflight_collisions(paths)
        current_predecessor = _verify_predecessor_evidence(
            private_root,
            expected_ledger_sha256=expected_ledger_sha256,
            expected_avgo_raw_sha256=expected_avgo_raw_sha256,
            expected_avgo_manifest_sha256=expected_avgo_manifest_sha256,
            expected_adjudication_sha256=expected_adjudication_sha256,
        )
        if current_predecessor != predecessor:
            raise OfflineCollectorError("predecessor evidence changed during preflight")
        _safe_mkdir(private_root, paths["run_root"], label="continuation run directory")
        _validate_paths(private_root, paths)
        _create_json_exclusive(private_root, paths["ledger"], report)

        def persist() -> None:
            _write_json(private_root, paths["ledger"], report)

        def before_http(request_id: str, attempted_at: str) -> None:
            if report["calls_attempted"] >= 1:
                raise OfflineCollectorError("continuation HTTP request budget exhausted")
            report["calls_attempted"] += 1
            report["active_request_id"] = request_id
            report["last_attempted_at"] = attempted_at
            report["run_state"] = "running"
            persist()

        def after_http_completed() -> None:
            report["calls_completed"] += 1
            persist()

        try:
            result = _capture_one(
                REQUEST,
                paths={
                    "raw": paths["raw"],
                    "partial": paths["partial"],
                    "manifest": paths["manifest"],
                },
                api_key=api_key,
                session=client,
                clock=effective_clock,
                before_http_request=before_http,
                after_http_request_completed=after_http_completed,
                private_root=private_root,
            )
            report["results"].append(result)
            report["active_request_id"] = None
            if _capture_succeeded(result):
                report["run_state"] = "completed"
            else:
                report["run_state"] = "failed"
                report["failure_code"] = _capture_failure(result)
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


def continuation_report_is_complete(report: dict[str, Any]) -> bool:
    results = report.get("results")
    return bool(
        isinstance(results, list)
        and len(results) == 1
        and report.get("run_state") == "completed"
        and report.get("failure_code") is None
        and report.get("logical_operation_count") == 1
        and report.get("approved_actual_http_request_budget") == 1
        and report.get("calls_attempted") == 1
        and report.get("calls_completed") == 1
        and report.get("automatic_retries") == 0
        and report.get("concurrency") == 1
        and report.get("parser_attempted") is False
        and report.get("candidate_created") is False
        and report.get("snapshot_created") is False
        and report.get("decision_created") is False
        and report.get("operational_eligible") is False
        and isinstance(results[0], dict)
        and results[0].get("request_id") == "nvda-estimates"
        and results[0].get("accounting_basis") == "unknown"
        and results[0].get("historical_availability_verified") is False
        and results[0].get("fy1_ntm_mapping_eligible") is False
        and results[0].get("parser_candidate") is False
        and results[0].get("snapshot_created") is False
        and results[0].get("decision_created") is False
        and results[0].get("operational_eligible") is False
        and _capture_succeeded(results[0])
    )


__all__ = [
    "AVGO_MANIFEST_SHA256",
    "AVGO_RAW_SHA256",
    "MAX_HTTP_REQUESTS",
    "P502_ADJUDICATION_SHA256",
    "PREVIOUS_LEDGER_SHA256",
    "OfflineCollectorError",
    "api_key_from_environment",
    "collect_alpha_continuation_evidence",
    "continuation_report_is_complete",
    "exact_continuation_request_plan",
]
