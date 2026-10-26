"""Immutable, offline input bundles for Decision Model v2.1.

The bundle severs a persisted decision from mutable files in the sibling
Nasdaq Top10 project.  It performs no network I/O and never changes the
original source files.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import atomic_write_json


BUNDLE_VERSION = "decision-model-v2.1-input-bundle-v1"
MANIFEST_NAME = "bundle-manifest.json"
SOURCE_ROLES = (
    "price_history",
    "consensus",
    "valuation_history",
    "industry_map",
    "macro",
    "config",
)
SOURCE_FILENAMES = {
    "price_history": "price-history.json",
    "consensus": "consensus.json",
    "valuation_history": "valuation-history.json",
    "industry_map": "industry-map.json",
    "macro": "macro-snapshot.json",
    "config": "decision-model-v2.json",
}


class DecisionInputBundleError(ValueError):
    """Raised when a bundle cannot be created or verified safely."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def file_sha256(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DecisionInputBundleError(f"{label} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DecisionInputBundleError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise DecisionInputBundleError(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc)


def _json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionInputBundleError(f"invalid JSON source: {path.name}") from exc
    if not isinstance(value, dict):
        raise DecisionInputBundleError(f"JSON source must be an object: {path.name}")
    return payload, value


def _generated_at(value: dict[str, Any], role: str, decision_at: datetime) -> str | None:
    if role == "config":
        return None
    generated = value.get("generatedAt")
    if _timestamp(generated, f"{role}.generatedAt") > decision_at:
        raise DecisionInputBundleError(f"{role} contains future data")
    return str(generated)


def _resolve_evidence_path(data_root: Path, relative: str) -> Path:
    declared = Path(relative)
    if declared.is_absolute() or ".." in declared.parts:
        raise DecisionInputBundleError("consensus evidence path is unsafe")
    candidates = [data_root / declared]
    if data_root.name.lower() == "data":
        candidates.insert(0, data_root.parent / declared)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise DecisionInputBundleError("selected consensus evidence file is missing")


