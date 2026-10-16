"""Fail-closed, read-only inventory for Same-Date P4.1 local evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import urlsplit

from .alpha_evidence_validation import validate_dataset_bytes, validate_terms_bytes

INVENTORY_VERSION = "same-date-evidence-inventory-p4.2.1-v1"
MANIFEST_VERSION = "evidence-inventory-manifest-v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "config" / "evidence-inventory-manifest-v1.json"
DEFAULT_OUTPUT_NAME = "evidence-inventory-p4.json"
TARGET_TICKERS = ("AVGO", "NVDA")
EVIDENCE_CLASSES = {
    "normalized_only", "derived_only", "supporting_only", "missing",
    "unclassified", "invalid_integrity", "malformed", "unreadable",
    "discovered_unverified", "parser_candidate",
}
OFFICIAL_ISSUER_DOMAINS = {
    "AVGO": frozenset({"investors.broadcom.com", "broadcom.com", "www.broadcom.com"}),
    "NVDA": frozenset({"investor.nvidia.com", "nvidia.com", "www.nvidia.com"}),
}


class EvidenceInventoryError(ValueError):
    """Raised when the inventory cannot safely inspect or write evidence."""


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _relative_manifest_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise EvidenceInventoryError(f"{label} must be a non-empty portable relative path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise EvidenceInventoryError(f"{label} must not be absolute, drive-prefixed, or contain '..'")
    if any(part in {"", "."} for part in posix.parts):
        raise EvidenceInventoryError(f"{label} contains an invalid path segment")
    return posix.as_posix()


def _resolved_child(root: Path, relative: str, *, label: str) -> Path:
    base = root.resolve(strict=True)
    candidate = root / relative
    resolved = candidate.resolve(strict=candidate.exists())
    if not _is_within(resolved, base):
        raise EvidenceInventoryError(f"{label} resolves outside its declared root")
    return candidate


def resolve_repository_private_data(
    project_root: str | Path = REPOSITORY_ROOT, *, require_exists: bool = True,
) -> Path:
    """Resolve only ``<verified project root>/private_data`` and fail closed."""

    root = Path(project_root).resolve(strict=True)
    if root != REPOSITORY_ROOT.resolve(strict=True) and not (root / ".git").exists():
        raise EvidenceInventoryError("project_root is not a verified Git repository")
    private = root / "private_data"
    if private.name != "private_data" or private.parent.resolve(strict=True) != root:
        raise EvidenceInventoryError("private_data root must be fixed directly under the repository root")
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        raise EvidenceInventoryError("repository .gitignore is missing")
    ignored = {
        line.strip().lstrip("/")
        for line in gitignore.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if "private_data/" not in ignored and "private_data" not in ignored:
        raise EvidenceInventoryError("private_data is not covered by repository .gitignore")
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", "private_data"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceInventoryError("unable to verify that private_data is untracked") from exc
    if tracked:
        raise EvidenceInventoryError("private_data contains Git-tracked paths")
    if require_exists and not private.is_dir():
        raise EvidenceInventoryError("repository private_data directory is missing")
    if private.exists() and not _is_within(private.resolve(strict=True), root):
        raise EvidenceInventoryError("private_data resolves outside the repository")
    return private


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise EvidenceInventoryError(f"unreadable JSON: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceInventoryError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise EvidenceInventoryError(f"JSON root must be an object: {path}")
    return value


def _parse_timestamp(value: Any) -> tuple[str | None, str | None, datetime | None]:
    if value in (None, ""):
        return None, None, None
    if not isinstance(value, str):
        return None, "invalid_timestamp", None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid_timestamp", None
    if parsed.tzinfo is None:
        return None, "timezone_missing", None
    utc = parsed.astimezone(timezone.utc)
    return utc.isoformat().replace("+00:00", "Z"), None, utc


def _load_manifest(path: Path) -> dict[str, Any]:
    manifest = _read_json(path)
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise EvidenceInventoryError("unsupported evidence inventory manifest version")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise EvidenceInventoryError("inventory manifest artifacts must be a non-empty list")
    ids: set[str] = set()
    paths: set[tuple[str, str]] = set()
    for row in artifacts:
        if not isinstance(row, dict):
            raise EvidenceInventoryError("inventory manifest artifact must be an object")
        artifact_id = row.get("artifact_id")
        relative = _relative_manifest_path(row.get("path"), label=f"artifact {artifact_id} path")
        row["path"] = relative
        key = (str(row.get("root")), relative)
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in ids:
            raise EvidenceInventoryError("inventory manifest artifact_id is missing or duplicated")
        if key in paths or row.get("root") not in {"private_data", "top10_data"}:
            raise EvidenceInventoryError("inventory manifest path is duplicated or has an invalid root")
        if row.get("classification") not in EVIDENCE_CLASSES:
            raise EvidenceInventoryError("inventory manifest has an invalid classification")
        ids.add(artifact_id)
        paths.add(key)
    exclusion_paths: set[str] = set()
    exclusions = manifest.get("known_exclusions", [])
    if not isinstance(exclusions, list):
        raise EvidenceInventoryError("known_exclusions must be a list")
    for index, row in enumerate(exclusions):
        if not isinstance(row, dict):
            raise EvidenceInventoryError("known exclusion must be an object")
        relative = _relative_manifest_path(row.get("path"), label=f"known_exclusions[{index}].path")
        if relative in exclusion_paths:
            raise EvidenceInventoryError("known exclusion path is duplicated")
        row["path"] = relative
        exclusion_paths.add(relative)
    return manifest


def _file_record(path: Path, *, role: str, source: str) -> dict[str, Any]:
    return {"role": role, "path": str(path.resolve()), "source": source,
            "source_hash": _sha256(path), "bytes": path.stat().st_size}


def _inspect_artifact(
    spec: dict[str, Any], *, roots: dict[str, Path], inspected_dt: datetime,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    path = _resolved_child(
        roots[str(spec["root"])], str(spec["path"]), label=f"artifact {spec['artifact_id']}",
    )
    base = {
        "artifact_id": spec["artifact_id"], "root": spec["root"],
        "relative_path": spec["path"], "path": str(path.resolve(strict=False)),
        "expected_format": spec["format"], "purpose": spec["purpose"],
        "required": bool(spec.get("required")),
        "declared_classification": spec["classification"],
        "provider_native_raw_exists": False, "parser_eligible": False,
        "promotion_eligible": False,
    }
    if not path.is_file():
        return {**base, "exists": False, "inspection_status": "missing",
                "classification": "missing", "source_hash": None,
                "reason": "declared artifact is not present"}, None
    actual_format = path.suffix.lower().lstrip(".")
    if actual_format != spec["format"]:
        return {**base, "exists": True, "inspection_status": "malformed",
                "classification": "malformed", "source_hash": _sha256(path),
                "reason": "file extension does not match the declared format"}, None
    try:
        source_hash = _sha256(path)
        rows: list[dict[str, str]] = []
        if actual_format == "json":
            value = _read_json(path)
            generated_raw = value.get("generatedAt") or value.get("captured_at")
        elif actual_format == "csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            value, generated_raw = None, None
        elif actual_format in {"raw", "html"}:
            path.read_bytes()
            value, generated_raw = None, None
        else:
            raise EvidenceInventoryError(f"unsupported declared format: {actual_format}")
    except EvidenceInventoryError as exc:
        status = "unreadable" if str(exc).startswith("unreadable") else "malformed"
        return {**base, "exists": True, "inspection_status": status,
                "classification": status, "source_hash": _sha256(path),
                "reason": str(exc)}, None
    except OSError as exc:
        return {**base, "exists": True, "inspection_status": "unreadable",
                "classification": "unreadable", "source_hash": None,
                "reason": f"unreadable file: {exc.__class__.__name__}"}, None
    generated_at, timestamp_error, generated_dt = _parse_timestamp(generated_raw)
    temporal_status, classification = "not_declared", str(spec["classification"])
    reason = "declared local artifact inspected; no promotion is performed"
    if timestamp_error:
        temporal_status, classification = timestamp_error, "malformed"
        reason = "declared timestamp is invalid or timezone-less"
    elif generated_dt is not None and generated_dt > inspected_dt:
        temporal_status, classification = "future_timestamp", "malformed"
        reason = "artifact timestamp is later than inspected_at"
    elif generated_dt is not None:
        temporal_status = "valid"
    record = {
        **base, "exists": True,
        "inspection_status": temporal_status if temporal_status in {
            "future_timestamp", "timezone_missing", "invalid_timestamp"
        } else "inspected",
        "classification": classification, "source_hash": source_hash,
        "bytes": path.stat().st_size, "generated_at": generated_at,
        "temporal_status": temporal_status, "reason": reason,
    }
    if actual_format == "csv":
        dates = [str(row.get("date")) for row in rows if row.get("date")]
        record.update({"rows": len(rows), "first_date": min(dates) if dates else None,
                       "last_date": max(dates) if dates else None})
    return record, value


def _target_rows(value: Any, ticker: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict) and row.get("ticker") == ticker]
    if isinstance(value, dict):
        direct = value.get(ticker)
        if isinstance(direct, list):
            return [row for row in direct if isinstance(row, dict)]
        if isinstance(direct, dict):
            return [direct]
        return [row for row in value.values() if isinstance(row, dict) and row.get("ticker") == ticker]
    return []


def _count_acceptance_timestamps(value: Any) -> int:
    names = {"accepted", "acceptedat", "acceptance", "acceptancedatetime", "acceptance_datetime"}
    if isinstance(value, dict):
        return sum((1 if str(key).lower() in names and _parse_timestamp(item)[0] else 0)
                   + _count_acceptance_timestamps(item) for key, item in value.items())
    if isinstance(value, list):
        return sum(_count_acceptance_timestamps(item) for item in value)
    return 0


def _add_details(artifact: dict[str, Any], value: dict[str, Any] | None) -> None:
    if value is None or artifact["classification"] in {"malformed", "unreadable"}:
        return
    artifact_id = artifact["artifact_id"]
    if artifact_id == "top10-consensus-history":
        observations = value.get("observations")
        artifact["target_records"] = {ticker: len(_target_rows(observations, ticker)) for ticker in TARGET_TICKERS}
        artifact["accounting_basis"] = {
            ticker: sorted({str(row.get("accountingBasis")) for row in _target_rows(observations, ticker)
                            if row.get("accountingBasis")}) for ticker in TARGET_TICKERS
        }
    elif artifact_id == "top10-valuation-history":
        records = value.get("records")
        detail: dict[str, Any] = {}
        for ticker in TARGET_TICKERS:
            rows = _target_rows(records, ticker)
            latest = rows[-1] if rows else {}
            detail[ticker] = {"rows": len(rows),
                              "strict_eligible_rows": sum(row.get("strictEligible") is True for row in rows),
                              "latest_point_in_time_verified": latest.get("pointInTimeVerified")}
        artifact["target_records"] = detail
    elif artifact_id == "top10-guidance-events":
        artifact["target_records"] = {
            ticker: len(_target_rows(value.get("events"), ticker)) for ticker in TARGET_TICKERS
        }
    elif artifact_id == "top10-sec-quarterly":
        records = value.get("records")
        detail = {}
        for ticker in TARGET_TICKERS:
            rows = _target_rows(records, ticker)
            detail[ticker] = {
                "records": len(rows),
                "records_with_acceptance_timestamp": sum(_count_acceptance_timestamps(row) > 0 for row in rows),
                "acceptance_timestamp_count": sum(_count_acceptance_timestamps(row) for row in rows),
            }
        artifact["target_records"] = detail


def _records_for_rule(value: dict[str, Any], container: str) -> list[dict[str, Any]]:
    current: Any = value.get(container)
    if isinstance(current, list):
        return [row for row in current if isinstance(row, dict)]
    if isinstance(current, dict):
        rows: list[dict[str, Any]] = []
        for ticker, item in current.items():
            values = item if isinstance(item, list) else [item]
            for row in values:
                if isinstance(row, dict):
                    rows.append({"ticker": row.get("ticker") or ticker, **row})
        return rows
    return []


def _apply_record_timestamp_rules(
    artifact: dict[str, Any],
    value: dict[str, Any] | None,
    spec: dict[str, Any],
    *,
    cutoff_dt: datetime,
) -> None:
    rules = spec.get("timestamp_rules")
    if value is None or not isinstance(rules, dict):
        return
    fields = rules.get("fields")
    container = rules.get("container")
    if not isinstance(fields, list) or not fields or not isinstance(container, str):
        raise EvidenceInventoryError(f"artifact {spec['artifact_id']} has invalid timestamp_rules")
    rows = _records_for_rule(value, container)
    invalid = 0
    future = 0
    missing = 0
    affected: set[str] = set()
    target_rows = 0
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if ticker and ticker not in TARGET_TICKERS:
            continue
        target_rows += 1
        row_valid = False
        row_invalid = False
        for field in fields:
            raw = row.get(str(field))
            if raw in (None, ""):
                continue
            row_valid = True
            _, error, parsed = _parse_timestamp(raw)
            if error or parsed is None:
                invalid += 1
                row_invalid = True
                break
            if parsed > cutoff_dt:
                future += 1
                row_invalid = True
                break
        if not row_valid:
            missing += 1
            row_invalid = True
        if row_invalid:
            affected.add(ticker or "unknown")
    artifact["record_timestamp_validation"] = {
        "container": container,
        "required_fields_any_of": fields,
        "decision_cutoff": cutoff_dt.isoformat().replace("+00:00", "Z"),
        "target_record_count": target_rows,
        "invalid_timestamp_count": invalid,
        "future_timestamp_count": future,
        "missing_timestamp_count": missing,
        "affected_tickers": sorted(affected),
    }
    if spec.get("operational_input_candidate") is True and (invalid or future or missing):
        artifact.update({
            "classification": "malformed",
            "inspection_status": "record_timestamp_invalid",
            "promotion_eligible": False,
            "parser_eligible": False,
            "reason": "operational input candidate contains missing, invalid, or future record timestamps",
        })


def _evidence_source_set(
    *, private_root: Path, top10_root: Path, manifest_path: Path, manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """Hash every file that can affect the inventory, except the inventory output itself."""

    private_base = private_root.resolve(strict=True)
    top10_base = top10_root.resolve(strict=True)
    candidates: dict[Path, str] = {manifest_path.resolve(strict=True): "config_manifest"}
    for path in top10_root.rglob("*"):
        if path.is_file():
            resolved = path.resolve(strict=True)
            if not _is_within(resolved, top10_base):
                raise EvidenceInventoryError("Top10 source file resolves outside the declared root")
            candidates[resolved] = f"top10_data/{path.relative_to(top10_root).as_posix()}"
    for spec in manifest["artifacts"]:
        if spec["root"] != "private_data":
            continue
        path = _resolved_child(private_root, str(spec["path"]), label=f"artifact {spec['artifact_id']}")
        if path.is_file():
            candidates[path.resolve(strict=True)] = f"private_data/{path.relative_to(private_root).as_posix()}"
    for relative_root in (Path("raw"), Path("rights"), Path("normalized/financials"), Path("decision")):
        directory = private_root / relative_root
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if not _is_within(resolved, private_base):
                raise EvidenceInventoryError("private evidence source resolves outside private_data")
            candidates[resolved] = f"private_data/{path.relative_to(private_root).as_posix()}"
    output = (private_root / "decision" / DEFAULT_OUTPUT_NAME).resolve(strict=False)
    temporary = output.with_suffix(output.suffix + ".tmp")
    candidates.pop(output, None)
    candidates.pop(temporary, None)
    rows = [
        {"relative_path": label, "bytes": path.stat().st_size, "source_hash": _sha256(path)}
        for path, label in sorted(candidates.items(), key=lambda item: item[1])
    ]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return rows, hashlib.sha256(payload).hexdigest().upper()


def validate_inventory_source_set(
    report: dict[str, Any],
    top10_data_root: str | Path,
    private_data_root: str | Path | None = None,
    inventory_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Recompute every source byte used by a persisted inventory."""

    private_root = Path(private_data_root or report.get("private_data_root") or "")
    manifest_path = Path(inventory_manifest_path or report.get("manifest_path") or DEFAULT_MANIFEST)
    if not private_root.is_dir():
        raise EvidenceInventoryError("private_data root is required to validate the complete source set")
    manifest = _load_manifest(manifest_path)
    rows, digest = _evidence_source_set(
        private_root=private_root,
        top10_root=Path(top10_data_root),
        manifest_path=manifest_path,
        manifest=manifest,
    )
    expected = str(report.get("source_set_hash") or "").upper()
    stale = not expected or digest != expected
    return {
        "inventory_status": "stale" if stale else "current",
        "source_set_hash": digest,
        "source_set_matches": not stale,
        "source_set": rows,
    }


