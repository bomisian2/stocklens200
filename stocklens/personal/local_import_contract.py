"""Fail-closed provider-neutral local CSV/JSON evidence import contract.

The validator is read-only.  A passing result does not create a parser
candidate, Same-Date snapshot, valuation, or investment decision.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .evidence_inventory import resolve_repository_private_data


SCHEMA_VERSION = "provider-neutral-local-import-v1"
DATA_KINDS = {"forward_estimates", "daily_prices"}
ACCOUNTING_BASES = {"GAAP", "NON_GAAP"}
HORIZONS = {"FY1", "FY2", "NTM"}
PER_SHARE_UNITS = {"currency_per_basic_share", "currency_per_diluted_share"}
PRICE_ADJUSTMENT_POLICIES = {"adjusted", "unadjusted"}
USAGE_SCOPES = {"personal_local_research"}
RIGHTS_ALLOWED = "authorized"
EVIDENCE_CLASSES = {"user_provided_real", "synthetic_fixture"}
MANIFEST_FIELDS = {
    "schema_version", "data_kind", "evidence_class", "provider", "ticker",
    "source_file", "source_format", "source_hash", "as_of", "available_at",
    "captured_at", "accounting_basis", "horizon", "currency", "per_share_unit",
    "analyst_count", "revision_or_vintage", "usage_scope",
    "price_adjustment_policy", "rights",
}
RIGHTS_FIELDS = {"storage", "derived_analysis", "local_display", "public_display"}


class LocalImportContractError(ValueError):
    """Raised when local import evidence is missing or internally inconsistent."""


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise LocalImportContractError(f"{label} must be a portable relative path")
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise LocalImportContractError(f"{label} must stay inside private_data")
    if any(part in {"", "."} for part in posix.parts):
        raise LocalImportContractError(f"{label} contains an invalid path segment")
    normalized = posix.as_posix()
    if not normalized.startswith("imports/"):
        raise LocalImportContractError(f"{label} must be below private_data/imports")
    return normalized


def _resolved_file(private_root: Path, relative: str, *, label: str) -> Path:
    base = private_root.resolve(strict=True)
    candidate = private_root / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base)
    except (OSError, ValueError) as exc:
        raise LocalImportContractError(f"{label} is missing or escapes private_data") from exc
    if not resolved.is_file():
        raise LocalImportContractError(f"{label} is not a regular file")
    return resolved


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LocalImportContractError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LocalImportContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise LocalImportContractError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _as_of(value: Any) -> date:
    if not isinstance(value, str) or not value.strip():
        raise LocalImportContractError("as_of is missing")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise LocalImportContractError("as_of must be exactly YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LocalImportContractError("as_of is invalid") from exc


def _hash(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{64}", value):
        raise LocalImportContractError(f"{label} must be a SHA-256 hash")
    return value.upper()


def _validate_source_bytes(path: Path, source_format: Any) -> int:
    if source_format not in {"csv", "json"}:
        raise LocalImportContractError("source_format must be csv or json")
    if path.suffix.lower() != f".{source_format}":
        raise LocalImportContractError("source_format does not match source file extension")
    if source_format == "json":
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalImportContractError("source JSON is invalid") from exc
        if not isinstance(value, (dict, list)) or not value:
            raise LocalImportContractError("source JSON must contain records")
        return len(value) if isinstance(value, list) else 1
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or any(not str(name or "").strip() for name in reader.fieldnames):
                raise LocalImportContractError("source CSV header is missing or invalid")
            rows = list(reader)
    except UnicodeDecodeError as exc:
        raise LocalImportContractError("source CSV is not UTF-8") from exc
    if not rows:
        raise LocalImportContractError("source CSV contains no records")
    return len(rows)


def validate_provider_neutral_import(
    *,
    project_root: str | Path,
    manifest_relative_path: str,
    decision_time: str,
) -> dict[str, Any]:
    """Validate one local import manifest and its immutable source bytes."""

    private_root = resolve_repository_private_data(project_root, require_exists=True)
    manifest_relative = _relative_path(manifest_relative_path, label="manifest path")
    manifest_path = _resolved_file(private_root, manifest_relative, label="import manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalImportContractError("import manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise LocalImportContractError("import manifest root must be an object")
    missing = sorted(MANIFEST_FIELDS.difference(manifest))
    unexpected = sorted(set(manifest).difference(MANIFEST_FIELDS))
    if missing:
        raise LocalImportContractError(f"import manifest required fields missing: {missing}")
    if unexpected:
        raise LocalImportContractError(
            f"import manifest contains unexpected fields: {unexpected}"
        )
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise LocalImportContractError("import schema_version is not approved")

    provider = manifest.get("provider")
    ticker = manifest.get("ticker")
    if not isinstance(provider, str) or not provider.strip():
        raise LocalImportContractError("provider is missing")
    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker):
        raise LocalImportContractError("ticker is invalid")
    data_kind = manifest.get("data_kind")
    if data_kind not in DATA_KINDS:
        raise LocalImportContractError("data_kind is not approved")
    if manifest.get("evidence_class") not in EVIDENCE_CLASSES:
        raise LocalImportContractError("evidence_class is not approved")

    source_relative = _relative_path(manifest.get("source_file"), label="source_file")
    if source_relative == manifest_relative:
        raise LocalImportContractError("source_file must not be the import manifest itself")
    source_path = _resolved_file(private_root, source_relative, label="import source")
    expected_hash = _hash(manifest.get("source_hash"), label="source_hash")
    actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest().upper()
    if actual_hash != expected_hash:
        raise LocalImportContractError("source_hash does not match source bytes")
    record_count = _validate_source_bytes(source_path, manifest.get("source_format"))

    decision_at = _timestamp(decision_time, label="decision_time")
    available_at = _timestamp(manifest.get("available_at"), label="available_at")
    captured_at = _timestamp(manifest.get("captured_at"), label="captured_at")
    as_of = _as_of(manifest.get("as_of"))
    if available_at > decision_at or captured_at > decision_at:
        raise LocalImportContractError("import evidence was not available by decision_time")
    if captured_at < available_at:
        raise LocalImportContractError("captured_at precedes available_at")
    if as_of > available_at.date():
        raise LocalImportContractError("as_of is later than available_at")

    currency = manifest.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise LocalImportContractError("currency must be an ISO-style three-letter code")
    revision = manifest.get("revision_or_vintage")
    if not isinstance(revision, str) or not revision.strip():
        raise LocalImportContractError("revision_or_vintage is missing")
    if manifest.get("usage_scope") not in USAGE_SCOPES:
        raise LocalImportContractError("usage_scope is not approved")
    rights = manifest.get("rights")
    if not isinstance(rights, dict):
        raise LocalImportContractError("rights object is missing")
    missing_rights = sorted(RIGHTS_FIELDS.difference(rights))
    unexpected_rights = sorted(set(rights).difference(RIGHTS_FIELDS))
    if missing_rights:
        raise LocalImportContractError(f"rights fields missing: {missing_rights}")
    if unexpected_rights:
        raise LocalImportContractError(
            f"rights contains unexpected fields: {unexpected_rights}"
        )
    for field in ("storage", "derived_analysis", "local_display"):
        if rights.get(field) != RIGHTS_ALLOWED:
            raise LocalImportContractError(f"{field} right is not explicitly authorized")
    if rights.get("public_display") not in {"authorized", "not_authorized"}:
        raise LocalImportContractError("public_display right is unknown")

    accounting_basis = manifest.get("accounting_basis")
    horizon = manifest.get("horizon")
    per_share_unit = manifest.get("per_share_unit")
    analyst_count = manifest.get("analyst_count")
    adjustment = manifest.get("price_adjustment_policy")
    if data_kind == "forward_estimates":
        if accounting_basis not in ACCOUNTING_BASES:
            raise LocalImportContractError("forward estimate GAAP/Non-GAAP basis is unknown")
        if horizon not in HORIZONS:
            raise LocalImportContractError("forward estimate FY1/FY2/NTM horizon is unknown")
        if per_share_unit not in PER_SHARE_UNITS:
            raise LocalImportContractError("forward estimate per_share_unit is unknown")
        if not isinstance(analyst_count, int) or isinstance(analyst_count, bool) or analyst_count < 1:
            raise LocalImportContractError("forward estimate analyst_count is missing or invalid")
        if adjustment != "not_applicable":
            raise LocalImportContractError("forward estimate price adjustment must be not_applicable")
        allowed_uses = ["forward_estimate_research"]
    else:
        if accounting_basis != "not_applicable" or horizon != "not_applicable":
            raise LocalImportContractError("daily price accounting basis and horizon must be not_applicable")
        if per_share_unit != "not_applicable" or analyst_count is not None:
            raise LocalImportContractError("daily price per-share and analyst fields must be not_applicable")
        if adjustment not in PRICE_ADJUSTMENT_POLICIES:
            raise LocalImportContractError("daily price adjusted/unadjusted policy is unknown")
        allowed_uses = (
            ["recent_price_flow_research", "integrity_diagnostics"]
            if adjustment == "unadjusted"
            else ["adjusted_price_research", "integrity_diagnostics"]
        )

    synthetic = manifest.get("evidence_class") == "synthetic_fixture"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "synthetic_evidence_container_validated"
            if synthetic else "evidence_container_validated"
        ),
        "validation_scope": "container_and_manifest_only",
        "provider": provider.strip(),
        "ticker": ticker,
        "data_kind": data_kind,
        "source_relative_path": source_relative,
        "source_hash": actual_hash,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper(),
        "as_of": manifest["as_of"],
        "available_at": available_at.isoformat().replace("+00:00", "Z"),
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "accounting_basis": accounting_basis,
        "horizon": horizon,
        "currency": currency,
        "per_share_unit": per_share_unit,
        "analyst_count": analyst_count,
        "revision_or_vintage": revision,
        "usage_scope": manifest["usage_scope"],
        "price_adjustment_policy": adjustment,
        "record_count": record_count,
        "allowed_uses": allowed_uses,
        "synthetic_fixture": synthetic,
        "row_semantics_validated": False,
        "real_data_eligible": False,
        "real_data_eligibility_reason": "row_level_semantic_validation_required",
        "parser_candidate": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }


__all__ = [
    "LocalImportContractError",
    "SCHEMA_VERSION",
    "validate_provider_neutral_import",
]
