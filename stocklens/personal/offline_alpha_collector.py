"""Fail-closed Alpha Vantage evidence collector for an approved local run.

The module deliberately does not parse, normalize, promote, or calculate an
investment decision.  It only preserves provider response bytes and a
redacted capture manifest under the repository's ignored ``private_data``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests

from .evidence_inventory import EvidenceInventoryError, resolve_repository_private_data


COLLECTOR_VERSION = "alpha-vantage-offline-evidence-v1"
API_ENDPOINT = "https://www.alphavantage.co/query"
TERMS_URL = "https://www.alphavantage.co/terms_of_service/"
ALLOWED_HOSTS = frozenset({"www.alphavantage.co", "alphavantage.co"})
ALLOWED_TICKERS = frozenset({"AVGO", "NVDA"})
ALLOWED_FUNCTIONS = frozenset({"TIME_SERIES_DAILY", "EARNINGS_ESTIMATES"})
MAX_CALLS = 5


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


def exact_request_plan() -> list[dict[str, Any]]:
    """Return the immutable, secret-free five-request allowlist."""

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
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise OfflineCollectorError("provider redirected outside the approved Alpha Vantage domain")
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


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
        return "terms-of-service.html"
    return f"{row.ticker}_{row.function}.response.raw"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _response_chunks(response: Any) -> Iterable[bytes]:
    if hasattr(response, "iter_content"):
        yield from response.iter_content(chunk_size=64 * 1024)
        return
    content = getattr(response, "content", b"")
    if content:
        yield bytes(content)


def _capture_one(
    row: EvidenceRequest,
    *,
    raw_root: Path,
    api_key: str,
    session: requests.Session,
    captured_at: datetime,
) -> dict[str, Any]:
    _validate_request(row)
    raw_path = raw_root / _raw_name(row)
    partial_path = raw_path.with_suffix(raw_path.suffix + ".partial")
    manifest_path = raw_path.with_suffix(raw_path.suffix + ".manifest.json")
    if raw_path.exists() or partial_path.exists() or manifest_path.exists():
        raise OfflineCollectorError(f"duplicate collection target exists for {row.request_id}")
    params = None
    if row.kind == "dataset":
        if not api_key:
            raise OfflineCollectorError("ALPHAVANTAGE_API_KEY is required for dataset requests")
        params = {"function": row.function, "symbol": row.ticker, "apikey": api_key}
        if row.function == "TIME_SERIES_DAILY":
            params["outputsize"] = "compact"
    raw_root.mkdir(parents=True, exist_ok=True)
    response: Any | None = None
    status_code: int | None = None
    content_type: str | None = None
    final_url: str | None = None
    digest = hashlib.sha256()
    byte_length = 0
    state = "failed"
    failure_code: str | None = None
    try:
        response = session.get(row.url, params=params, timeout=30, allow_redirects=True, stream=True)
        status_code = int(response.status_code)
        final_url = _safe_url(str(getattr(response, "url", row.url)))
        content_type = str(getattr(response, "headers", {}).get("Content-Type") or "") or None
        with partial_path.open("xb") as handle:
            for chunk in _response_chunks(response):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                byte_length += len(chunk)
        partial_path.replace(raw_path)
        state = "captured" if status_code == 200 else "http_error"
        failure_code = None if status_code == 200 else f"http_{status_code}"
    except KeyboardInterrupt:
        failure_code = "interrupted"
        raise
    except requests.exceptions.Timeout:
        failure_code = "timeout"
    except requests.exceptions.SSLError:
        failure_code = "tls_error"
    except requests.exceptions.ConnectionError:
        failure_code = "connection_error"
    except requests.exceptions.RequestException:
        failure_code = "request_error"
    except OSError:
        failure_code = "local_io_error"
    finally:
        if response is not None and hasattr(response, "close"):
            response.close()
        kept_path = raw_path if raw_path.exists() else partial_path if partial_path.exists() else None
        if kept_path is not None and byte_length == 0:
            payload = kept_path.read_bytes()
            digest = hashlib.sha256(payload)
            byte_length = len(payload)
        manifest = {
            "collector_version": COLLECTOR_VERSION,
            "request_id": row.request_id,
            "provider": "Alpha Vantage",
            "request_host": urlsplit(row.url).hostname,
            "request_path": urlsplit(row.url).path,
            "function": row.function,
            "ticker": row.ticker,
            "captured_at": captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "http_status": status_code,
            "final_url": final_url,
            "content_type": content_type,
            "byte_length": byte_length,
            "response_sha256": digest.hexdigest().upper() if kept_path is not None else None,
            "response_file": kept_path.name if kept_path is not None else None,
            "capture_state": state,
            "failure_code": failure_code,
            "automatic_retries": 0,
            "api_key_persisted": False,
            "request_query_persisted": False,
            "parser_attempted": False,
            "candidate_created": False,
            "snapshot_created": False,
            "decision_created": False,
            "operational_eligible": False,
        }
        _write_json(manifest_path, manifest)
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
) -> dict[str, Any]:
    """Execute the exact five-call plan once and preserve only raw evidence."""

    if not personal_noncommercial_confirmed:
        raise OfflineCollectorError("personal non-commercial Alpha Vantage use must be confirmed")
    if not api_key.strip():
        raise OfflineCollectorError("ALPHAVANTAGE_API_KEY is required before any request")
    if max_calls != MAX_CALLS or len(REQUEST_PLAN) != MAX_CALLS:
        raise OfflineCollectorError("the approved collection budget must be exactly five calls")
    if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in run_id):
        raise OfflineCollectorError("run_id must use only letters, digits, hyphen, or underscore")
    private_root = resolve_repository_private_data(project_root, require_exists=False)
    raw_root = private_root / "raw" / "alpha_vantage"
    run_path = raw_root / f"collection-run-{run_id}.json"
    if run_path.exists():
        raise OfflineCollectorError("duplicate collection run_id is blocked")
    for row in REQUEST_PLAN:
        _validate_request(row)
    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    client = session or requests.Session()
    results: list[dict[str, Any]] = []
    calls_made = 0
    for row in REQUEST_PLAN:
        result = _capture_one(
            row,
            raw_root=raw_root,
            api_key=api_key,
            session=client,
            captured_at=captured_at,
        )
        calls_made += 1
        results.append(result)
    terms = next(row for row in results if row["request_id"] == "alpha-vantage-terms")
    rights = {
        "schema_version": 1,
        "provider": "Alpha Vantage",
        "terms_url": TERMS_URL,
        "terms_source_hash": terms.get("response_sha256"),
        "terms_capture_manifest_hash": hashlib.sha256(
            (raw_root / "terms-of-service.html.manifest.json").read_bytes()
        ).hexdigest().upper(),
        "reviewed_at": captured_at.date().isoformat(),
        "personal_noncommercial_confirmed": True,
        "local_storage_permission": "manual_review_required",
        "derived_analysis_permission": "manual_review_required",
        "local_display_permission": "manual_review_required",
        "public_display_permission": "not_authorized",
        "expires_at": None,
        "linked_response_hashes": [row["response_sha256"] for row in results if row.get("response_sha256")],
        "parser_candidate": False,
        "operational_eligible": False,
    }
    _write_json(private_root / "rights" / "provider-terms-manifest.json", rights)
    run_report = {
        "collector_version": COLLECTOR_VERSION,
        "run_id": run_id,
        "started_at": captured_at.isoformat().replace("+00:00", "Z"),
        "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "approved_call_budget": MAX_CALLS,
        "calls_made": calls_made,
        "automatic_retries": 0,
        "results": results,
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    _write_json(run_path, run_report)
    return run_report


def api_key_from_environment() -> str:
    """Read the secret without printing or persisting it."""

    return os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
