"""Fail-closed, read-only inventory for Same-Date P4.1 local evidence."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

INVENTORY_VERSION = "same-date-evidence-inventory-p4.1-v1"
MANIFEST_VERSION = "evidence-inventory-manifest-v1"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "evidence-inventory-manifest-v1.json"
DEFAULT_OUTPUT_NAME = "evidence-inventory-p4.json"
TARGET_TICKERS = ("AVGO", "NVDA")
EVIDENCE_CLASSES = {
    "normalized_only", "derived_only", "supporting_only", "missing",
    "unclassified", "invalid_integrity", "malformed", "unreadable",
}


class EvidenceInventoryError(ValueError):
    """Raised when the inventory cannot safely inspect or write evidence."""


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
        key = (str(row.get("root")), str(row.get("path")))
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in ids:
            raise EvidenceInventoryError("inventory manifest artifact_id is missing or duplicated")
        if key in paths or row.get("root") not in {"private_data", "top10_data"}:
            raise EvidenceInventoryError("inventory manifest path is duplicated or has an invalid root")
        if row.get("classification") not in EVIDENCE_CLASSES:
            raise EvidenceInventoryError("inventory manifest has an invalid classification")
        ids.add(artifact_id)
        paths.add(key)
    return manifest


def _file_record(path: Path, *, role: str, source: str) -> dict[str, Any]:
    return {"role": role, "path": str(path.resolve()), "source": source,
            "source_hash": _sha256(path), "bytes": path.stat().st_size}


def _inspect_artifact(
    spec: dict[str, Any], *, roots: dict[str, Path], inspected_dt: datetime,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    path = roots[str(spec["root"])] / str(spec["path"])
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
            official_verified = ticker == "NVDA" and "official investor" in distribution.lower()
            source_label = ("AVGO issuer-release supporting evidence distributed by PR Newswire"
                            if ticker == "AVGO" else
                            "NVIDIA official investor-relations evidence" if official_verified else distribution)
            row: dict[str, Any] = {
                "ticker": ticker,
                "classification": "supporting_only" if integrity_ok and temporal == "valid" else
                                  ("invalid_integrity" if not integrity_ok else "malformed"),
                "provider_native_raw_exists": integrity_ok, "source": source_label,
                "official_issuer_origin_independently_verified": official_verified,
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
    for path in sorted(item for item in top10_root.iterdir() if item.is_file()):
        if path.name in declared:
            continue
        record = {"path": str(path.resolve()), "source_hash": _sha256(path)}
        if path.name in exclusions:
            excluded.append({**record, "reason": exclusions[path.name]})
        else:
            unclassified.append({**record, "classification": "unclassified", "promotion_eligible": False,
                                 "reason": "not declared or explicitly excluded by the inventory manifest"})
    return unclassified, excluded


def build_p4_evidence_inventory(
    *, private_data_root: str | Path, top10_data_root: str | Path, inspected_at: str,
    inventory_manifest_path: str | Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    """Inventory exact local evidence without network access or promotion."""

    inspected, error, inspected_dt = _parse_timestamp(inspected_at)
    if error or inspected_dt is None:
        raise EvidenceInventoryError("inspected_at must be a timezone-aware ISO timestamp")
    private_root, top10_root = Path(private_data_root), Path(top10_data_root)
    manifest_path = Path(inventory_manifest_path)
    manifest = _load_manifest(manifest_path)
    roots = {"private_data": private_root, "top10_data": top10_root}
    artifacts: list[dict[str, Any]] = []
    values: dict[str, dict[str, Any] | None] = {}
    for spec in manifest["artifacts"]:
        artifact, value = _inspect_artifact(spec, roots=roots, inspected_dt=inspected_dt)
        _add_details(artifact, value)
        artifacts.append(artifact)
        values[artifact["artifact_id"]] = value
    _apply_alpha_integrity(artifacts, values, inspected_dt)
    unclassified, excluded = _classify_top10_directory(top10_root, manifest)
    supporting = _issuer_supporting_evidence(private_root, inspected_dt)
    missing = [row for row in artifacts if row["inspection_status"] == "missing"]
    invalid_states = {"malformed", "unreadable", "invalid_integrity", "future_timestamp",
                      "timezone_missing", "invalid_timestamp"}
    invalid = [row for row in artifacts if row["classification"] in invalid_states
               or row["inspection_status"] in invalid_states]
    native = [row for row in supporting if row.get("provider_native_raw_exists") is True]
    source_paths = [str(manifest_path.resolve())] + [row["path"] for row in artifacts if row.get("exists")]
    source_paths += [str(row["file"]["path"]) for row in supporting if isinstance(row.get("file"), dict)]
    return {
        "inventory_version": INVENTORY_VERSION, "manifest_version": manifest["manifest_version"],
        "manifest_path": str(manifest_path.resolve()), "manifest_hash": _sha256(manifest_path),
        "inspected_at": inspected, "target_tickers": list(TARGET_TICKERS),
        "expected_artifact_count": len(artifacts),
        "inspected_artifact_count": sum(row.get("exists") is True for row in artifacts),
        "missing_artifact_count": len(missing), "unclassified_artifact_count": len(unclassified),
        "invalid_artifact_count": len(invalid),
        "inventory_complete": not missing and not unclassified and not invalid,
        "network_capability_present": False, "network_calls_observed_in_this_run": 0,
        "actions_performed": ["read_local_metadata", "hash_local_files", "classify_local_artifacts"],
        "decisions_created_in_this_run": 0, "promotion_attempted": False,
        "candidate_generation_attempted": False, "approval_requested": False,
        "v1_1_snapshot_created": False, "operational_eligible": False,
        "provider_native_raw_count": len(native), "same_date_target_provider_native_raw_count": 0,
        "supporting_provider_native_raw_count": len(native), "provider_parser_eligible_count": 0,
        "provider_parsers_implemented": [], "status": "blocked",
        "block_stage": "provider_native_evidence_inventory",
        "block_reason": "required Same-Date target provider-native bytes and rights evidence are not complete",
        "artifacts": artifacts, "supporting_provider_native_evidence": supporting,
        "unclassified_relevant_artifacts": unclassified, "explicitly_excluded_artifacts": excluded,
        "missing_targets": [row["artifact_id"] for row in missing],
        "invalid_targets": [row["artifact_id"] for row in invalid],
        "inspected_source_paths": sorted(set(source_paths)), "qa_handoff_ready": False,
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def write_p4_inventory(
    report: dict[str, Any], *, private_data_root: str | Path,
    output_path: str | Path | None = None, protected_paths: Iterable[str | Path] = (),
) -> Path:
    """Atomically write only the fixed private output and never overwrite evidence."""

    root = Path(private_data_root).resolve(strict=True)
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