def _apply_alpha_integrity(
    artifacts: list[dict[str, Any]], values: dict[str, dict[str, Any] | None], inspected_dt: datetime,
) -> None:
    by_id = {row["artifact_id"]: row for row in artifacts}
    for ticker in TARGET_TICKERS:
        data = by_id[f"alpha-{ticker.lower()}-daily-data"]
        manifest = by_id[f"alpha-{ticker.lower()}-daily-manifest"]
        value = values.get(manifest["artifact_id"])
        if not data["exists"] or not manifest["exists"] or not isinstance(value, dict):
            continue
        declared = str(value.get("sha256") or "").upper()
        actual = str(data.get("source_hash") or "").upper()
        captured_at, timestamp_error, captured_dt = _parse_timestamp(value.get("collected_at_utc"))
        integrity_ok = len(declared) == 64 and declared == actual
        data.update({"manifest_hash_matches_data": integrity_ok, "captured_at": captured_at,
                     "provider_date": value.get("provider_market_date"),
                     "adjusted": bool(value.get("adjusted_prices_available"))})
        if not integrity_ok:
            for row in (data, manifest):
                row.update({"classification": "invalid_integrity", "inspection_status": "invalid_integrity",
                            "reason": "manifest SHA-256 does not match current CSV bytes"})
        elif timestamp_error:
            manifest.update({"classification": "malformed", "inspection_status": timestamp_error,
                             "reason": "capture timestamp is invalid or timezone-less"})
        elif captured_dt is not None and captured_dt > inspected_dt:
            manifest.update({"classification": "malformed", "inspection_status": "future_timestamp",
                             "reason": "capture timestamp is later than inspected_at"})


