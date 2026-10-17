"""Fail-closed Alpha Vantage evidence collector for a separately approved run.

The collector preserves immutable provider bytes and redacted manifests only.
It never parses into StockLens data, promotes a candidate, creates a snapshot,
or calculates an investment decision.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from .alpha_evidence_validation import validate_dataset_bytes, validate_terms_bytes
from .evidence_inventory import resolve_repository_private_data


COLLECTOR_VERSION = "alpha-vantage-offline-evidence-v2"
API_ENDPOINT = "https://www.alphavantage.co/query"
TERMS_URL = "https://www.alphavantage.co/terms_of_service/"
ALLOWED_HOSTS = frozenset({"www.alphavantage.co", "alphavantage.co"})
ALLOWED_TICKERS = frozenset({"AVGO", "NVDA"})
ALLOWED_FUNCTIONS = frozenset({"TIME_SERIES_DAILY", "EARNINGS_ESTIMATES"})
MAX_CALLS = 5
MAX_HTTP_REQUESTS = 5


@dataclass(frozen=True)
class EvidenceRequest:
    request_id: str
    kind: str
    url: str
    function: str | None = None
    ticker: str | None = None


REQUEST_PLAN = (
    EvidenceRequest("alpha-vantage-terms", "terms", TERMS_URL),
    EvidenceRequest("avgo-daily", "dataset", API_ENDPOINT, "TIME_SERIES_DAILY", "AVGO"),
    EvidenceRequest("nvda-daily", "dataset", API_ENDPOINT, "TIME_SERIES_DAILY", "NVDA"),
    EvidenceRequest("avgo-estimates", "dataset", API_ENDPOINT, "EARNINGS_ESTIMATES", "AVGO"),
    EvidenceRequest("nvda-estimates", "dataset", API_ENDPOINT, "EARNINGS_ESTIMATES", "NVDA"),
)


class OfflineCollectorError(ValueError):
    """Raised before or during a local evidence collection run."""


def _utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise OfflineCollectorError("collector clock must return a timezone-aware timestamp")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def exact_request_plan() -> list[dict[str, Any]]:
    """Return the immutable, secret-free five-operation allowlist."""

    return [
        {
            "request_id": row.request_id,
            "provider": "Alpha Vantage",
            "host": urlsplit(row.url).hostname,
            "path": urlsplit(row.url).path,
            "function": row.function,
            "ticker": row.ticker,
        }
        for row in REQUEST_PLAN
    ]


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise OfflineCollectorError("provider URL is outside the approved HTTPS Alpha Vantage domain")
    return urlunsplit(("https", host, parsed.path or "/", "", ""))


def _validate_request(row: EvidenceRequest) -> None:
    _safe_url(row.url)
    if row.kind == "terms":
        if row.function is not None or row.ticker is not None:
            raise OfflineCollectorError("terms request must not include dataset parameters")
        return
    if row.function not in ALLOWED_FUNCTIONS or row.ticker not in ALLOWED_TICKERS:
        raise OfflineCollectorError("request is outside the approved function/ticker allowlist")


def _raw_name(row: EvidenceRequest) -> str:
    if row.kind == "terms":
        return "terms-of-service.response.raw"
    return f"{row.ticker}_{row.function}.response.raw"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_output_path(private_root: Path, path: Path, *, label: str) -> None:
    """Fail closed when a collector path resolves outside private_data."""

    base = private_root.resolve(strict=True)
    absolute = Path(os.path.abspath(path))
    if not _is_within(absolute, base):
        raise OfflineCollectorError(f"{label} is outside repository private_data")
    cursor = base
    for part in absolute.relative_to(base).parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            try:
                resolved = cursor.resolve(strict=True)
            except OSError as exc:
                raise OfflineCollectorError(f"{label} cannot be safely resolved") from exc
            if not _is_within(resolved, base):
                raise OfflineCollectorError(f"{label} resolves outside repository private_data")
    resolved = absolute.resolve(strict=False)
    if not _is_within(resolved, base):
        raise OfflineCollectorError(f"{label} resolves outside repository private_data")


def _safe_mkdir(private_root: Path, directory: Path, *, label: str) -> None:
    """Create a directory one component at a time and recheck every component."""

    base = private_root.resolve(strict=True)
    absolute = Path(os.path.abspath(directory))
    _validate_output_path(private_root, absolute, label=label)
    cursor = base
    for part in absolute.relative_to(base).parts:
        cursor = cursor / part
        _validate_output_path(private_root, cursor, label=label)
        try:
            cursor.mkdir(exist_ok=True)
        except FileExistsError as exc:
            raise OfflineCollectorError(f"{label} collides with a non-directory path") from exc
        if not cursor.is_dir():
            raise OfflineCollectorError(f"{label} collides with a non-directory path")
        _validate_output_path(private_root, cursor, label=label)


def _write_json(private_root: Path, path: Path, value: dict[str, Any]) -> None:
    _validate_output_path(private_root, path.parent, label="JSON output parent")
    _validate_output_path(private_root, path, label="JSON output")
    temporary = path.with_suffix(path.suffix + ".tmp")
    _validate_output_path(private_root, temporary, label="temporary JSON output")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_output_path(private_root, temporary, label="temporary JSON output")
    _validate_output_path(private_root, path, label="JSON output")
    temporary.replace(path)


def _create_json_exclusive(private_root: Path, path: Path, value: dict[str, Any]) -> None:
    _validate_output_path(private_root, path.parent, label="exclusive JSON output parent")
    _validate_output_path(private_root, path, label="exclusive JSON output")
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _response_chunks(response: Any) -> Iterable[bytes]:
    if hasattr(response, "iter_content"):
        yield from response.iter_content(chunk_size=64 * 1024)
        return
    content = getattr(response, "content", b"")
    if content:
        yield bytes(content)


def _planned_paths(private_root: Path, run_id: str) -> dict[str, Any]:
    raw_root = private_root / "raw"
    provider_root = raw_root / "alpha_vantage"
    run_root = provider_root / run_id
    rights_root = private_root / "rights" / "alpha_vantage" / run_id
    rights_path = rights_root / "provider-terms-manifest.json"
    requests: dict[str, dict[str, Path]] = {}
    for row in REQUEST_PLAN:
        raw = run_root / _raw_name(row)
        requests[row.request_id] = {
            "raw": raw,
            "partial": raw.with_suffix(raw.suffix + ".partial"),
            "manifest": raw.with_suffix(raw.suffix + ".manifest.json"),
        }
    return {
        "raw_root": raw_root,
        "provider_root": provider_root,
        "lock": provider_root / ".collector.lock",
        "run_root": run_root,
        "run_ledger": run_root / "collection-run.json",
        "rights_root": rights_root,
        "rights": rights_path,
        "requests": requests,
    }


def _all_planned_paths(paths: dict[str, Any]) -> list[tuple[str, Path]]:
    planned = [
        ("raw directory", paths["raw_root"]),
        ("provider directory", paths["provider_root"]),
        ("collector lock", paths["lock"]),
        ("run directory", paths["run_root"]),
        ("run ledger", paths["run_ledger"]),
        ("rights directory", paths["rights_root"]),
        ("rights manifest", paths["rights"]),
    ]
    for request_id, row in paths["requests"].items():
        for kind, path in row.items():
            planned.append((f"{request_id} {kind}", path))
    return planned


def _validate_all_output_paths(private_root: Path, paths: dict[str, Any]) -> None:
    for label, path in _all_planned_paths(paths):
        _validate_output_path(private_root, path, label=label)


def _preflight_output_paths(paths: dict[str, Any]) -> None:
    collisions: list[Path] = []
    run_root = paths["run_root"]
    if run_root.exists():
        collisions.append(run_root)
    if paths["rights"].exists():
        collisions.append(paths["rights"])
    for row in paths["requests"].values():
        collisions.extend(path for path in row.values() if path.exists())
    if paths["run_ledger"].exists():
        collisions.append(paths["run_ledger"])
    if collisions:
        raise OfflineCollectorError("collection output collision detected before any network request")


class _ExclusiveCollectorLock:
    def __init__(self, private_root: Path, path: Path, run_id: str, started_at: str) -> None:
        self.private_root = private_root
        self.path = path
        self.run_id = run_id
        self.started_at = started_at
        self.acquired = False

    def __enter__(self) -> "_ExclusiveCollectorLock":
        _validate_output_path(self.private_root, self.path.parent, label="collector lock parent")
        _validate_output_path(self.private_root, self.path, label="collector lock")
        payload = json.dumps({
            "collector_version": COLLECTOR_VERSION,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "state": "running",
        }, sort_keys=True).encode("utf-8")
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise OfflineCollectorError("another Alpha Vantage collection is already active") from exc
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _validate_output_path(self.private_root, self.path, label="collector lock")
        self.acquired = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        if self.acquired:
            try:
                _validate_output_path(self.private_root, self.path, label="collector lock")
                current = json.loads(self.path.read_text(encoding="utf-8"))
            except (OfflineCollectorError, OSError, json.JSONDecodeError):
                current = {}
            if current.get("run_id") == self.run_id:
                self.path.unlink(missing_ok=True)
        self.acquired = False


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.RequestException):
        return "request_error"
    if isinstance(exc, OSError):
        return "local_io_error"
    if isinstance(exc, OfflineCollectorError):
        return "collector_policy_error"
    return "unexpected_error"


def _capture_one(
    row: EvidenceRequest,
    *,
    collector_version: str,
    paths: dict[str, Path],
    api_key: str,
    session: requests.Session,
    clock: Callable[[], datetime],
    before_http_request: Callable[[str, str], None],
    after_http_request_completed: Callable[[], None],
    private_root: Path,
) -> dict[str, Any]:
    """Capture one logical operation using manually validated redirects."""

    if not collector_version.strip():
        raise OfflineCollectorError("capture collector version is required")
    _validate_request(row)
    raw_path, partial_path, manifest_path = paths["raw"], paths["partial"], paths["manifest"]
    for label, path in (("raw response", raw_path), ("partial response", partial_path),
                        ("capture manifest", manifest_path)):
        _validate_output_path(private_root, path, label=label)
    params: dict[str, str] | None = None
    if row.kind == "dataset":
        params = {"function": str(row.function), "symbol": str(row.ticker), "apikey": api_key}
        if row.function == "TIME_SERIES_DAILY":
            params["outputsize"] = "compact"
    request_started_at: datetime | None = None
    headers_received_at: datetime | None = None
    response_completed_at: datetime | None = None
    response: Any | None = None
    status_code: int | None = None
    content_type: str | None = None
    final_url: str | None = None
    digest = hashlib.sha256()
    byte_length = 0
    capture_state = "failed"
    failure_code: str | None = None
    validation: dict[str, Any] = {"valid_provider_dataset": False, "validation_code": "not_validated"}
    redirect_chain: list[dict[str, Any]] = []
    current_url = row.url
    current_params = params
    propagate: BaseException | None = None
    try:
        while True:
            attempt_started = _utc(clock)
            if request_started_at is None:
                request_started_at = attempt_started
            before_http_request(row.request_id, _iso(attempt_started))
            response = session.get(
                current_url,
                params=current_params,
                timeout=30,
                allow_redirects=False,
                stream=True,
            )
            attempt_headers = _utc(clock)
            status_code = int(response.status_code)
            response_url = _safe_url(str(getattr(response, "url", current_url)))
            if 300 <= status_code < 400:
                location = str(getattr(response, "headers", {}).get("Location") or "")
                if not location:
                    raise OfflineCollectorError("redirect response did not provide a Location header")
                redirected = _safe_url(urljoin(current_url, location))
                redirect_chain.append({
                    "status": status_code,
                    "from": response_url,
                    "to": redirected,
                    "request_started_at": _iso(attempt_started),
                    "headers_received_at": _iso(attempt_headers),
                })
                response.close()
                after_http_request_completed()
                response = None
                current_url = redirected
                current_params = None
                continue
            headers_received_at = attempt_headers
            final_url = response_url
            content_type = str(getattr(response, "headers", {}).get("Content-Type") or "") or None
            _validate_output_path(private_root, partial_path.parent, label="partial response parent")
            _validate_output_path(private_root, partial_path, label="partial response")
            with partial_path.open("xb") as handle:
                for chunk in _response_chunks(response):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    handle.flush()
                    digest.update(chunk)
                    byte_length += len(chunk)
                os.fsync(handle.fileno())
            response_completed_at = _utc(clock)
            after_http_request_completed()
            payload = partial_path.read_bytes()
            if row.kind == "terms":
                validation = validate_terms_bytes(payload, content_type)
            else:
                validation = validate_dataset_bytes(
                    payload,
                    content_type,
                    function=str(row.function),
                    ticker=str(row.ticker),
                    available_at=(
                        _iso(response_completed_at)
                        if response_completed_at is not None else None
                    ),
                )
            if status_code != 200:
                capture_state, failure_code = "http_error", f"http_{status_code}"
            elif not validation["valid_provider_dataset"]:
                capture_state = "invalid_provider_dataset"
                failure_code = str(validation.get("validation_code") or "provider_dataset_invalid")
            else:
                capture_state = "captured"
            _validate_output_path(private_root, partial_path, label="partial response")
            _validate_output_path(private_root, raw_path, label="raw response")
            partial_path.replace(raw_path)
            break
    except BaseException as exc:
        failure_code = _failure_code(exc)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            propagate = exc
        elif not isinstance(exc, Exception):
            propagate = exc
    finally:
        if response is not None and hasattr(response, "close"):
            response.close()
        kept_path = raw_path if raw_path.exists() else partial_path if partial_path.exists() else None
        if kept_path is not None:
            payload = kept_path.read_bytes()
            digest = hashlib.sha256(payload)
            byte_length = len(payload)
        timestamp_order_valid = bool(
            request_started_at is not None
            and headers_received_at is not None
            and response_completed_at is not None
            and request_started_at <= headers_received_at <= response_completed_at
        )
        manifest = {
            "collector_version": collector_version,
            "request_id": row.request_id,
            "provider": "Alpha Vantage",
            "request_host": urlsplit(row.url).hostname,
            "request_path": urlsplit(row.url).path,
            "function": row.function,
            "ticker": row.ticker,
            "request_started_at": _iso(request_started_at) if request_started_at else None,
            "headers_received_at": _iso(headers_received_at) if headers_received_at else None,
            "response_completed_at": _iso(response_completed_at) if response_completed_at else None,
            "available_at": _iso(response_completed_at) if response_completed_at else None,
            "timestamp_order_valid": timestamp_order_valid,
            "http_status": status_code,
            "final_url": final_url,
            "redirect_chain": redirect_chain,
            "content_type": content_type,
            "byte_length": byte_length,
            "response_sha256": digest.hexdigest().upper() if kept_path is not None else None,
            "response_file": kept_path.name if kept_path is not None else None,
            "capture_state": capture_state,
            "failure_code": failure_code,
            **validation,
            "automatic_retries": 0,
            "api_key_persisted": False,
            "request_query_persisted": False,
            "parser_attempted": False,
            "candidate_created": False,
            "snapshot_created": False,
            "decision_created": False,
            "operational_eligible": False,
        }
        _write_json(private_root, manifest_path, manifest)
    if propagate is not None:
        raise propagate
    return manifest


def collect_approved_alpha_evidence(
    *,
    project_root: str | Path,
    api_key: str,
    run_id: str,
    personal_noncommercial_confirmed: bool,
    max_calls: int = MAX_CALLS,
    session: requests.Session | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Execute at most five actual HTTP requests and preserve raw evidence."""

    if not personal_noncommercial_confirmed:
        raise OfflineCollectorError("personal non-commercial Alpha Vantage use must be confirmed")
    if not api_key.strip():
        raise OfflineCollectorError("ALPHAVANTAGE_API_KEY is required before any request")
    if max_calls != MAX_HTTP_REQUESTS or len(REQUEST_PLAN) != MAX_CALLS:
        raise OfflineCollectorError("the approved budget must be exactly five actual HTTP requests")
    if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in run_id):
        raise OfflineCollectorError("run_id must use only letters, digits, hyphen, or underscore")
    for row in REQUEST_PLAN:
        _validate_request(row)
    private_root = resolve_repository_private_data(project_root, require_exists=True)
    effective_clock = clock or (lambda: datetime.now(timezone.utc))
    if now is not None and now.tzinfo is None:
        raise OfflineCollectorError("run start timestamp must be timezone-aware")
    started_at = now.astimezone(timezone.utc) if now else _utc(effective_clock)
    started_text = _iso(started_at)
    paths = _planned_paths(private_root, run_id)
    _validate_all_output_paths(private_root, paths)
    _safe_mkdir(private_root, paths["provider_root"], label="provider directory")
    _validate_all_output_paths(private_root, paths)
    client = session or requests.Session()
    report: dict[str, Any] = {
        "collector_version": COLLECTOR_VERSION,
        "run_id": run_id,
        "started_at": started_text,
        "finished_at": None,
        "logical_operation_count": len(REQUEST_PLAN),
        "approved_actual_http_request_budget": MAX_HTTP_REQUESTS,
        "calls_attempted": 0,
        "calls_completed": 0,
        "automatic_retries": 0,
        "run_state": "initializing",
        "failure_code": None,
        "active_request_id": None,
        "results": [],
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    with _ExclusiveCollectorLock(private_root, paths["lock"], run_id, started_text):
        _validate_all_output_paths(private_root, paths)
        _preflight_output_paths(paths)
        _safe_mkdir(private_root, paths["run_root"], label="run directory")
        _safe_mkdir(private_root, paths["rights_root"], label="rights directory")
        _validate_all_output_paths(private_root, paths)
        _create_json_exclusive(private_root, paths["run_ledger"], report)

        def persist() -> None:
            _write_json(private_root, paths["run_ledger"], report)

        def before_http(request_id: str, attempted_at: str) -> None:
            if report["calls_attempted"] >= MAX_HTTP_REQUESTS:
                raise OfflineCollectorError("actual HTTP request budget exhausted")
            report["calls_attempted"] += 1
            report["active_request_id"] = request_id
            report["last_attempted_at"] = attempted_at
            report["run_state"] = "running"
            persist()

        def after_http_completed() -> None:
            report["calls_completed"] += 1
            persist()

        try:
            for row in REQUEST_PLAN:
                result = _capture_one(
                    row,
                    collector_version=COLLECTOR_VERSION,
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
                if row.kind == "terms" and result.get("valid_provider_dataset") is not True:
                    report["run_state"] = "failed"
                    report["failure_code"] = "terms_validation_failed"
                    break
            if report["run_state"] != "failed":
                failed_results = [row for row in report["results"] if row.get("capture_state") != "captured"]
                if failed_results:
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

        terms = next((row for row in report["results"] if row["request_id"] == "alpha-vantage-terms"), None)
        if terms and terms.get("valid_provider_dataset") is True:
            terms_manifest = paths["requests"]["alpha-vantage-terms"]["manifest"]
            rights = {
                "schema_version": 2,
                "provider": "Alpha Vantage",
                "run_id": run_id,
                "terms_url": TERMS_URL,
                "terms_source_hash": terms.get("response_sha256"),
                "terms_capture_manifest_hash": hashlib.sha256(terms_manifest.read_bytes()).hexdigest().upper(),
                "reviewed_by": None,
                "reviewed_at": None,
                "review_scope": None,
                "review_result": "manual_review_required",
                "personal_noncommercial_confirmed": True,
                "local_storage_permission": "manual_review_required",
                "derived_analysis_permission": "manual_review_required",
                "local_display_permission": "manual_review_required",
                "public_display_permission": "not_authorized",
                "expires_at": None,
                "linked_response_hashes": [
                    row["response_sha256"] for row in report["results"] if row.get("response_sha256")
                ],
                "parser_candidate": False,
                "operational_eligible": False,
            }
            try:
                _create_json_exclusive(private_root, paths["rights"], rights)
            except BaseException as exc:
                report["run_state"] = "failed"
                report["failure_code"] = _failure_code(exc)
                report["finished_at"] = _iso(_utc(effective_clock))
                persist()
                raise
    return report


def collection_report_is_complete(report: dict[str, Any]) -> bool:
    """Return true only for a fully completed and validated five-operation run."""

    expected_ids = [row.request_id for row in REQUEST_PLAN]
    results = report.get("results")
    if not isinstance(results, list):
        return False
    if report.get("run_state") != "completed" or report.get("failure_code") is not None:
        return False
    if report.get("active_request_id") is not None:
        return False
    if report.get("logical_operation_count") != len(expected_ids):
        return False
    if report.get("approved_actual_http_request_budget") != MAX_HTTP_REQUESTS:
        return False
    if report.get("calls_attempted") != MAX_HTTP_REQUESTS:
        return False
    if report.get("calls_completed") != MAX_HTTP_REQUESTS:
        return False
    if len(results) != len(expected_ids):
        return False
    if [row.get("request_id") for row in results if isinstance(row, dict)] != expected_ids:
        return False
    return all(
        isinstance(row, dict)
        and row.get("capture_state") == "captured"
        and row.get("valid_provider_dataset") is True
        and row.get("failure_code") is None
        for row in results
    )


def api_key_from_environment() -> str:
    """Read the secret without printing or persisting it."""

    return os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
