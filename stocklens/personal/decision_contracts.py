"""Fail-closed offline contracts for Same-Date decision promotion.

Every normalized row is reconstructed from preserved source bytes. The
module performs no network access and never creates an investment decision.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from stocklens.market_calendar import (
    is_xnys_session,
    latest_completed_session,
    next_xnys_session,
    xnys_session_bounds,
    xnys_sessions,
)

CONTRACT_VERSION = "same-date-ready-contract-v3"
EVIDENCE_CLASSES = {"synthetic_fixture", "local_evidence_candidate"}
PROVENANCE_REGISTRY_VERSION = "same-date-provenance-candidate-v2"
USAGE_POLICY_REGISTRY_VERSION = "same-date-usage-policy-candidate-v2"
APPROVAL_RECEIPT_VERSION = "same-date-independent-approval-receipt-v1"
APPROVAL_SCOPE = "personal_local_same_date_research"
ACCOUNTING_BASES = {"gaap", "non_gaap"}
FORECAST_HORIZONS = {"FY1", "NTM"}
EPS_UNITS = {"currency_per_diluted_share"}
PER_UNITS = {"multiple"}
USAGE_SCOPES = {"personal_local_research"}
WEIGHTING_METHODS = {"equal_weight", "market_cap"}
TIMING_BASES = {"sec_acceptance_timestamp", "conservative_next_session"}
PRICE_ADJUSTMENT_POLICIES = {"split_and_cash_dividend_adjusted"}
SPLIT_POLICIES = {"price_and_eps_split_adjusted"}
REGIME_STATES = {"risk_on", "neutral", "risk_off"}
VERIFIED_HISTORICAL_AVAILABILITY = {
    "provider_point_in_time_archive",
    "contemporaneous_preserved_raw",
    "revision_database_extract",
    "trusted_collection_log",
}
UNVERIFIED_HISTORICAL_AVAILABILITY = "historical_availability_unverified"
EPS_MAX_LAG_SESSIONS = 1
REGIME_MAX_LAG_SESSIONS = 1

CONTRACT_ROLES = (
    "pit_forward_eps",
    "company_pit_forward_per",
    "relative_forward_multiples",
    "filing_timing",
    "adjusted_price_history",
    "regime",
)
REQUIREMENT_BY_ROLE = {
    "pit_forward_eps": "verified_pit_forward_eps",
    "company_pit_forward_per": "company_pit_forward_per_ledger",
    "relative_forward_multiples": "independent_qqq_soxx_peer_forward_multiples",
    "filing_timing": "sec_acceptance_or_conservative_next_session",
    "adjusted_price_history": "daily_adjusted_price_history",
}


class DecisionContractError(ValueError):
    """Raised when evidence is malformed, stale, future-dated, or unbound."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def canonical_record_hash(value: dict[str, Any]) -> str:
    """Return the stable hash binding normalized fields to one raw record."""
    return _sha256(_canonical_bytes(value))


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise DecisionContractError(f"{label} must be a SHA-256 hash")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise DecisionContractError(f"{label} must be a SHA-256 hash") from exc
    return value.upper()


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DecisionContractError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionContractError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DecisionContractError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date(value: Any, label: str) -> date:
    if not isinstance(value, str) or not value:
        raise DecisionContractError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DecisionContractError(f"{label} must be an ISO date") from exc