def _apply_provider_native_integrity(
    artifacts: list[dict[str, Any]],
    values: dict[str, dict[str, Any] | None],
    specs: list[dict[str, Any]],
    *,
    cutoff_dt: datetime,
) -> None:
    by_id = {row["artifact_id"]: row for row in artifacts}
    rights_value = values.get("usage-rights-manifest")
    linked_hashes = {
        str(value).upper()
        for value in (rights_value or {}).get("linked_response_hashes", [])
        if isinstance(value, str)
    }
    terms_raw = by_id.get("alpha-terms-raw", {})
    terms_capture = by_id.get("alpha-terms-capture", {})
    reviewed_at, reviewed_error, reviewed_dt = _parse_timestamp(
        (rights_value or {}).get("reviewed_at") if isinstance(rights_value, dict) else None
    )
    expires_at, expires_error, expires_dt = _parse_timestamp(
        (rights_value or {}).get("expires_at") if isinstance(rights_value, dict) else None
    )
    scopes = (rights_value or {}).get("review_scope") if isinstance(rights_value, dict) else None
    required_scopes = {"local_storage", "derived_analysis", "local_display"}
    rights_checks = {
        "manifest_exists": isinstance(rights_value, dict),
        "terms_bytes_exist": terms_raw.get("exists") is True,
        "terms_capture_exists": terms_capture.get("exists") is True,
        "terms_source_hash": bool(
            isinstance(rights_value, dict)
            and str(rights_value.get("terms_source_hash") or "").upper()
            == str(terms_raw.get("source_hash") or "").upper()
        ),
        "terms_capture_manifest_hash": bool(
            isinstance(rights_value, dict)
            and str(rights_value.get("terms_capture_manifest_hash") or "").upper()
            == str(terms_capture.get("source_hash") or "").upper()
        ),
        "reviewed_by": bool(isinstance(rights_value, dict) and str(rights_value.get("reviewed_by") or "").strip()),
        "reviewed_at": not reviewed_error and reviewed_dt is not None and reviewed_dt <= cutoff_dt,
        "review_scope": isinstance(scopes, list) and required_scopes.issubset(set(scopes)),
        "review_result": bool(isinstance(rights_value, dict) and rights_value.get("review_result") == "approved"),
        "not_expired": not expires_error and expires_dt is not None and expires_dt > cutoff_dt,
        "personal_noncommercial": bool(
            isinstance(rights_value, dict) and rights_value.get("personal_noncommercial_confirmed") is True
        ),
        "local_storage": bool(
            isinstance(rights_value, dict) and rights_value.get("local_storage_permission") == "allowed"
        ),
        "derived_analysis": bool(
            isinstance(rights_value, dict) and rights_value.get("derived_analysis_permission") == "allowed"
        ),
        "local_display": bool(
            isinstance(rights_value, dict) and rights_value.get("local_display_permission") == "allowed"
        ),
    }
    rights_allowed = all(rights_checks.values())
    for raw_spec in specs:
        native = raw_spec.get("provider_native")
        if not isinstance(native, dict) or native.get("kind") != "response_bytes":
            continue
        raw = by_id[raw_spec["artifact_id"]]
        if not raw.get("exists"):
            continue
        raw.update({
            "classification": "discovered_unverified",
            "inspection_status": "discovered_unverified",
            "provider_native_raw_exists": True,
            "parser_eligible": False,
            "promotion_eligible": False,
            "target_roles": list(native.get("target_roles") or []),
        })
        reasons: list[str] = []
        capture_id = str(native.get("capture_manifest_artifact_id") or "")
        capture = by_id.get(capture_id)
        capture_value = values.get(capture_id)
        if capture is None or not capture.get("exists") or not isinstance(capture_value, dict):
            reasons.append("capture manifest is missing")
            raw["candidate_checks"] = {"capture_manifest": False}
            raw["reason"] = "; ".join(reasons)
            continue
        actual_hash = str(raw.get("source_hash") or "").upper()
        declared_hash = str(capture_value.get("response_sha256") or "").upper()
        hash_ok = len(actual_hash) == 64 and actual_hash == declared_hash
        length_ok = capture_value.get("byte_length") == raw.get("bytes")
        completed_at, timestamp_error, completed_dt = _parse_timestamp(
            capture_value.get("response_completed_at") or capture_value.get("available_at")
        )
        started_at, started_error, started_dt = _parse_timestamp(capture_value.get("request_started_at"))
        headers_at, headers_error, headers_dt = _parse_timestamp(capture_value.get("headers_received_at"))
        timestamp_ok = bool(
            not timestamp_error and not started_error and not headers_error
            and completed_dt is not None and started_dt is not None and headers_dt is not None
            and started_dt <= headers_dt <= completed_dt <= cutoff_dt
        )
        try:
            final_url = str(capture_value.get("final_url") or "")
            parsed = urlsplit(final_url)
            url_ok = parsed.scheme == "https" and (parsed.hostname or "").lower() in {
                "alphavantage.co", "www.alphavantage.co"
            } and not parsed.query
        except ValueError:
            url_ok = False
        request_ok = (
            capture_value.get("provider") == "Alpha Vantage"
            and capture_value.get("function") == native.get("function")
            and capture_value.get("ticker") == native.get("ticker")
            and capture_value.get("http_status") == 200
            and capture_value.get("capture_state") == "captured"
        )
        content_type = str(capture_value.get("content_type") or "")
        if native.get("function") is None:
            dataset_validation = validate_terms_bytes(Path(raw["path"]).read_bytes(), content_type)
        else:
            dataset_validation = validate_dataset_bytes(
                Path(raw["path"]).read_bytes(),
                content_type,
                function=str(native.get("function")),
                ticker=str(native.get("ticker")),
            )
        dataset_ok = bool(
            dataset_validation.get("valid_provider_dataset") is True
            and capture_value.get("valid_provider_dataset") is True
        )
        rights_hash_ok = actual_hash in linked_hashes
        basis = native.get("basis")
        basis_ok = basis in {"raw_as_traded_unadjusted", "terms_text"}
        if basis == "provider_estimate_basis_required":
            basis_ok = (
                capture_value.get("accounting_basis") in {"GAAP", "NON_GAAP"}
                and dataset_validation.get("accounting_basis") in {"GAAP", "NON_GAAP"}
                and capture_value.get("accounting_basis") == dataset_validation.get("accounting_basis")
            )
        checks = {
            "capture_manifest": True,
            "source_hash": hash_ok,
            "byte_length": length_ok,
            "request_response_timestamps": timestamp_ok,
            "approved_final_url": url_ok,
            "request_identity": request_ok,
            "valid_provider_dataset": dataset_ok,
            "rights_hash_link": rights_hash_ok,
            "rights_scope": rights_allowed,
            "basis": basis_ok,
        }
        for label, passed in checks.items():
            if not passed:
                reasons.append(f"{label} check failed")
        raw.update({
            "available_at": completed_at,
            "candidate_checks": checks,
            "provider_dataset_validation": dataset_validation,
            "rights_review_checks": rights_checks,
            "rights_manifest_source_hash": (
                by_id.get("usage-rights-manifest", {}).get("source_hash")
                if isinstance(by_id.get("usage-rights-manifest"), dict) else None
            ),
            "reason": "; ".join(reasons) if reasons else "all parser-candidate checks passed",
        })
        if all(checks.values()):
            raw.update({
                "classification": "parser_candidate",
                "inspection_status": "parser_candidate",
                "parser_eligible": True,
            })
        capture["linked_response_artifact_id"] = raw_spec["artifact_id"]
        capture["linked_response_hash_matches"] = hash_ok