def select_latest_consensus_evidence(
    *, data_root: str | Path, decision_time: str
) -> tuple[Path, dict[str, Any], Path, str]:
    data_root = Path(data_root).resolve()
    manifest_path = (data_root / "consensus-evidence" / "manifest.json").resolve()
    _, manifest = _json_bytes(manifest_path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise DecisionInputBundleError("consensus evidence manifest has no artifacts")
    decision_at = _timestamp(decision_time, "decision_time")
    eligible: list[tuple[datetime, str, dict[str, Any]]] = []
    for declared_hash, row in artifacts.items():
        if not isinstance(declared_hash, str) or not isinstance(row, dict):
            continue
        generated = _timestamp(row.get("evidenceGeneratedAt"), "evidenceGeneratedAt")
        if generated <= decision_at:
            eligible.append((generated, declared_hash.upper(), row))
    if not eligible:
        raise DecisionInputBundleError("no consensus evidence is available before decision_time")
    _, declared_hash, selected = max(eligible, key=lambda item: item[0])
    source_path = _resolve_evidence_path(data_root, str(selected.get("evidenceFile") or ""))
    actual_hash = file_sha256(source_path)
    if actual_hash != declared_hash:
        raise DecisionInputBundleError("consensus evidence SHA-256 mismatch")
    return source_path, selected, manifest_path, file_sha256(manifest_path)


def _decision_id(decision_time: str, hashes: dict[str, str]) -> str:
    compact_time = decision_time.replace("-", "").replace(":", "").replace(".", "").replace("+00:00", "Z")
    digest = sha256_bytes(
        json.dumps(
            {"decision_time": decision_time, "source_hashes": hashes},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )[:12].lower()
    return f"nvda-{compact_time.lower()}-{digest}"


def create_input_bundle(
    *,
    data_root: str | Path,
    config_path: str | Path,
    decision_root: str | Path,
    decision_time: str,
    code_commit_sha: str,
) -> tuple[Path, dict[str, Any], str]:
    """Create or idempotently reuse a complete immutable input bundle."""
    decision_at = _timestamp(decision_time, "decision_time")
    data_root = Path(data_root).resolve()
    decision_root = Path(decision_root).resolve()
    bundle_root = decision_root / "input-bundles"
    consensus_path, consensus_entry, evidence_manifest_path, evidence_manifest_sha = (
        select_latest_consensus_evidence(data_root=data_root, decision_time=decision_time)
    )
    originals = {
        "price_history": data_root / "price-history.json",
        "consensus": consensus_path,
        "valuation_history": data_root / "valuation-history.json",
        "industry_map": data_root / "industry-map.json",
        "macro": data_root / "macro-snapshot.json",
        "config": Path(config_path).resolve(),
    }
    payloads: dict[str, bytes] = {}
    values: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    generated: dict[str, str | None] = {}
    for role in SOURCE_ROLES:
        path = originals[role].resolve()
        payload, value = _json_bytes(path)
        payloads[role] = payload
        values[role] = value
        hashes[role] = sha256_bytes(payload)
        generated[role] = _generated_at(value, role, decision_at)

    decision_id = _decision_id(decision_time, hashes)
    final_dir = bundle_root / decision_id
    source_entries: list[dict[str, Any]] = []
    for role in SOURCE_ROLES:
        row = {
            "role": role,
            "bundle_path": SOURCE_FILENAMES[role],
            "sha256": hashes[role],
            "original_local_path": str(originals[role].resolve()),
            "generated_at": generated[role],
            "available_at": generated[role],
            "source_kind": "preserved_consensus_evidence" if role == "consensus" else "local_snapshot_copy",
            "ticker": "NVDA" if role in {"price_history", "consensus", "valuation_history"} else None,
            "operational_eligible": False,
        }
        source_entries.append(row)
    manifest: dict[str, Any] = {
        "bundle_version": BUNDLE_VERSION,
        "decision_id": decision_id,
        "created_at": decision_time,
        "decision_time": decision_time,
        "code_commit_sha": code_commit_sha.lower(),
        "operational_eligible": False,
        "sources": source_entries,
        "consensus_selection": {
            "evidence_generated_at": consensus_entry.get("evidenceGeneratedAt"),
            "evidence_artifact_id": consensus_entry.get("evidenceArtifactId"),
            "evidence_manifest_original_path": str(evidence_manifest_path),
            "evidence_manifest_sha256_at_creation": evidence_manifest_sha,
        },
    }

    if final_dir.exists():
        existing_manifest_path = final_dir / MANIFEST_NAME
        if not existing_manifest_path.is_file():
            raise DecisionInputBundleError("existing input bundle is incomplete")
        existing = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise DecisionInputBundleError("input bundle decision_id collision")
        verified, manifest_sha = validate_input_bundle(final_dir)
        return final_dir, verified, manifest_sha

    bundle_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{decision_id}.", dir=bundle_root))
    try:
        for role in SOURCE_ROLES:
            target = temp_dir / SOURCE_FILENAMES[role]
            target.write_bytes(payloads[role])
            if file_sha256(target) != hashes[role]:
                raise DecisionInputBundleError(f"copied {role} SHA-256 mismatch")
        atomic_write_json(temp_dir / MANIFEST_NAME, manifest)
        validate_input_bundle(temp_dir, expected_decision_id=decision_id)
        try:
            os.replace(temp_dir, final_dir)
        except FileExistsError as exc:
            raise DecisionInputBundleError("input bundle promotion collision") from exc
    except BaseException:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise
    verified, manifest_sha = validate_input_bundle(final_dir)
    return final_dir, verified, manifest_sha


def validate_input_bundle(
    bundle_dir: str | Path,
    *,
    expected_manifest_sha256: str | None = None,
    expected_decision_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    bundle_dir = Path(bundle_dir).resolve()
    manifest_path = bundle_dir / MANIFEST_NAME
    payload, manifest = _json_bytes(manifest_path)
    manifest_sha = sha256_bytes(payload)
    if expected_manifest_sha256 is not None and manifest_sha != expected_manifest_sha256:
        raise DecisionInputBundleError("input bundle manifest SHA-256 mismatch")
    if manifest.get("bundle_version") != BUNDLE_VERSION:
        raise DecisionInputBundleError("input bundle version mismatch")
    expected_id = expected_decision_id or bundle_dir.name
    if manifest.get("decision_id") != expected_id:
        raise DecisionInputBundleError("input bundle decision_id mismatch")
    if manifest.get("operational_eligible") is not False:
        raise DecisionInputBundleError("input bundle cannot be operational")
    decision_at = _timestamp(manifest.get("decision_time"), "bundle decision_time")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != len(SOURCE_ROLES):
        raise DecisionInputBundleError("input bundle source manifest mismatch")
    seen: set[str] = set()
    for row in sources:
        if not isinstance(row, dict):
            raise DecisionInputBundleError("input bundle source entry is invalid")
        role = row.get("role")
        if role not in SOURCE_ROLES or role in seen:
            raise DecisionInputBundleError("input bundle source role mismatch")
        seen.add(role)
        relative = Path(str(row.get("bundle_path") or ""))
        if relative.is_absolute() or ".." in relative.parts or relative != Path(SOURCE_FILENAMES[role]):
            raise DecisionInputBundleError(f"input bundle {role} path is unsafe")
        resolved = (bundle_dir / relative).resolve()
        try:
            resolved.relative_to(bundle_dir)
        except ValueError as exc:
            raise DecisionInputBundleError(f"input bundle {role} path escapes bundle") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise DecisionInputBundleError(f"input bundle {role} file is missing")
        if file_sha256(resolved) != row.get("sha256"):
            raise DecisionInputBundleError(f"input bundle {role} SHA-256 mismatch")
        _, value = _json_bytes(resolved)
        generated = _generated_at(value, role, decision_at)
        if generated != row.get("generated_at") or row.get("available_at") != generated:
            raise DecisionInputBundleError(f"input bundle {role} timestamp mismatch")
        if row.get("operational_eligible") is not False:
            raise DecisionInputBundleError(f"input bundle {role} cannot be operational")
    if seen != set(SOURCE_ROLES):
        raise DecisionInputBundleError("input bundle source roles are incomplete")
    return manifest, manifest_sha


def bundled_source_paths(bundle_dir: str | Path, manifest: dict[str, Any]) -> dict[str, Path]:
    bundle_dir = Path(bundle_dir).resolve()
    return {
        str(row["role"]): (bundle_dir / str(row["bundle_path"])).resolve()
        for row in manifest["sources"]
    }


def detect_parent_input_updates(bundle_dir: str | Path) -> dict[str, Any]:
    """Return a safe status only; never invalidate the immutable decision."""
    bundle_dir = Path(bundle_dir).resolve()
    manifest, _ = validate_input_bundle(bundle_dir)
    changed: list[str] = []
    for row in manifest["sources"]:
        if row["role"] == "consensus":
            continue
        original = Path(str(row.get("original_local_path") or ""))
        if original.is_file() and file_sha256(original) != row["sha256"]:
            changed.append(str(row["role"]))
    selection = manifest.get("consensus_selection") or {}
    evidence_manifest = Path(str(selection.get("evidence_manifest_original_path") or ""))
    if evidence_manifest.is_file():
        try:
            current = json.loads(evidence_manifest.read_text(encoding="utf-8"))
            rows = current.get("artifacts") or {}
            latest = max(
                (_timestamp(row.get("evidenceGeneratedAt"), "evidenceGeneratedAt") for row in rows.values() if isinstance(row, dict)),
                default=None,
            )
            bundled = _timestamp(selection.get("evidence_generated_at"), "bundled consensus generated_at")
            if latest is not None and latest > bundled:
                changed.append("consensus")
        except (OSError, json.JSONDecodeError, DecisionInputBundleError):
            changed.append("consensus_manifest_unreadable")
    return {
        "new_input_available": bool(changed),
        "changed_roles": sorted(set(changed)),
        "status": "new_input_available" if changed else "current",
    }