def _number(value: Any, label: str, *, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionContractError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise DecisionContractError(f"{label} must be a finite positive number")
    return result


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionContractError(f"{label} must be an object")
    return value


def _load(path: str | Path, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise DecisionContractError(f"{label} is missing")
    raw = source.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionContractError(f"{label} is not valid UTF-8 JSON") from exc
    return source, raw, _object(value, f"{label} root")


def _common(
    value: dict[str, Any], *, role: str, decision_at: datetime, tickers: tuple[str, ...]
) -> str:
    if value.get("schema_version") != f"{role.replace('_', '-')}-v2":
        raise DecisionContractError(f"{role} schema_version is not approved")
    if value.get("contract_version") != CONTRACT_VERSION:
        raise DecisionContractError(f"{role} contract_version is not approved")
    classification = value.get("evidence_classification")
    if classification not in EVIDENCE_CLASSES:
        raise DecisionContractError(f"{role} evidence_classification is not approved")
    if value.get("target_tickers") != list(tickers):
        raise DecisionContractError(f"{role} target_tickers do not match")
    if _timestamp(value.get("decision_time"), f"{role}.decision_time") != decision_at:
        raise DecisionContractError(f"{role} decision_time does not match")
    return str(classification)


def _source_files(
    manifest_path: Path, value: dict[str, Any], *, role: str, decision_at: datetime
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    rows = value.get("source_files")
    if not isinstance(rows, list) or not rows:
        raise DecisionContractError(f"{role}.source_files are missing")
    root = manifest_path.parent.resolve()
    sources: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    expected_schema = f"{role.replace('_', '-')}-source-v3"
    for index, item in enumerate(rows):
        row = _object(item, f"{role}.source_files[{index}]")
        relative = row.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise DecisionContractError(f"{role}.source_files[{index}].path must be relative")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise DecisionContractError(f"{role}.source_files[{index}].path escapes root") from exc
        source_path, raw, parsed = _load(path, f"{role}.source_files[{index}]")
        expected_hash = _hash(row.get("source_hash"), f"{role}.source_files[{index}].source_hash")
        actual_hash = _sha256(raw)
        if expected_hash != actual_hash:
            raise DecisionContractError(f"{role}.source_files[{index}] source_hash mismatch")
        if parsed.get("raw_schema_version") != expected_schema:
            raise DecisionContractError(f"{role}.source_files[{index}] raw schema is not approved")
        if not isinstance(parsed.get("source"), str) or not parsed["source"].strip():
            raise DecisionContractError(f"{role}.source_files[{index}] source is missing")
        if not isinstance(parsed.get("provider"), str) or not parsed["provider"].strip():
            raise DecisionContractError(f"{role}.source_files[{index}] provider is missing")
        native = _object(
            parsed.get("provider_native"),
            f"{role}.source_files[{index}].provider_native",
        )
        native_relative = native.get("path")
        if (
            not isinstance(native_relative, str)
            or not native_relative
            or Path(native_relative).is_absolute()
        ):
            raise DecisionContractError(
                f"{role}.source_files[{index}].provider_native.path must be relative"
            )
        native_path = (source_path.parent / native_relative).resolve()
        try:
            native_path.relative_to(root)
        except ValueError as exc:
            raise DecisionContractError(
                f"{role}.source_files[{index}].provider_native.path escapes root"
            ) from exc
        if native_path == source_path.resolve():
            raise DecisionContractError(
                f"{role}.source_files[{index}] provider-native raw and normalized files must differ"
            )
        native_hash = _hash(
            native.get("source_hash"),
            f"{role}.source_files[{index}].provider_native.source_hash",
        )
        if not native_path.is_file() or _sha256(native_path.read_bytes()) != native_hash:
            raise DecisionContractError(
                f"{role}.source_files[{index}] provider-native raw hash mismatch"
            )
        for field in ("mime_type", "parser_name", "parser_version"):
            if not isinstance(native.get(field), str) or not native[field].strip():
                raise DecisionContractError(
                    f"{role}.source_files[{index}].provider_native.{field} is missing"
                )
        if not (native.get("source_url") or native.get("local_collection_path")):
            raise DecisionContractError(
                f"{role}.source_files[{index}] provider-native source location is missing"
            )
        if not (native.get("revision_id") or native.get("vintage_id")):
            raise DecisionContractError(
                f"{role}.source_files[{index}] provider-native revision or vintage is missing"
            )
        available_at = _timestamp(row.get("available_at"), f"{role}.source_files[{index}].available_at")
        captured_at = _timestamp(parsed.get("captured_at"), f"{role}.source_files[{index}].captured_at")
        if available_at != captured_at:
            raise DecisionContractError(
                f"{role}.source_files[{index}].available_at does not match raw captured_at"
            )
        if available_at > decision_at:
            raise DecisionContractError(f"{role}.source_files[{index}] is future information")
        native_captured_at = _timestamp(
            native.get("captured_at"),
            f"{role}.source_files[{index}].provider_native.captured_at",
        )
        provider_timestamp = _timestamp(
            native.get("provider_timestamp"),
            f"{role}.source_files[{index}].provider_native.provider_timestamp",
        )
        if provider_timestamp > native_captured_at or native_captured_at > captured_at:
            raise DecisionContractError(
                f"{role}.source_files[{index}] provider timestamp/capture order is invalid"
            )
        normalized_records_hash = _hash(
            parsed.get("normalized_records_sha256"),
            f"{role}.source_files[{index}].normalized_records_sha256",
        )
        if normalized_records_hash != _sha256(_canonical_bytes(parsed.get("records"))):
            raise DecisionContractError(
                f"{role}.source_files[{index}] normalized records hash mismatch"
            )
        if actual_hash in sources:
            raise DecisionContractError(f"{role}.source_files contain a duplicate source_hash")
        sources[actual_hash] = {
            "path": source_path,
            "raw": raw,
            "value": parsed,
            "available_at": available_at,
            "provider_native_path": native_path,
            "provider_native_hash": native_hash,
            "provider_native": native,
            "normalized_records_hash": normalized_records_hash,
        }
        evidence.append({
            "role": f"contract_source:{role}:{index}",
            "path": str(source_path),
            "source_hash": actual_hash,
            "available_at": available_at.isoformat(),
            "provider": parsed["provider"],
            "provider_native_path": str(native_path),
            "provider_native_hash": native_hash,
            "provider_timestamp": provider_timestamp.isoformat(),
            "provider_captured_at": native_captured_at.isoformat(),
            "normalized_records_hash": normalized_records_hash,
            "parser_name": native["parser_name"],
            "parser_version": native["parser_version"],
            "revision_id": native.get("revision_id"),
            "vintage_id": native.get("vintage_id"),
        })
    return sources, evidence


def _raw_list(
    sources: dict[str, dict[str, Any]], label: str
) -> list[tuple[str, datetime, dict[str, Any]]]:
    records: list[tuple[str, datetime, dict[str, Any]]] = []
    for source_hash, source in sources.items():
        rows = source["value"].get("records")
        if not isinstance(rows, list):
            raise DecisionContractError(f"{label} raw records are missing")
        records.extend(
            (source_hash, source["available_at"], _object(row, f"{label} raw record"))
            for row in rows
        )
    return records


def _raw_dict(
    sources: dict[str, dict[str, Any]], label: str
) -> dict[str, tuple[str, list[Any]]]:
    merged: dict[str, tuple[str, list[Any]]] = {}
    for source_hash, source in sources.items():
        records = source["value"].get("records")
        if not isinstance(records, dict):
            raise DecisionContractError(f"{label} raw records are missing")
        for symbol, rows in records.items():
            if symbol in merged or not isinstance(rows, list):
                raise DecisionContractError(f"{label} raw symbol set is invalid")
            merged[str(symbol)] = (source_hash, rows)
    return merged


def _exact_normalized(
    manifest_row: Any,
    raw_row: dict[str, Any],
    *,
    source_hash: str,
    label: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = {**raw_row, "source_hash": source_hash, **(extra or {})}
    if manifest_row != normalized:
        raise DecisionContractError(f"{label} does not match parsed source bytes")
    return normalized


def _available(row: dict[str, Any], *, label: str, decision_at: datetime) -> datetime:
    available_at = _timestamp(row.get("available_at"), f"{label}.available_at")
    if available_at > decision_at:
        raise DecisionContractError(f"{label}.available_at is later than decision_time")
    return available_at


def _session(value: Any, label: str) -> date:
    result = _date(value, label)
    if not is_xnys_session(result):
        raise DecisionContractError(f"{label} is not an XNYS session")
    return result


def _session_lag(older: date, newer: date, label: str) -> int:
    if older > newer:
        raise DecisionContractError(f"{label} is later than the aligned price session")
    sessions = xnys_sessions(older, newer)
    if not sessions or sessions[0] != older or sessions[-1] != newer:
        raise DecisionContractError(f"{label} is not aligned to XNYS sessions")
    return len(sessions) - 1


def _result(
    requirement_id: str,
    *,
    classification: str | None,
    schema_valid: bool,
    reason: str,
    manifest_path: Path | None = None,
    manifest_hash: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = (
        "fixture_validated"
        if schema_valid and classification == "synthetic_fixture"
        else ("candidate_validated" if schema_valid else "blocked")
    )
    if schema_valid:
        prefix = (
            "synthetic fixture validated; not actual evidence"
            if classification == "synthetic_fixture"
            else "local evidence candidate validated; external approval required"
        )
        reason = f"{prefix}: {reason}"
    return {
        "id": requirement_id,
        "passed": False,
        "schema_valid": schema_valid,
        "status": status,
        "reason": reason,
        "evidence_classification": classification,
        "manifest_path": str(manifest_path.resolve()) if manifest_path else None,
        "manifest_hash": manifest_hash,
        "evidence": evidence or [],
        "details": details or {},
    }


def _blocked_result(requirement_id: str, reason: str) -> dict[str, Any]:
    return _result(requirement_id, classification=None, schema_valid=False, reason=reason)


def _validate_eps(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path, raw, value = _load(path, "PIT Forward EPS manifest")
    classification = _common(value, role="pit_forward_eps", decision_at=decision_at, tickers=tickers)
    sources, evidence = _source_files(manifest_path, value, role="pit_forward_eps", decision_at=decision_at)
    raw_rows = _raw_list(sources, "pit_forward_eps")
    rows = value.get("records")
    if not isinstance(rows, list) or len(rows) != len(raw_rows) or len(rows) != len(tickers):
        raise DecisionContractError("pit_forward_eps.records must contain one raw-bound row per ticker")
    metadata: dict[str, dict[str, Any]] = {}
    for index, ((source_hash, source_available, raw_row), manifest_row) in enumerate(
        zip(raw_rows, rows, strict=True)
    ):
        canonical_hash = canonical_record_hash(raw_row)
        row = _exact_normalized(
            manifest_row, raw_row, source_hash=source_hash,
            label=f"pit_forward_eps.records[{index}]",
            extra={"canonical_record_hash": canonical_hash},
        )
        ticker = row.get("ticker")
        if ticker not in tickers or ticker in metadata:
            raise DecisionContractError("pit_forward_eps ticker set is invalid")
        basis, horizon, currency, unit = (
            row.get("accounting_basis"), row.get("forecast_horizon"),
            row.get("currency"), row.get("unit"),
        )
        if basis not in ACCOUNTING_BASES:
            raise DecisionContractError(f"{ticker} accounting_basis is not approved")
        if horizon not in FORECAST_HORIZONS:
            raise DecisionContractError(f"{ticker} forecast_horizon is not approved")
        if not isinstance(currency, str) or len(currency) != 3 or currency.upper() != currency:
            raise DecisionContractError(f"{ticker} currency is invalid")
        if unit not in EPS_UNITS:
            raise DecisionContractError(f"{ticker} unit is not approved")
        if row.get("usage_scope") not in USAGE_SCOPES:
            raise DecisionContractError(f"{ticker} usage_scope is not approved")
        if not isinstance(row.get("provider"), str) or not row["provider"].strip():
            raise DecisionContractError(f"{ticker} provider is missing")
        as_of = _session(row.get("as_of"), f"{ticker}.as_of")
        available_at = _available(row, label=str(ticker), decision_at=decision_at)
        if source_available > available_at:
            raise DecisionContractError(f"{ticker} source was not available by the EPS record timestamp")
        low = _number(row.get("eps_low"), f"{ticker}.eps_low")
        base = _number(row.get("eps_base"), f"{ticker}.eps_base")
        high = _number(row.get("eps_high"), f"{ticker}.eps_high")
        if not low <= base <= high:
            raise DecisionContractError(f"{ticker} EPS scenarios are not ordered")
        metadata[str(ticker)] = {
            "accounting_basis": basis, "forecast_horizon": horizon,
            "currency": currency, "unit": unit, "as_of": as_of.isoformat(),
            "available_at": available_at.isoformat(), "source_hash": source_hash,
            "canonical_record_hash": canonical_hash, "provider": row.get("provider"),
        }
    shared = {
        (row["accounting_basis"], row["forecast_horizon"], row["currency"], row["unit"])
        for row in metadata.values()
    }
    if len(shared) != 1:
        raise DecisionContractError("PIT Forward EPS basis, horizon, currency, and unit must match")
    return _result(
        "verified_pit_forward_eps", classification=classification, schema_valid=True,
        reason="raw consensus bytes deterministically reproduce every PIT EPS record",
        manifest_path=manifest_path, manifest_hash=_sha256(raw), evidence=evidence,
        details={"metadata": metadata, "source_hashes": sorted(sources)},
    ), value


def _validate_company_per(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...], eps_result: dict[str, Any]
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "company PIT Forward PER ledger")
    classification = _common(value, role="company_pit_forward_per", decision_at=decision_at, tickers=tickers)
    sources, evidence = _source_files(manifest_path, value, role="company_pit_forward_per", decision_at=decision_at)
    ledger_rows: list[tuple[str, datetime, dict[str, Any]]] = []
    historical_eps: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for source_hash, source in sources.items():
        source_kind = source["value"].get("source_kind")
        source_rows = source["value"].get("records")
        if not isinstance(source_rows, list):
            raise DecisionContractError("company_pit_forward_per raw records are missing")
        if source_kind == "per_ledger":
            ledger_rows.extend(
                (source_hash, source["available_at"], _object(row, "company PER raw record"))
                for row in source_rows
            )
        elif source_kind == "historical_eps_archive":
            for raw_eps in source_rows:
                eps_record = _object(raw_eps, "historical EPS raw record")
                record_hash = canonical_record_hash(eps_record)
                key = (source_hash, record_hash)
                if key in historical_eps:
                    raise DecisionContractError("historical EPS archive contains duplicate records")
                historical_eps[key] = (eps_record, source)
        else:
            raise DecisionContractError("company PIT PER source_kind is not approved")
    if not ledger_rows or not historical_eps:
        raise DecisionContractError("company PIT PER requires separate PER and historical EPS sources")
    rows = value.get("records")
    if not isinstance(rows, list) or len(rows) != len(ledger_rows):
        raise DecisionContractError("company_pit_forward_per records do not match raw bytes")
    normalized: list[dict[str, Any]] = []
    for index, ((source_hash, _, raw_row), manifest_row) in enumerate(zip(ledger_rows, rows, strict=True)):
        if "eps_record" in raw_row:
            raise DecisionContractError("embedded historical EPS records are not approved")
        normalized.append(_exact_normalized(
            manifest_row, raw_row, source_hash=source_hash,
            label=f"company_pit_forward_per.records[{index}]",
        ))
    current_meta = (eps_result.get("details") or {}).get("metadata") or {}
    for ticker in tickers:
        ticker_rows = [row for row in normalized if row.get("ticker") == ticker]
        if len(ticker_rows) < 20:
            raise DecisionContractError(f"{ticker} company PIT Forward PER history has fewer than 20 rows")
        dates: list[date] = []
        for index, row in enumerate(ticker_rows):
            label = f"{ticker}.per[{index}]"
            observation = _session(row.get("as_of"), f"{label}.as_of")
            dates.append(observation)
            _available(row, label=label, decision_at=decision_at)
            eps_source_hash = _hash(
                row.get("underlying_eps_source_hash"), f"{label}.underlying_eps_source_hash"
            )
            eps_record_hash = _hash(
                row.get("underlying_eps_record_hash"), f"{label}.underlying_eps_record_hash"
            )
            linked = historical_eps.get((eps_source_hash, eps_record_hash))
            if linked is None:
                raise DecisionContractError(f"{label} historical EPS source/hash link does not exist")
            eps_record, eps_source = linked
            if eps_record.get("ticker") != ticker:
                raise DecisionContractError(f"{label}.eps_record ticker does not match")
            eps_as_of = _session(eps_record.get("as_of"), f"{label}.eps_record.as_of")
            if eps_as_of > observation:
                raise DecisionContractError(f"{label}.eps_record.as_of is later than PER observation session")
            eps_available = _timestamp(eps_record.get("available_at"), f"{label}.eps_record.available_at")
            source_available = _timestamp(
                eps_record.get("source_available_at"), f"{label}.eps_record.source_available_at"
            )
            session_open, _ = xnys_session_bounds(observation)
            if not source_available <= eps_available < session_open:
                raise DecisionContractError(f"{label} violates EPS source <= EPS record < PER observation session")
            if eps_source["available_at"] < eps_available:
                raise DecisionContractError(f"{label} historical EPS archive predates its record availability")
            availability_status = eps_record.get("evidence_status")
            if availability_status == UNVERIFIED_HISTORICAL_AVAILABILITY:
                raise DecisionContractError(
                    f"{label} historical_availability_unverified; prohibited for backtest and operating PER distribution"
                )
            if availability_status not in VERIFIED_HISTORICAL_AVAILABILITY:
                raise DecisionContractError(
                    f"{label}.eps_record.evidence_status is not an approved historical PIT proof"
                )
            pit_proof = _object(eps_record.get("pit_proof"), f"{label}.eps_record.pit_proof")
            if pit_proof.get("type") != availability_status:
                raise DecisionContractError(f"{label}.eps_record PIT proof type does not match status")
            if _hash(
                pit_proof.get("provider_raw_sha256"),
                f"{label}.eps_record.pit_proof.provider_raw_sha256",
            ) != eps_source["provider_native_hash"]:
                raise DecisionContractError(f"{label}.eps_record PIT proof source hash mismatch")
            proof_timestamp = _timestamp(
                pit_proof.get("provider_timestamp"),
                f"{label}.eps_record.pit_proof.provider_timestamp",
            )
            if proof_timestamp != source_available:
                raise DecisionContractError(
                    f"{label}.eps_record PIT proof timestamp does not match source availability"
                )
            if availability_status in {
                "provider_point_in_time_archive",
                "revision_database_extract",
            }:
                proof_vintage = pit_proof.get("vintage_id")
                if not isinstance(proof_vintage, str) or not proof_vintage.strip():
                    raise DecisionContractError(f"{label}.eps_record PIT proof vintage is missing")
            elif eps_source["provider_native"].get("captured_at") != eps_record.get("source_available_at"):
                raise DecisionContractError(
                    f"{label}.eps_record contemporaneous proof was not captured at source availability"
                )
            current = current_meta.get(ticker) or {}
            for field in ("accounting_basis", "forecast_horizon", "currency", "provider"):
                if row.get(field) != current.get(field) or eps_record.get(field) != row.get(field):
                    raise DecisionContractError(f"{label}.{field} does not match PIT EPS basis")
            if eps_record.get("unit") not in EPS_UNITS:
                raise DecisionContractError(f"{label}.eps_record.unit is not approved")
            if canonical_record_hash(eps_record) != eps_record_hash:
                raise DecisionContractError(f"{label} historical EPS canonical hash mismatch")
            if row.get("unit") not in PER_UNITS:
                raise DecisionContractError(f"{label}.unit is not approved")
            if row.get("split_adjustment_policy") not in SPLIT_POLICIES:
                raise DecisionContractError(f"{label}.split_adjustment_policy is not approved")
            price = _number(row.get("price"), f"{label}.price")
            forward_eps = _number(row.get("forward_eps"), f"{label}.forward_eps")
            if not math.isclose(forward_eps, _number(eps_record.get("eps_base"), f"{label}.eps_record.eps_base"), rel_tol=1e-12):
                raise DecisionContractError(f"{label}.forward_eps does not match its PIT EPS record")
            multiple = _number(row.get("forward_per"), f"{label}.forward_per")
            if not math.isclose(price / forward_eps, multiple, rel_tol=1e-9, abs_tol=1e-9):
                raise DecisionContractError(f"{label}.forward_per calculation mismatch")
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise DecisionContractError(f"{ticker} PER dates must be unique and chronological")
        if (dates[-1] - dates[0]).days < 730:
            raise DecisionContractError(f"{ticker} PER history spans less than two years")
    return _result(
        "company_pit_forward_per_ledger", classification=classification, schema_valid=True,
        reason="historical PER rows reproduce raw price/EPS and bind independently approved PIT availability proofs",
        manifest_path=manifest_path, manifest_hash=_sha256(raw), evidence=evidence,
        details={
            "row_count": len(normalized),
            "historical_eps_record_count": len(historical_eps),
            "historical_availability_status": "verified",
            "source_hashes": sorted(sources),
        },
    )


def _validate_relative(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...], eps_result: dict[str, Any]
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "relative Forward multiples ledger")
    classification = _common(value, role="relative_forward_multiples", decision_at=decision_at, tickers=tickers)
    sources, evidence = _source_files(manifest_path, value, role="relative_forward_multiples", decision_at=decision_at)
    raw_rows = _raw_list(sources, "relative_forward_multiples")
    rows = value.get("records")
    if not isinstance(rows, list) or len(rows) != len(raw_rows):
        raise DecisionContractError("relative Forward multiples do not match raw bytes")
    normalized: list[dict[str, Any]] = []
    source_available_by_hash: dict[str, datetime] = {}
    for index, ((source_hash, source_available, raw_row), manifest_row) in enumerate(
        zip(raw_rows, rows, strict=True)
    ):
        normalized.append(
            _exact_normalized(
                manifest_row,
                raw_row,
                source_hash=source_hash,
                label=f"relative_forward_multiples.records[{index}]",
            )
        )
        source_available_by_hash[source_hash] = source_available
    roles = [row.get("role") for row in normalized]
    if len(roles) != len(set(roles)):
        raise DecisionContractError("relative Forward multiples contain duplicate roles")
    if set(roles) != {"qqq", "soxx", "peer_basket"}:
        raise DecisionContractError("relative Forward multiples must contain QQQ, SOXX, and peer_basket")
    eps_meta = next(iter(((eps_result.get("details") or {}).get("metadata") or {}).values()), {})
    as_of_values: set[str] = set()
    for row in normalized:
        role = str(row["role"])
        label = f"relative_forward_multiples.{role}"
        as_of_values.add(_session(row.get("as_of"), f"{label}.as_of").isoformat())
        record_available = _available(row, label=label, decision_at=decision_at)
        if source_available_by_hash[str(row["source_hash"])] < record_available:
            raise DecisionContractError(f"{label} source was captured before record availability")
        for field in ("accounting_basis", "forecast_horizon", "currency"):
            if row.get(field) != eps_meta.get(field):
                raise DecisionContractError(f"{label}.{field} does not match PIT EPS basis")
        if row.get("unit") not in PER_UNITS:
            raise DecisionContractError(f"{label}.unit is not approved")
        method = row.get("weighting_method")
        if method not in WEIGHTING_METHODS:
            raise DecisionContractError(f"{label}.weighting_method is not approved")
        constituents = row.get("constituents")
        if not isinstance(constituents, list) or not constituents:
            raise DecisionContractError(f"{label}.constituents are missing")
        names = [item.get("ticker") for item in constituents if isinstance(item, dict)]
        if len(names) != len(constituents) or len(names) != len(set(names)):
            raise DecisionContractError(f"{label}.constituent tickers are missing or duplicated")
        total_market_cap = sum(_number(item.get("market_cap"), f"{label}.constituent.market_cap") for item in constituents)
        recomputed = 0.0
        for index, item in enumerate(constituents):
            item_label = f"{label}.constituents[{index}]"
            for field in ("accounting_basis", "forecast_horizon", "currency"):
                if item.get(field) != eps_meta.get(field):
                    raise DecisionContractError(f"{item_label}.{field} does not match PIT EPS basis")
            constituent_evidence = {
                "ticker": item.get("ticker"),
                "accounting_basis": item.get("accounting_basis"),
                "forecast_horizon": item.get("forecast_horizon"),
                "currency": item.get("currency"),
                "as_of": item.get("as_of"),
                "price": item.get("price"),
                "forward_eps": item.get("forward_eps"),
            }
            source_record_hash = _hash(
                item.get("source_record_hash"), f"{item_label}.source_record_hash"
            )
            if source_record_hash != canonical_record_hash(constituent_evidence):
                raise DecisionContractError(
                    f"{item_label}.source_record_hash does not match constituent fields"
                )
            if _session(item.get("as_of"), f"{item_label}.as_of").isoformat() != row.get("as_of"):
                raise DecisionContractError(f"{item_label}.as_of does not match aggregate session")
            price = _number(item.get("price"), f"{item_label}.price")
            forward_eps = _number(item.get("forward_eps"), f"{item_label}.forward_eps")
            multiple = _number(item.get("forward_multiple"), f"{item_label}.forward_multiple")
            if not math.isclose(price / forward_eps, multiple, rel_tol=1e-9, abs_tol=1e-9):
                raise DecisionContractError(f"{item_label}.forward_multiple calculation mismatch")
            weight = _number(item.get("weight"), f"{item_label}.weight")
            expected_weight = 1 / len(constituents) if method == "equal_weight" else float(item["market_cap"]) / total_market_cap
            if not math.isclose(weight, expected_weight, rel_tol=1e-9, abs_tol=1e-9):
                raise DecisionContractError(f"{item_label}.weight does not match {method}")
            recomputed += weight * multiple
        aggregate = _number(row.get("forward_multiple"), f"{label}.forward_multiple")
        if not math.isclose(aggregate, recomputed, rel_tol=1e-9, abs_tol=1e-9):
            raise DecisionContractError(f"{label}.forward_multiple does not match constituent recomputation")
    if len(as_of_values) != 1:
        raise DecisionContractError("relative Forward multiples do not share one as_of date")
    return _result(
        "independent_qqq_soxx_peer_forward_multiples", classification=classification,
        schema_valid=True, reason="QQQ, SOXX, and peer aggregates reproduce constituent price/EPS multiples",
        manifest_path=manifest_path, manifest_hash=_sha256(raw), evidence=evidence,
        details={"as_of": next(iter(as_of_values)), "source_hashes": sorted(sources)},
    )


def _validate_filing(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...]
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "filing timing manifest")
    classification = _common(value, role="filing_timing", decision_at=decision_at, tickers=tickers)
    sources, evidence = _source_files(manifest_path, value, role="filing_timing", decision_at=decision_at)
    raw_rows = _raw_list(sources, "filing_timing")
    rows = value.get("records")
    if not isinstance(rows, list) or len(rows) != len(raw_rows) or len(rows) != len(tickers):
        raise DecisionContractError("filing_timing records do not match raw bytes")
    seen: set[str] = set()
    for index, ((source_hash, _, raw_row), manifest_row) in enumerate(zip(raw_rows, rows, strict=True)):
        row = _exact_normalized(manifest_row, raw_row, source_hash=source_hash,
                                label=f"filing_timing.records[{index}]")
        ticker = row.get("ticker")
        if ticker not in tickers or ticker in seen:
            raise DecisionContractError("filing_timing ticker set is invalid")
        seen.add(str(ticker))
        filing_date = _date(row.get("filing_date"), f"{ticker}.filing_date")
        usable = _session(row.get("usable_from_session"), f"{ticker}.usable_from_session")
        basis = row.get("timing_basis")
        if basis not in TIMING_BASES:
            raise DecisionContractError(f"{ticker}.timing_basis is not approved")
        if basis == "conservative_next_session":
            if row.get("accepted_at") is not None or usable != next_xnys_session(filing_date):
                raise DecisionContractError(f"{ticker} conservative usable date is not the next XNYS session")
        else:
            accepted = _timestamp(row.get("accepted_at"), f"{ticker}.accepted_at")
            if accepted > decision_at:
                raise DecisionContractError(f"{ticker}.accepted_at is future information")
            accepted_day = accepted.date()
            if is_xnys_session(accepted_day):
                _, close = xnys_session_bounds(accepted_day)
                expected = accepted_day if accepted < close else next_xnys_session(accepted_day)
            else:
                expected = next_xnys_session(accepted_day)
            if usable != expected:
                raise DecisionContractError(f"{ticker} SEC acceptance does not map to the correct XNYS session")
        if usable > decision_at.date():
            raise DecisionContractError(f"{ticker}.usable_from_session is after decision_time")
    return _result(
        "sec_acceptance_or_conservative_next_session", classification=classification,
        schema_valid=True, reason="filing timestamps map to an exact XNYS session with after-close handling",
        manifest_path=manifest_path, manifest_hash=_sha256(raw), evidence=evidence,
        details={"source_hashes": sorted(sources)},
    )


def _validate_prices(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...]
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "adjusted price ledger")
    classification = _common(value, role="adjusted_price_history", decision_at=decision_at, tickers=tickers)
    sources, evidence = _source_files(manifest_path, value, role="adjusted_price_history", decision_at=decision_at)
    if value.get("adjustment_policy") not in PRICE_ADJUSTMENT_POLICIES:
        raise DecisionContractError("adjusted price ledger adjustment_policy is not approved")
    if value.get("calendar") != "XNYS":
        raise DecisionContractError("adjusted price ledger calendar must be XNYS")
    if not isinstance(value.get("corporate_action_policy"), str) or not value["corporate_action_policy"].strip():
        raise DecisionContractError("adjusted price ledger corporate_action_policy is missing")
    raw_records = _raw_dict(sources, "adjusted_price_history")
    records = value.get("records")
    if not isinstance(records, dict) or set(records) != set(raw_records):
        raise DecisionContractError("adjusted price symbol set does not match raw bytes")
    symbols = (*tickers, "QQQ", "SOXX")
    if set(records) != set(symbols):
        raise DecisionContractError("adjusted price ledger must contain target tickers, QQQ, and SOXX")
    expected_latest = latest_completed_session(decision_at).session_date
    for symbol in symbols:
        source_hash, raw_rows = raw_records[symbol]
        rows = records[symbol]
        if not isinstance(rows, list) or len(rows) != len(raw_rows) or len(rows) < 200:
            raise DecisionContractError(f"{symbol} adjusted price history has fewer than 200 raw-bound rows")
        dates: list[date] = []
        source_available = sources[source_hash]["available_at"]
        latest_record_available: datetime | None = None
        for index, (raw_row_value, manifest_row) in enumerate(zip(raw_rows, rows, strict=True)):
            raw_row = _object(raw_row_value, f"{symbol}.raw[{index}]")
            row = _exact_normalized(
                manifest_row,
                raw_row,
                source_hash=source_hash,
                label=f"{symbol}.prices[{index}]",
                extra={"original_source_hash": source_hash},
            )
            label = f"{symbol}.prices[{index}]"
            if row.get("price_basis") not in {"close", "adjusted_close"}:
                raise DecisionContractError(f"{label}.price_basis is not approved")
            session = _session(row.get("session"), f"{label}.session")
            dates.append(session)
            _, expected_close = xnys_session_bounds(session)
            declared_close = _timestamp(row.get("session_close_at"), f"{label}.session_close_at")
            if declared_close != expected_close:
                raise DecisionContractError(f"{label}.session_close_at does not match XNYS close")
            record_available = _available(row, label=label, decision_at=decision_at)
            if record_available < expected_close:
                raise DecisionContractError(f"{label}.available_at is before XNYS session close")
            if record_available.date() != session:
                raise DecisionContractError(f"{label}.available_at is backdated or delayed to another session")
            if row.get("corporate_action_basis") != value.get("adjustment_policy"):
                raise DecisionContractError(f"{label}.corporate_action_basis does not match ledger policy")
            if source_available < record_available:
                raise DecisionContractError(f"{label} source was captured before record availability")
            latest_record_available = max(latest_record_available or record_available, record_available)
            _number(row.get("adjusted_close"), f"{symbol}.prices[{index}].adjusted_close")
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise DecisionContractError(f"{symbol} adjusted price dates must be unique and chronological")
        if dates[-1].isoformat() != expected_latest:
            raise DecisionContractError(
                f"{symbol} latest price session {dates[-1]} is not latest completed XNYS session {expected_latest}"
            )
        if latest_record_available is None:
            raise DecisionContractError(f"{symbol} adjusted price availability is missing")
    return _result(
        "daily_adjusted_price_history", classification=classification, schema_valid=True,
        reason="raw-bound adjusted histories contain only XNYS sessions through the latest completed session",
        manifest_path=manifest_path, manifest_hash=_sha256(raw), evidence=evidence,
        details={"latest_session": expected_latest, "source_hashes": sorted(sources)},
    )


def _validate_regime(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...]
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "regime manifest")
    classification = _common(value, role="regime", decision_at=decision_at, tickers=tickers)
    sources, evidence = _source_files(manifest_path, value, role="regime", decision_at=decision_at)
    raw_rows = _raw_list(sources, "regime")
    rows = value.get("records")
    if not isinstance(rows, list) or len(rows) != 1 or len(raw_rows) != 1:
        raise DecisionContractError("regime must contain exactly one raw-bound record")
    source_hash, source_available, raw_row = raw_rows[0]
    row = _exact_normalized(rows[0], raw_row, source_hash=source_hash, label="regime.records[0]")
    as_of = _session(row.get("as_of"), "regime.as_of")
    record_available = _available(row, label="regime", decision_at=decision_at)
    if source_available < record_available:
        raise DecisionContractError("regime source was captured before record availability")
    if row.get("risk_state") not in REGIME_STATES:
        raise DecisionContractError("regime.risk_state is not approved")
    if row.get("source_kind") != "cross_project_market_proxy":
        raise DecisionContractError("regime.source_kind is not an approved proxy")
    if not isinstance(row.get("indicators"), dict) or not row["indicators"]:
        raise DecisionContractError("regime.indicators are missing")
    return {
        "classification": classification, "as_of": as_of.isoformat(),
        "manifest_path": manifest_path, "manifest_hash": _sha256(raw),
        "evidence": evidence, "source_hashes": sorted(sources),
    }


def _load_candidate_registry(
    path: str | Path | None,
    *,
    label: str,
    schema_version: str,
    decision_at: datetime,
) -> tuple[Path, bytes, dict[str, Any]]:
    if path is None:
        raise DecisionContractError(f"{label} is missing")
    registry_path, raw, value = _load(path, label)
    if value.get("schema_version") != schema_version:
        raise DecisionContractError(f"{label} schema_version is not approved")
    if not isinstance(value.get("generated_by"), str) or not value["generated_by"].strip():
        raise DecisionContractError(f"{label} generated_by is missing")
    if _timestamp(value.get("generated_at"), f"{label}.generated_at") > decision_at:
        raise DecisionContractError(f"{label} was generated after decision_time")
    return registry_path, raw, value


def _validate_provenance_registry(
    path: str | Path | None,
    *,
    decision_at: datetime,
    source_evidence: list[dict[str, Any]],
    actual_evidence_root: str | Path | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry_path, raw, value = _load_candidate_registry(
        path,
        label="provenance candidate registry",
        schema_version=PROVENANCE_REGISTRY_VERSION,
        decision_at=decision_at,
    )
    approved_root = Path(actual_evidence_root).resolve() if actual_evidence_root else None
    if approved_root is None:
        raise DecisionContractError("actual evidence root is not approved")
    records = value.get("records")
    if not isinstance(records, list) or not records:
        raise DecisionContractError("provenance registry records are missing")
    by_hash: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(records):
        row = _object(item, f"provenance.records[{index}]")
        normalized_hash = _hash(
            row.get("normalized_file_sha256"), "provenance.normalized_file_sha256"
        )
        if normalized_hash in by_hash:
            raise DecisionContractError("provenance registry contains duplicate normalized hashes")
        for field in (
            "provider",
            "captured_at",
            "provider_timestamp",
            "original_mime_type",
            "parser_name",
            "parser_version",
            "original_file_path",
            "normalized_file_path",
            "normalized_records_sha256",
        ):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise DecisionContractError(f"provenance.{field} is missing")
        if not (
            row.get("original_source_url")
            or row.get("official_document_id")
            or row.get("local_collection_path")
        ):
            raise DecisionContractError("provenance source URL or official document id is missing")
        if _timestamp(row["captured_at"], "provenance.captured_at") > decision_at:
            raise DecisionContractError("provenance capture is later than decision_time")
        original_path = Path(row["original_file_path"])
        if not original_path.is_absolute():
            original_path = (registry_path.parent / original_path).resolve()
        else:
            original_path = original_path.resolve()
        normalized_path = Path(row["normalized_file_path"])
        if not normalized_path.is_absolute():
            normalized_path = (registry_path.parent / normalized_path).resolve()
        else:
            normalized_path = normalized_path.resolve()
        try:
            original_path.relative_to(approved_root)
            normalized_path.relative_to(approved_root)
        except ValueError as exc:
            raise DecisionContractError("provenance source path is outside the approved root") from exc
        original_hash = _hash(
            row.get("original_file_sha256"), "provenance.original_file_sha256"
        )
        if not original_path.is_file() or _sha256(original_path.read_bytes()) != original_hash:
            raise DecisionContractError("provenance source bytes do not match original_file_sha256")
        if not normalized_path.is_file() or _sha256(normalized_path.read_bytes()) != normalized_hash:
            raise DecisionContractError("provenance normalized bytes do not match normalized_file_sha256")
        if original_path == normalized_path:
            raise DecisionContractError("provenance original and normalized files must differ")
        by_hash[normalized_hash] = {
            **row,
            "resolved_original_path": str(original_path),
            "resolved_normalized_path": str(normalized_path),
            "original_file_sha256": original_hash,
        }
    required = {str(row["source_hash"]): row for row in source_evidence}
    if set(required) != set(by_hash):
        raise DecisionContractError("provenance registry does not exactly cover decision source hashes")
    for source_hash, evidence in required.items():
        registry_row = by_hash[source_hash]
        if Path(evidence["path"]).resolve() != Path(registry_row["resolved_normalized_path"]):
            raise DecisionContractError("provenance normalized path does not match current contract source")
        if Path(evidence["provider_native_path"]).resolve() != Path(
            registry_row["resolved_original_path"]
        ):
            raise DecisionContractError("provenance original path does not match provider-native raw")
        if evidence.get("provider_native_hash") != registry_row.get("original_file_sha256"):
            raise DecisionContractError("provenance original hash does not match provider-native raw")
        if evidence.get("normalized_records_hash") != _hash(
            registry_row.get("normalized_records_sha256"),
            "provenance.normalized_records_sha256",
        ):
            raise DecisionContractError("provenance normalized records hash does not match")
        if evidence.get("provider") != registry_row.get("provider"):
            raise DecisionContractError("provenance provider does not match current contract source")
        for evidence_field, registry_field in (
            ("provider_captured_at", "captured_at"),
            ("provider_timestamp", "provider_timestamp"),
            ("parser_name", "parser_name"),
            ("parser_version", "parser_version"),
            ("revision_id", "revision_id"),
            ("vintage_id", "vintage_id"),
        ):
            if evidence.get(evidence_field) != registry_row.get(registry_field):
                raise DecisionContractError(
                    f"provenance {registry_field} does not match current contract source"
                )
    registry_evidence = [{
        "role": "candidate_registry:provenance",
        "path": str(registry_path.resolve()),
        "source_hash": _sha256(raw),
    }]
    return {
        "registry_hash": _sha256(raw),
        "generated_by": value["generated_by"],
        "generated_at": value["generated_at"],
        "providers": sorted({str(row["provider"]) for row in by_hash.values()}),
        "covered_source_hashes": sorted(by_hash),
    }, registry_evidence


def _validate_usage_policy_registry(
    path: str | Path | None,
    *,
    decision_at: datetime,
    provenance: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry_path, raw, value = _load_candidate_registry(
        path,
        label="usage policy candidate registry",
        schema_version=USAGE_POLICY_REGISTRY_VERSION,
        decision_at=decision_at,
    )
    records = value.get("records")
    if not isinstance(records, list) or not records:
        raise DecisionContractError("usage policy registry records are missing")
    providers: set[str] = set()
    evidence: list[dict[str, Any]] = [{
        "role": "candidate_registry:usage_policy",
        "path": str(registry_path.resolve()),
        "source_hash": _sha256(raw),
    }]
    for index, item in enumerate(records):
        row = _object(item, f"usage_policy.records[{index}]")
        provider = row.get("provider")
        if not isinstance(provider, str) or not provider.strip() or provider in providers:
            raise DecisionContractError("usage policy provider is missing or duplicated")
        providers.add(provider)
        if row.get("usage_scope") not in USAGE_SCOPES:
            raise DecisionContractError(f"usage policy for {provider} has an invalid scope")
        for field in ("storage_allowed", "derivative_analysis_allowed", "local_display_allowed"):
            if row.get(field) is not True:
                raise DecisionContractError(f"usage policy for {provider} does not permit {field}")
        clauses = row.get("terms_clause_ids")
        if not isinstance(clauses, list) or not clauses or not all(
            isinstance(clause, str) and clause.strip() for clause in clauses
        ):
            raise DecisionContractError(f"usage policy for {provider} has no terms clauses")
        reviewed = _timestamp(row.get("reviewed_at"), f"usage_policy[{provider}].reviewed_at")
        expires = _timestamp(row.get("expires_at"), f"usage_policy[{provider}].expires_at")
        if reviewed > decision_at or expires <= decision_at:
            raise DecisionContractError(f"usage policy for {provider} is not valid at decision_time")
        terms_path_value = row.get("terms_document_path")
        if not isinstance(terms_path_value, str) or not terms_path_value:
            raise DecisionContractError(f"usage policy for {provider} terms document is missing")
        terms_path = Path(terms_path_value)
        if not terms_path.is_absolute():
            terms_path = (registry_path.parent / terms_path).resolve()
        expected_terms_hash = _hash(
            row.get("terms_document_sha256"), f"usage_policy[{provider}].terms_document_sha256"
        )
        if not terms_path.is_file() or _sha256(terms_path.read_bytes()) != expected_terms_hash:
            raise DecisionContractError(f"usage policy for {provider} terms document hash mismatch")
        evidence.append({
            "role": f"usage_terms:{provider}",
            "path": str(terms_path),
            "source_hash": expected_terms_hash,
        })
    if providers != set(provenance.get("providers") or []):
        raise DecisionContractError("usage policy registry does not exactly cover provenance providers")
    return {
        "registry_hash": _sha256(raw),
        "generated_by": value["generated_by"],
        "generated_at": value["generated_at"],
        "providers": sorted(providers),
        "covered_source_hashes": provenance.get("covered_source_hashes") or [],
    }, evidence


def _validate_independent_approval_receipt(
    path: str | Path | None,
    approved_sha256: str | None,
    *,
    decision_at: datetime,
    decision_time: str,
    tickers: tuple[str, ...],
    provenance: dict[str, Any],
    usage_policy: dict[str, Any],
    actual_evidence_root: str | Path | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path is None:
        raise DecisionContractError("independent approval receipt is missing")
    if approved_sha256 is None:
        raise DecisionContractError("independent approval receipt approved SHA-256 is missing")
    receipt_path, raw, value = _load(path, "independent approval receipt")
    receipt_hash = _sha256(raw)
    if _hash(
        approved_sha256,
        "independent approval receipt approved SHA-256",
    ) != receipt_hash:
        raise DecisionContractError("independent approval receipt SHA-256 mismatch")
    if value.get("schema_version") != APPROVAL_RECEIPT_VERSION:
        raise DecisionContractError("independent approval receipt schema_version is not approved")
    if value.get("status") != "approved":
        raise DecisionContractError("independent approval receipt status is not approved")
    if value.get("approval_scope") != APPROVAL_SCOPE:
        raise DecisionContractError("independent approval receipt scope is not approved")
    for field in ("approved_by", "approval_receipt_id"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise DecisionContractError(f"independent approval receipt {field} is missing")
    if value["approved_by"] in {provenance.get("generated_by"), usage_policy.get("generated_by")}:
        raise DecisionContractError("candidate generator cannot approve its own registries")
    approved_at = _timestamp(value.get("approved_at"), "independent approval receipt approved_at")
    expires_at = _timestamp(value.get("expires_at"), "independent approval receipt expires_at")
    if approved_at > decision_at or expires_at <= decision_at:
        raise DecisionContractError("independent approval receipt is not valid at decision_time")
    if approved_at < max(
        _timestamp(provenance["generated_at"], "provenance candidate generated_at"),
        _timestamp(usage_policy["generated_at"], "usage policy candidate generated_at"),
    ):
        raise DecisionContractError("independent approval predates candidate registries")
    if value.get("decision_time") != decision_time:
        raise DecisionContractError("independent approval receipt decision_time does not match")
    if value.get("target_tickers") != list(tickers):
        raise DecisionContractError("independent approval receipt target_tickers do not match")
    if value.get("operational_eligible") is not False:
        raise DecisionContractError("independent approval receipt must not grant operating eligibility")
    approved_registries = _object(
        value.get("approved_registry_sha256"),
        "independent approval receipt approved_registry_sha256",
    )
    expected_registries = {
        "provenance_candidate": provenance["registry_hash"],
        "usage_policy_candidate": usage_policy["registry_hash"],
    }
    if set(approved_registries) != set(expected_registries):
        raise DecisionContractError("independent approval receipt registry set is incomplete")
    for role, expected_hash in expected_registries.items():
        if _hash(approved_registries.get(role), f"approved registry {role}") != expected_hash:
            raise DecisionContractError(f"independent approval receipt {role} hash mismatch")
    if actual_evidence_root is not None:
        candidate_root = Path(actual_evidence_root).resolve()
        try:
            receipt_path.resolve().relative_to(candidate_root)
        except ValueError:
            pass
        else:
            raise DecisionContractError(
                "independent approval receipt must be stored outside the candidate evidence root"
            )
    return {
        "receipt_hash": receipt_hash,
        "approved_by": value["approved_by"],
        "approved_at": approved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "approval_scope": value["approval_scope"],
        "approval_receipt_id": value["approval_receipt_id"],
    }, [{
        "role": "independent_approval_receipt",
        "path": str(receipt_path.resolve()),
        "source_hash": receipt_hash,
    }]


def build_candidate_registries(
    contract_paths: dict[str, str | Path],
    *,
    actual_evidence_root: str | Path,
    usage_policy_input_path: str | Path,
    output_dir: str | Path,
    generated_by: str,
    generated_at: str,
) -> dict[str, Any]:
    """Create candidate-only registries; this function cannot create or approve a receipt."""
    if not generated_by.strip():
        raise DecisionContractError("candidate generated_by is missing")
    generated = _timestamp(generated_at, "candidate generated_at")
    root = Path(actual_evidence_root).resolve()
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise DecisionContractError("candidate registry output must be inside evidence root") from exc

    provenance_records: list[dict[str, Any]] = []
    seen_normalized: set[str] = set()
    for role in CONTRACT_ROLES:
        manifest_value = contract_paths.get(role)
        if manifest_value is None:
            raise DecisionContractError(f"{role} manifest is missing")
        manifest_path, _, manifest = _load(manifest_value, f"{role} manifest")
        if manifest.get("contract_version") != CONTRACT_VERSION:
            raise DecisionContractError(f"{role} contract_version is not approved")
        source_rows = manifest.get("source_files")
        if not isinstance(source_rows, list) or not source_rows:
            raise DecisionContractError(f"{role}.source_files are missing")
        for index, source_row_value in enumerate(source_rows):
            source_row = _object(source_row_value, f"{role}.source_files[{index}]")
            normalized_path = (manifest_path.parent / str(source_row.get("path"))).resolve()
            try:
                normalized_path.relative_to(root)
            except ValueError as exc:
                raise DecisionContractError("normalized source path is outside evidence root") from exc
            _, normalized_raw, normalized = _load(normalized_path, "normalized contract source")
            normalized_hash = _sha256(normalized_raw)
            if _hash(source_row.get("source_hash"), f"{role}.source_hash") != normalized_hash:
                raise DecisionContractError(f"{role} normalized source hash mismatch")
            if normalized_hash in seen_normalized:
                continue
            seen_normalized.add(normalized_hash)
            native = _object(normalized.get("provider_native"), "provider_native")
            original_path = (normalized_path.parent / str(native.get("path"))).resolve()
            try:
                original_path.relative_to(root)
            except ValueError as exc:
                raise DecisionContractError("provider-native source path is outside evidence root") from exc
            original_hash = _hash(native.get("source_hash"), "provider_native.source_hash")
            if not original_path.is_file() or _sha256(original_path.read_bytes()) != original_hash:
                raise DecisionContractError("provider-native source hash mismatch")
            provenance_records.append({
                "provider": normalized.get("provider"),
                "original_source_url": native.get("source_url"),
                "local_collection_path": native.get("local_collection_path"),
                "captured_at": _timestamp(
                    native.get("captured_at"), "provider_native.captured_at"
                ).isoformat(),
                "provider_timestamp": _timestamp(
                    native.get("provider_timestamp"), "provider_native.provider_timestamp"
                ).isoformat(),
                "original_mime_type": native.get("mime_type"),
                "original_file_sha256": original_hash,
                "original_file_path": os.path.relpath(original_path, destination),
                "normalized_file_sha256": normalized_hash,
                "normalized_file_path": os.path.relpath(normalized_path, destination),
                "normalized_records_sha256": normalized.get("normalized_records_sha256"),
                "parser_name": native.get("parser_name"),
                "parser_version": native.get("parser_version"),
                "revision_id": native.get("revision_id"),
                "vintage_id": native.get("vintage_id"),
            })

    _, _, policy_input = _load(usage_policy_input_path, "usage policy input")
    if policy_input.get("schema_version") != "same-date-usage-policy-input-v1":
        raise DecisionContractError("usage policy input schema_version is not approved")
    policy_records = policy_input.get("records")
    if not isinstance(policy_records, list) or not policy_records:
        raise DecisionContractError("usage policy input records are missing")
    provider_set = {str(row.get("provider")) for row in provenance_records}
    if {str(row.get("provider")) for row in policy_records if isinstance(row, dict)} != provider_set:
        raise DecisionContractError("usage policy input does not exactly cover provenance providers")

    provenance_path = destination / "provenance-candidate.json"
    usage_path = destination / "usage-policy-candidate.json"
    provenance_value = {
        "schema_version": PROVENANCE_REGISTRY_VERSION,
        "generated_by": generated_by,
        "generated_at": generated.isoformat(),
        "records": sorted(provenance_records, key=lambda row: str(row["normalized_file_sha256"])),
    }
    usage_value = {
        "schema_version": USAGE_POLICY_REGISTRY_VERSION,
        "generated_by": generated_by,
        "generated_at": generated.isoformat(),
        "records": policy_records,
    }
    provenance_path.write_bytes(_canonical_bytes(provenance_value))
    usage_path.write_bytes(_canonical_bytes(usage_value))
    return {
        "provenance_registry_path": str(provenance_path),
        "provenance_registry_sha256": _sha256(provenance_path.read_bytes()),
        "usage_policy_registry_path": str(usage_path),
        "usage_policy_registry_sha256": _sha256(usage_path.read_bytes()),
        "approval_created": False,
    }


def validate_ready_contracts(
    contract_paths: dict[str, str | Path] | None,
    *, decision_time: str, tickers: tuple[str, ...],
    actual_evidence_root: str | Path | None = None,
    provenance_registry_path: str | Path | None = None,
    usage_policy_registry_path: str | Path | None = None,
    approval_receipt_path: str | Path | None = None,
    approved_approval_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate raw-bound offline manifests without creating a decision."""
    decision_at = _timestamp(decision_time, "decision_time")
    paths = contract_paths or {}
    results: dict[str, dict[str, Any]] = {}
    classifications: set[str] = set()

    def run(role: str, validator: Any, *args: Any) -> dict[str, Any]:
        requirement_id = REQUIREMENT_BY_ROLE[role]
        if role not in paths:
            return _blocked_result(requirement_id, f"{role} manifest is missing")
        try:
            result = validator(paths[role], decision_at, tickers, *args)
        except (OSError, DecisionContractError, ValueError) as exc:
            return _blocked_result(requirement_id, str(exc))
        if isinstance(result.get("evidence_classification"), str):
            classifications.add(result["evidence_classification"])
        return result

    try:
        eps_result, _ = _validate_eps(paths["pit_forward_eps"], decision_at, tickers)
    except KeyError:
        eps_result = _blocked_result("verified_pit_forward_eps", "pit_forward_eps manifest is missing")
    except (OSError, DecisionContractError, ValueError) as exc:
        eps_result = _blocked_result("verified_pit_forward_eps", str(exc))
    if isinstance(eps_result.get("evidence_classification"), str):
        classifications.add(eps_result["evidence_classification"])
    results["verified_pit_forward_eps"] = eps_result
    results["company_pit_forward_per_ledger"] = run("company_pit_forward_per", _validate_company_per, eps_result)
    results["independent_qqq_soxx_peer_forward_multiples"] = run("relative_forward_multiples", _validate_relative, eps_result)
    results["sec_acceptance_or_conservative_next_session"] = run("filing_timing", _validate_filing)
    results["daily_adjusted_price_history"] = run("adjusted_price_history", _validate_prices)

    regime: dict[str, Any] | None = None
    regime_error: str | None = None
    try:
        regime = _validate_regime(paths["regime"], decision_at, tickers)
        classifications.add(regime["classification"])
    except KeyError:
        regime_error = "regime manifest is missing"
    except (OSError, DecisionContractError, ValueError) as exc:
        regime_error = str(exc)

    price_as_of_value = (results["daily_adjusted_price_history"].get("details") or {}).get("latest_session")
    relative_as_of_value = (results["independent_qqq_soxx_peer_forward_multiples"].get("details") or {}).get("as_of")
    eps_dates = {
        str(row.get("as_of"))
        for row in ((eps_result.get("details") or {}).get("metadata") or {}).values()
        if row.get("as_of")
    }
    alignment_error = regime_error
    alignment_details = {
        "decision_time": decision_time, "price_as_of": price_as_of_value,
        "relative_as_of": relative_as_of_value, "eps_as_of": sorted(eps_dates),
        "regime_as_of": regime.get("as_of") if regime else None,
        "eps_max_lag_sessions": EPS_MAX_LAG_SESSIONS,
        "regime_max_lag_sessions": REGIME_MAX_LAG_SESSIONS,
    }
    if alignment_error is None:
        try:
            if not price_as_of_value or relative_as_of_value != price_as_of_value:
                raise DecisionContractError("relative multiples and price do not share the latest session")
            price_as_of = _session(price_as_of_value, "aligned.price_as_of")
            if len(eps_dates) != 1:
                raise DecisionContractError("PIT EPS records do not share one as_of session")
            eps_lag = _session_lag(_session(next(iter(eps_dates)), "aligned.eps_as_of"), price_as_of, "EPS")
            regime_lag = _session_lag(_session(regime["as_of"], "aligned.regime_as_of"), price_as_of, "regime")
            alignment_details.update({"eps_lag_sessions": eps_lag, "regime_lag_sessions": regime_lag})
            if eps_lag > EPS_MAX_LAG_SESSIONS:
                raise DecisionContractError("PIT EPS is stale for the decision session")
            if regime_lag > REGIME_MAX_LAG_SESSIONS:
                raise DecisionContractError("regime is stale for the decision session")
        except (DecisionContractError, ValueError) as exc:
            alignment_error = str(exc)
    classification = next(iter(classifications)) if len(classifications) == 1 else None
    results["same_date_price_and_regime"] = _result(
        "same_date_price_and_regime", classification=classification,
        schema_valid=alignment_error is None,
        reason=("price, EPS, relative multiples, regime, and decision time are aligned" if alignment_error is None else str(alignment_error)),
        details=alignment_details, evidence=(regime.get("evidence") or []) if regime else [],
        manifest_path=regime.get("manifest_path") if regime else None,
        manifest_hash=regime.get("manifest_hash") if regime else None,
    )
    original_valid = bool(eps_result.get("schema_valid") and eps_result.get("evidence"))
    results["original_consensus_bytes"] = _result(
        "original_consensus_bytes", classification=eps_result.get("evidence_classification"),
        schema_valid=original_valid,
        reason=("normalized PIT EPS exactly reproduces preserved consensus bytes" if original_valid else "PIT EPS raw-byte linkage is missing"),
        evidence=list(eps_result.get("evidence") or []),
        details={"source_hashes": (eps_result.get("details") or {}).get("source_hashes") or []},
    )

    if len(classifications) > 1:
        for result in results.values():
            result.update(passed=False, status="blocked", reason="evidence classifications are mixed across contracts")
    source_evidence_by_hash: dict[str, dict[str, Any]] = {}
    for result in results.values():
        for item in result.get("evidence") or []:
            if str(item.get("role", "")).startswith("contract_source:"):
                source_evidence_by_hash[str(item["source_hash"])] = item
    provenance: dict[str, Any] | None = None
    provenance_evidence: list[dict[str, Any]] = []
    provenance_error: str | None = None
    try:
        provenance, provenance_evidence = _validate_provenance_registry(
            provenance_registry_path,
            decision_at=decision_at,
            source_evidence=list(source_evidence_by_hash.values()),
            actual_evidence_root=actual_evidence_root,
        )
    except (OSError, DecisionContractError, ValueError) as exc:
        provenance_error = str(exc)

    rights_error: str | None = None
    rights_evidence: list[dict[str, Any]] = []
    rights: dict[str, Any] | None = None
    if provenance is None:
        rights_error = f"provenance approval failed: {provenance_error}"
    else:
        try:
            rights, rights_evidence = _validate_usage_policy_registry(
                usage_policy_registry_path,
                decision_at=decision_at,
                provenance=provenance,
            )
            results["decision_display_rights"] = _result(
                "decision_display_rights",
                classification=classification,
                schema_valid=True,
                reason="usage-policy candidate and terms evidence cover every provider",
                manifest_path=Path(usage_policy_registry_path) if usage_policy_registry_path else None,
                manifest_hash=rights["registry_hash"],
                evidence=rights_evidence,
                details=rights,
            )
        except (OSError, DecisionContractError, ValueError) as exc:
            rights_error = str(exc)
    if rights_error is not None:
        results["decision_display_rights"] = _blocked_result("decision_display_rights", rights_error)

    approval: dict[str, Any] | None = None
    approval_evidence: list[dict[str, Any]] = []
    approval_error: str | None = None
    if provenance is None or rights is None:
        approval_error = "candidate registries are not valid"
    else:
        try:
            approval, approval_evidence = _validate_independent_approval_receipt(
                approval_receipt_path,
                approved_approval_receipt_sha256,
                decision_at=decision_at,
                decision_time=decision_time,
                tickers=tickers,
                provenance=provenance,
                usage_policy=rights,
                actual_evidence_root=actual_evidence_root,
            )
        except (OSError, DecisionContractError, ValueError) as exc:
            approval_error = str(exc)

    evidence: list[dict[str, Any]] = []
    for result in results.values():
        if result.get("manifest_path") and result.get("manifest_hash"):
            evidence.append({
                "role": f"contract_manifest:{result['id']}", "path": result["manifest_path"],
                "source_hash": result["manifest_hash"],
            })
        evidence.extend(result.get("evidence") or [])
    evidence.extend(provenance_evidence)
    evidence.extend(rights_evidence)
    evidence.extend(approval_evidence)
    unique = {(row["role"], row["path"], row["source_hash"]): row for row in evidence}

    order = (
        "verified_pit_forward_eps", "company_pit_forward_per_ledger",
        "independent_qqq_soxx_peer_forward_multiples", "original_consensus_bytes",
        "same_date_price_and_regime", "sec_acceptance_or_conservative_next_session",
        "daily_adjusted_price_history", "decision_display_rights",
    )
    requirements = [results[key] for key in order]
    trust_ready = (
        classification == "local_evidence_candidate"
        and provenance is not None
        and rights_error is None
        and approval is not None
        and all(bool(row.get("schema_valid")) for row in requirements)
    )
    if trust_ready:
        for row in requirements:
            row.update(
                passed=True,
                status="passed",
                reason=str(row["reason"]).replace(
                    "local evidence candidate validated; external approval required: ", ""
                ),
                evidence_classification="actual_local_evidence",
            )
        output_classification = "actual_local_evidence"
    else:
        output_classification = classification
        trust_error = provenance_error or rights_error or approval_error
        if classification == "local_evidence_candidate" and trust_error:
            for row in requirements:
                if row.get("schema_valid"):
                    row.update(
                        passed=False,
                        status="blocked",
                        reason=f"independent evidence approval failed: {trust_error}",
                    )
    return {
        "contract_version": CONTRACT_VERSION, "evidence_classification": output_classification,
        "decision_time": decision_time, "target_tickers": list(tickers),
        "requirements": requirements, "evidence": list(unique.values()),
        "schema_valid": all(bool(row.get("schema_valid")) for row in requirements),
        "promotion_ready": all(bool(row.get("passed")) for row in requirements),
        "decision_created": False,
        "provenance_registry_hash": provenance.get("registry_hash") if provenance else None,
        "usage_policy_registry_hash": (
            (results.get("decision_display_rights", {}).get("details") or {}).get("registry_hash")
        ),
        "approval_receipt_hash": approval.get("receipt_hash") if approval else None,
        "approval_receipt_id": approval.get("approval_receipt_id") if approval else None,
        "approval_error": approval_error,
    }