def _issuer_supporting_evidence(private_root: Path, inspected_dt: datetime) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    raw_root = private_root / "raw"
    for ticker in TARGET_TICKERS:
        normalized_path = private_root / "normalized" / "financials" / f"{ticker}.json"
        if not normalized_path.is_file():
            continue
        try:
            normalized = _read_json(normalized_path)
        except EvidenceInventoryError as exc:
            results.append({"ticker": ticker, "classification": "malformed", "reason": str(exc),
                            "provider_native_raw_exists": False})
            continue
        documents = normalized.get("documents")
        if not isinstance(documents, list):
            continue
        for document in documents:
            if not isinstance(document, dict):
                continue
            declared = str(document.get("sha256") or "").upper()
            candidates = list(raw_root.glob(f"{ticker}_*")) if raw_root.is_dir() else []
            matching = [path for path in candidates if _sha256(path) == declared]
            published, timestamp_error, published_dt = _parse_timestamp(document.get("published_at"))
            integrity_ok = len(matching) == 1 and len(declared) == 64
            temporal = timestamp_error or ("future_timestamp" if published_dt and published_dt > inspected_dt else "valid")
            distribution = str(document.get("distribution") or "")
            source_url = str(document.get("source_url") or document.get("url") or "")
            final_url = str(document.get("final_url") or "")
            source_host = (urlsplit(source_url).hostname or "").lower()
            final_host = (urlsplit(final_url).hostname or "").lower()
            allowed = OFFICIAL_ISSUER_DOMAINS[ticker]
            official_verified = bool(
                integrity_ok
                and source_url
                and final_url
                and source_host in allowed
                and final_host in allowed
            )
            source_label = ("AVGO issuer-release supporting evidence distributed by PR Newswire"
                            if ticker == "AVGO" else
                            "NVIDIA official investor-relations evidence" if official_verified else distribution)
            row: dict[str, Any] = {
                "ticker": ticker,
                "classification": "supporting_only" if integrity_ok and temporal == "valid" else
                                  ("invalid_integrity" if not integrity_ok else "malformed"),
                "provider_native_raw_exists": integrity_ok, "source": source_label,
                "official_issuer_origin_independently_verified": official_verified,
                "source_url": source_url or None, "final_url": final_url or None,
                "source_host": source_host or None, "final_host": final_host or None,
                "source_hash": declared or None, "as_of": document.get("period_end"),
                "available_at": published, "temporal_status": temporal,
                "same_date_target_roles_satisfied": [], "promotion_eligible": False,
                "reason": "supporting issuer evidence only; it does not satisfy a Same-Date target role",
            }
            if integrity_ok:
                row["file"] = _file_record(matching[0], role="issuer_financial_raw", source=source_label)
            else:
                row["reason"] = "declared source hash does not identify exactly one current raw file"
            results.append(row)
    return results


def _classify_top10_directory(
    top10_root: Path, manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    declared = {str(row["path"]) for row in manifest["artifacts"] if row["root"] == "top10_data"}
    exclusions = {str(row["path"]): str(row["reason"]) for row in manifest.get("known_exclusions", [])
                  if isinstance(row, dict)}
    unclassified: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    if not top10_root.is_dir():
        return unclassified, excluded
    base = top10_root.resolve(strict=True)
    for path in sorted(item for item in top10_root.iterdir() if item.is_file()):
        if not _is_within(path.resolve(strict=True), base):
            raise EvidenceInventoryError("Top10 source file resolves outside the declared root")
        if path.name in declared:
            continue
        record = {"path": str(path.resolve()), "source_hash": _sha256(path)}
        if path.name in exclusions:
            excluded.append({**record, "reason": exclusions[path.name]})
        else:
            unclassified.append({**record, "classification": "unclassified", "promotion_eligible": False,
                                 "reason": "not declared or explicitly excluded by the inventory manifest"})
    return unclassified, excluded


def _discover_private_provider_artifacts(
    private_root: Path, manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    declared = {
        str(row["path"])
        for row in manifest["artifacts"]
        if row["root"] == "private_data" and str(row["path"]).startswith("raw/")
    }
    results: list[dict[str, Any]] = []
    base = private_root.resolve(strict=True)
    adjudications: dict[str, dict[str, Any]] = {}
    decision_root = private_root / "decision"
    if decision_root.is_dir():
        for path in decision_root.glob("alpha-vantage-adjudication-*.json"):
            value = _read_json(path)
            run_id = str(value.get("run_id") or "")
            if run_id and value.get("adjudication_version") == "alpha-vantage-collection-adjudication-p4.3-v1":
                adjudications[run_id] = {
                    "value": value,
                    "path": path,
                    "source_hash": _sha256(path),
                }
    for relative_root in (Path("raw/alpha_vantage"), Path("raw/nasdaq_consensus")):
        directory = private_root / relative_root
        if not directory.is_dir():
            continue
        if not _is_within(directory.resolve(strict=True), base):
            raise EvidenceInventoryError("provider raw directory resolves outside private_data")
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            resolved = path.resolve(strict=True)
            if not _is_within(resolved, base):
                raise EvidenceInventoryError("provider raw artifact resolves outside private_data")
            relative = path.relative_to(private_root).as_posix()
            if relative in declared:
                continue
            filename = path.name.upper()
            target_roles: list[str] = []
            ticker = next((value for value in TARGET_TICKERS if filename.startswith(f"{value}_")), None)
            if "TIME_SERIES_DAILY.RESPONSE.RAW" in filename:
                target_roles = ["daily_price"]
            elif "EARNINGS_ESTIMATES.RESPONSE.RAW" in filename:
                target_roles = ["forward_eps"]
            is_raw = path.name.lower().endswith(".response.raw")
            classification = "supporting_only" if not is_raw else "discovered_unverified"
            reason = (
                "dynamic provider run metadata; never promoted automatically"
                if not is_raw else
                "provider bytes were discovered but rights and parser eligibility are not approved"
            )
            valid_provider_dataset: bool | None = None
            adjudication_status: str | None = None
            adjudication_hash: str | None = None
            counts_toward_required = False
            if is_raw:
                capture_path = path.with_suffix(path.suffix + ".manifest.json")
                capture = _read_json(capture_path) if capture_path.is_file() else None
                integrity_ok = bool(
                    isinstance(capture, dict)
                    and str(capture.get("response_sha256") or "").upper() == _sha256(path)
                    and capture.get("byte_length") == path.stat().st_size
                )
                valid_provider_dataset = bool(
                    integrity_ok and isinstance(capture, dict)
                    and capture.get("valid_provider_dataset") is True
                    and capture.get("capture_state") == "captured"
                )
                run_id = path.parent.name
                adjudication = adjudications.get(run_id)
                if adjudication:
                    ledger_path = path.parent / "collection-run.json"
                    linked_ledger = (adjudication["value"].get("collection_run") or {})
                    ledger_ok = bool(
                        ledger_path.is_file()
                        and str(linked_ledger.get("source_hash") or "").upper() == _sha256(ledger_path)
                    )
                    matching = [
                        row for row in adjudication["value"].get("requests", [])
                        if isinstance(row, dict)
                        and row.get("source_relative_path") == relative
                        and str(row.get("source_hash") or "").upper() == _sha256(path)
                    ]
                    if ledger_ok and len(matching) == 1:
                        adjudication_status = str(matching[0].get("adjudication") or "")
                        adjudication_hash = adjudication["source_hash"]
                if adjudication_status == "invalid_provider_dataset" or not valid_provider_dataset:
                    classification = "invalid_provider_dataset"
                    reason = "provider response is an invalid dataset; adjudicated restriction is rate-or-plan related"
                else:
                    counts_toward_required = bool(target_roles)
            results.append({
                "relative_path": relative,
                "path": str(resolved),
                "source_hash": _sha256(path),
                "bytes": path.stat().st_size,
                "classification": classification,
                "provider_native_raw_exists": is_raw,
                "ticker": ticker,
                "target_roles": target_roles,
                "valid_provider_dataset": valid_provider_dataset,
                "counts_toward_same_date_required_raw": counts_toward_required,
                "adjudication": adjudication_status,
                "adjudication_source_hash": adjudication_hash,
                "parser_eligible": False,
                "promotion_eligible": False,
                "reason": reason,
            })
    return results


def build_p4_evidence_inventory(
    *, private_data_root: str | Path, top10_data_root: str | Path, inspected_at: str,
    inventory_manifest_path: str | Path = DEFAULT_MANIFEST,
    decision_time: str | None = None,
) -> dict[str, Any]:
    """Inventory exact local evidence without network access or promotion."""

    inspected, error, inspected_dt = _parse_timestamp(inspected_at)
    if error or inspected_dt is None:
        raise EvidenceInventoryError("inspected_at must be a timezone-aware ISO timestamp")
    private_root, top10_root = Path(private_data_root), Path(top10_data_root)
    if private_root.name != "private_data":
        raise EvidenceInventoryError("private_data_root must be named private_data")
    if not private_root.is_dir() or not top10_root.is_dir():
        raise EvidenceInventoryError("private_data and Top10 data roots must exist")
    _, cutoff_error, cutoff_dt = _parse_timestamp(decision_time or inspected_at)
    if cutoff_error or cutoff_dt is None:
        raise EvidenceInventoryError("decision_time must be a timezone-aware ISO timestamp")
    manifest_path = Path(inventory_manifest_path)
    manifest = _load_manifest(manifest_path)
    before_source_set, before_source_hash = _evidence_source_set(
        private_root=private_root,
        top10_root=top10_root,
        manifest_path=manifest_path,
        manifest=manifest,
    )
    roots = {"private_data": private_root, "top10_data": top10_root}
    artifacts: list[dict[str, Any]] = []
    values: dict[str, dict[str, Any] | None] = {}
    for spec in manifest["artifacts"]:
        artifact, value = _inspect_artifact(spec, roots=roots, inspected_dt=inspected_dt)
        _add_details(artifact, value)
        _apply_record_timestamp_rules(artifact, value, spec, cutoff_dt=cutoff_dt)
        artifacts.append(artifact)
        values[artifact["artifact_id"]] = value
    _apply_alpha_integrity(artifacts, values, inspected_dt)
    _apply_provider_native_integrity(
        artifacts, values, manifest["artifacts"], cutoff_dt=cutoff_dt,
    )
    unclassified, excluded = _classify_top10_directory(top10_root, manifest)
    discovered_provider = _discover_private_provider_artifacts(private_root, manifest)
    after_source_set, after_source_hash = _evidence_source_set(
        private_root=private_root,
        top10_root=top10_root,
        manifest_path=manifest_path,
        manifest=manifest,
    )
    source_set_changed = before_source_hash != after_source_hash
    supporting = _issuer_supporting_evidence(private_root, inspected_dt)
    missing = [row for row in artifacts if row["inspection_status"] == "missing" and row["required"]]
    optional_missing = [row for row in artifacts if row["inspection_status"] == "missing" and not row["required"]]
    invalid_states = {"malformed", "unreadable", "invalid_integrity", "future_timestamp",
                      "timezone_missing", "invalid_timestamp", "record_timestamp_invalid"}
    invalid = [row for row in artifacts if row["classification"] in invalid_states
               or row["inspection_status"] in invalid_states]
    declared_target_native = [
        row for row in artifacts
        if row.get("provider_native_raw_exists") is True and row.get("target_roles")
    ]
    discovered_target_native = [
        row for row in discovered_provider
        if row.get("provider_native_raw_exists") is True
        and row.get("target_roles")
        and row.get("counts_toward_same_date_required_raw") is True
    ]
    target_native = [*declared_target_native, *discovered_target_native]
    parser_candidates = [row for row in target_native if row.get("parser_eligible") is True]
    supporting_native = [row for row in supporting if row.get("provider_native_raw_exists") is True]
    discovered_native = [row for row in discovered_provider if row.get("provider_native_raw_exists") is True]
    native_by_path = {
        str(
            row.get("path")
            or row.get("relative_path")
            or ((row.get("file") or {}).get("path") if isinstance(row.get("file"), dict) else "")
            or f"supporting:{row.get('ticker')}:{row.get('source_hash')}"
        ): row
        for row in [*target_native, *supporting_native, *discovered_native]
    }
    native = list(native_by_path.values())
    source_paths = [str(manifest_path.resolve())] + [row["path"] for row in artifacts if row.get("exists")]
    source_paths += [str(row["file"]["path"]) for row in supporting if isinstance(row.get("file"), dict)]
    source_paths += [str(row["path"]) for row in discovered_provider]
    return {
        "inventory_version": INVENTORY_VERSION, "manifest_version": manifest["manifest_version"],
        "manifest_path": str(manifest_path.resolve()), "manifest_hash": _sha256(manifest_path),
        "private_data_root": str(private_root.resolve()),
        "top10_data_root": str(top10_root.resolve()),
        "inspected_at": inspected,
        "decision_time": cutoff_dt.isoformat().replace("+00:00", "Z"),
        "target_tickers": list(TARGET_TICKERS),
        "source_set_hash": after_source_hash,
        "source_set": after_source_set,
        "inventory_status": "stale" if source_set_changed else "current",
        "source_set_changed_during_inspection": source_set_changed,
        "expected_artifact_count": len(artifacts),
        "inspected_artifact_count": sum(row.get("exists") is True for row in artifacts),
        "missing_artifact_count": len(missing), "unclassified_artifact_count": len(unclassified),
        "optional_missing_artifact_count": len(optional_missing),
        "invalid_artifact_count": len(invalid),
        "inventory_complete": (
            not missing and not unclassified and not invalid and not discovered_provider and not source_set_changed
        ),
        "network_capability_present": False, "network_calls_observed_in_this_run": 0,
        "actions_performed": ["read_local_metadata", "hash_local_files", "classify_local_artifacts"],
        "decisions_created_in_this_run": 0, "promotion_attempted": False,
        "candidate_generation_attempted": False, "approval_requested": False,
        "v1_1_snapshot_created": False, "operational_eligible": False,
        "provider_native_raw_count": len(native),
        "same_date_target_provider_native_raw_count": len(target_native),
        "supporting_provider_native_raw_count": len(supporting_native),
        "provider_parser_eligible_count": len(parser_candidates),
        "invalid_provider_dataset_count": sum(
            row.get("classification") == "invalid_provider_dataset" for row in discovered_provider
        ),
        "provider_parsers_implemented": [], "status": "blocked",
        "block_stage": "provider_native_evidence_inventory",
        "block_reason": "required Same-Date target provider-native bytes and rights evidence are not complete",
        "artifacts": artifacts, "supporting_provider_native_evidence": supporting,
        "discovered_provider_native_evidence": discovered_provider,
        "unclassified_relevant_artifacts": unclassified, "explicitly_excluded_artifacts": excluded,
        "missing_targets": [row["artifact_id"] for row in missing],
        "optional_missing_targets": [row["artifact_id"] for row in optional_missing],
        "invalid_targets": [row["artifact_id"] for row in invalid],
        "inspected_source_paths": sorted(set(source_paths)), "qa_handoff_ready": False,
    }


def write_p4_inventory(
    report: dict[str, Any], *, private_data_root: str | Path,
    output_path: str | Path | None = None, protected_paths: Iterable[str | Path] = (),
) -> Path:
    """Atomically write only the fixed private output and never overwrite evidence."""

    root = Path(private_data_root).resolve(strict=True)
    if root.name != "private_data":
        raise EvidenceInventoryError("private_data root must be named private_data")
    decision_raw = root / "decision"
    if decision_raw.exists() and not _is_within(decision_raw.resolve(strict=True), root):
        raise EvidenceInventoryError("private decision directory resolves outside private_data")
    allowed = (decision_raw / DEFAULT_OUTPUT_NAME).resolve(strict=False)
    requested = allowed if output_path is None else Path(output_path).resolve(strict=False)
    if requested != allowed or not _is_within(requested, root):
        raise EvidenceInventoryError("inventory output must be private_data/decision/evidence-inventory-p4.json")
    temporary = requested.with_suffix(requested.suffix + ".tmp")
    protected = {Path(item).resolve(strict=False) for item in protected_paths}
    protected.update(Path(item).resolve(strict=False) for item in report.get("inspected_source_paths", []))
    if requested in protected or temporary in protected:
        raise EvidenceInventoryError("inventory output or temporary path collides with inspected evidence")
    actions = report.setdefault("actions_performed", [])
    if "write_private_inventory" not in actions:
        actions.append("write_private_inventory")
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    decision_raw.mkdir(parents=True, exist_ok=True)
    if not _is_within(decision_raw.resolve(strict=True), root):
        raise EvidenceInventoryError("private decision directory resolves outside private_data")
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(requested)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return requested
