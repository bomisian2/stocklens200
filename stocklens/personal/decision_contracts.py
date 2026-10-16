"""Fail-closed offline contracts for Same-Date decision promotion.

Every normalized row is reconstructed from preserved source bytes. The
module performs no network access and never creates an investment decision.
"""

from __future__ import annotations

import hashlib
import json
import math
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

CONTRACT_VERSION = "same-date-ready-contract-v1"
EVIDENCE_CLASSES = {"synthetic_fixture", "actual_local_evidence"}
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
EPS_MAX_LAG_SESSIONS = 1
REGIME_MAX_LAG_SESSIONS = 1

CONTRACT_ROLES = (
    "pit_forward_eps",
    "company_pit_forward_per",
    "relative_forward_multiples",
    "filing_timing",
    "adjusted_price_history",
    "regime",
    "rights",
)
REQUIREMENT_BY_ROLE = {
    "pit_forward_eps": "verified_pit_forward_eps",
    "company_pit_forward_per": "company_pit_forward_per_ledger",
    "relative_forward_multiples": "independent_qqq_soxx_peer_forward_multiples",
    "filing_timing": "sec_acceptance_or_conservative_next_session",
    "adjusted_price_history": "daily_adjusted_price_history",
    "rights": "decision_display_rights",
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
    if value.get("schema_version") != f"{role.replace('_', '-')}-v1":
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
    expected_schema = f"{role.replace('_', '-')}-source-v1"
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
        available_at = _timestamp(row.get("available_at"), f"{role}.source_files[{index}].available_at")
        if available_at > decision_at:
            raise DecisionContractError(f"{role}.source_files[{index}] is future information")
        if actual_hash in sources:
            raise DecisionContractError(f"{role}.source_files contain a duplicate source_hash")
        sources[actual_hash] = {
            "path": source_path,
            "raw": raw,
            "value": parsed,
            "available_at": available_at,
        }
        evidence.append({
            "role": f"contract_source:{role}:{index}",
            "path": str(source_path),
            "source_hash": actual_hash,
            "available_at": available_at.isoformat(),
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
    actual = schema_valid and classification == "actual_local_evidence"
    status = "passed" if actual else ("fixture_validated" if schema_valid else "blocked")
    if schema_valid and not actual:
        reason = f"synthetic fixture validated; not actual evidence: {reason}"
    return {
        "id": requirement_id,
        "passed": actual,
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
            "canonical_record_hash": canonical_hash,
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
    raw_rows = _raw_list(sources, "company_pit_forward_per")
    rows = value.get("records")
    if not isinstance(rows, list) or len(rows) != len(raw_rows):
        raise DecisionContractError("company_pit_forward_per records do not match raw bytes")
    normalized: list[dict[str, Any]] = []
    for index, ((source_hash, _, raw_row), manifest_row) in enumerate(zip(raw_rows, rows, strict=True)):
        eps_record = _object(raw_row.get("eps_record"), f"per[{index}].eps_record")
        normalized.append(_exact_normalized(
            manifest_row, raw_row, source_hash=source_hash,
            label=f"company_pit_forward_per.records[{index}]",
            extra={"underlying_eps_record_hash": canonical_record_hash(eps_record)},
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
            eps_record = _object(row.get("eps_record"), f"{label}.eps_record")
            if eps_record.get("ticker") != ticker:
                raise DecisionContractError(f"{label}.eps_record ticker does not match")
            eps_available = _timestamp(eps_record.get("available_at"), f"{label}.eps_record.available_at")
            source_available = _timestamp(row.get("eps_source_available_at"), f"{label}.eps_source_available_at")
            session_open, _ = xnys_session_bounds(observation)
            if not source_available <= eps_available < session_open:
                raise DecisionContractError(f"{label} violates EPS source <= EPS record < PER observation session")
            current = current_meta.get(ticker) or {}
            for field in ("accounting_basis", "forecast_horizon", "currency"):
                if row.get(field) != current.get(field) or eps_record.get(field) != row.get(field):
                    raise DecisionContractError(f"{label}.{field} does not match PIT EPS basis")
            if eps_record.get("unit") not in EPS_UNITS:
                raise DecisionContractError(f"{label}.eps_record.unit is not approved")
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
        reason="historical PER rows reproduce raw price/EPS and bind exact prior-available EPS records",
        manifest_path=manifest_path, manifest_hash=_sha256(raw), evidence=evidence,
        details={"row_count": len(normalized), "source_hashes": sorted(sources)},
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
    normalized = [
        _exact_normalized(manifest_row, raw_row, source_hash=source_hash,
                          label=f"relative_forward_multiples.records[{index}]")
        for index, ((source_hash, _, raw_row), manifest_row) in enumerate(zip(raw_rows, rows, strict=True))
    ]
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
        _available(row, label=label, decision_at=decision_at)
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
        for index, (raw_row_value, manifest_row) in enumerate(zip(raw_rows, rows, strict=True)):
            raw_row = _object(raw_row_value, f"{symbol}.raw[{index}]")
            row = _exact_normalized(manifest_row, raw_row, source_hash=source_hash,
                                    label=f"{symbol}.prices[{index}]")
            dates.append(_session(row.get("date"), f"{symbol}.prices[{index}].date"))
            _number(row.get("adjusted_close"), f"{symbol}.prices[{index}].adjusted_close")
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise DecisionContractError(f"{symbol} adjusted price dates must be unique and chronological")
        if dates[-1].isoformat() != expected_latest:
            raise DecisionContractError(
                f"{symbol} latest price session {dates[-1]} is not latest completed XNYS session {expected_latest}"
            )
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
    source_hash, _, raw_row = raw_rows[0]
    row = _exact_normalized(rows[0], raw_row, source_hash=source_hash, label="regime.records[0]")
    as_of = _session(row.get("as_of"), "regime.as_of")
    _available(row, label="regime", decision_at=decision_at)
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


def _validate_rights(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...], required_hashes: set[str]
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "rights manifest")
    classification = _common(value, role="rights", decision_at=decision_at, tickers=tickers)
    sources, evidence = _source_files(manifest_path, value, role="rights", decision_at=decision_at)
    raw_rows = _raw_list(sources, "rights")
    records = value.get("records")
    if not isinstance(records, list) or len(records) != len(raw_rows):
        raise DecisionContractError("rights records do not match raw bytes")
    normalized = [
        _exact_normalized(manifest_row, raw_row, source_hash=source_hash,
                          label=f"rights.records[{index}]")
        for index, ((source_hash, _, raw_row), manifest_row) in enumerate(zip(raw_rows, records, strict=True))
    ]
    by_hash: dict[str, dict[str, Any]] = {}
    for row in normalized:
        evidence_hash = _hash(row.get("evidence_source_hash"), "rights.evidence_source_hash")
        if evidence_hash in by_hash:
            raise DecisionContractError("rights records contain a duplicate evidence_source_hash")
        by_hash[evidence_hash] = row
        if row.get("usage_scope") not in USAGE_SCOPES:
            raise DecisionContractError(f"rights for {evidence_hash} have an invalid usage_scope")
        for field in ("storage_allowed", "derivative_analysis_allowed", "local_display_allowed"):
            if row.get(field) is not True:
                raise DecisionContractError(f"rights for {evidence_hash} do not permit {field}")
        reviewed = _timestamp(row.get("reviewed_at"), f"rights[{evidence_hash}].reviewed_at")
        expires = _timestamp(row.get("expires_at"), f"rights[{evidence_hash}].expires_at")
        if reviewed > decision_at:
            raise DecisionContractError(f"rights for {evidence_hash} were reviewed after decision_time")
        if expires <= decision_at:
            raise DecisionContractError(f"rights for {evidence_hash} are expired")
    if required_hashes - set(by_hash):
        raise DecisionContractError("rights manifest does not cover every decision source_hash")
    return _result(
        "decision_display_rights", classification=classification, schema_valid=True,
        reason="local storage, derivative analysis, and display rights cover every evidence source",
        manifest_path=manifest_path, manifest_hash=_sha256(raw), evidence=evidence,
        details={"covered_source_hashes": sorted(required_hashes), "source_hashes": sorted(sources)},
    )


def validate_ready_contracts(
    contract_paths: dict[str, str | Path] | None,
    *, decision_time: str, tickers: tuple[str, ...],
    actual_evidence_root: str | Path | None = None,
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

    required_hashes: set[str] = set()
    for result in results.values():
        required_hashes.update((result.get("details") or {}).get("source_hashes") or [])
    if regime:
        required_hashes.update(regime["source_hashes"])
    results["decision_display_rights"] = run("rights", _validate_rights, required_hashes)

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
    evidence: list[dict[str, Any]] = []
    for result in results.values():
        if result.get("manifest_path") and result.get("manifest_hash"):
            evidence.append({
                "role": f"contract_manifest:{result['id']}", "path": result["manifest_path"],
                "source_hash": result["manifest_hash"],
            })
        evidence.extend(result.get("evidence") or [])
    unique = {(row["role"], row["path"], row["source_hash"]): row for row in evidence}
    if classification == "actual_local_evidence":
        approved_root = Path(actual_evidence_root).resolve() if actual_evidence_root else None
        root_error = None
        if approved_root is None:
            root_error = "actual evidence root is not approved"
        else:
            for row in unique.values():
                try:
                    Path(row["path"]).resolve().relative_to(approved_root)
                except (KeyError, TypeError, ValueError):
                    root_error = "actual evidence path is outside the approved root"
                    break
        if root_error:
            for result in results.values():
                result.update(passed=False, status="blocked", reason=root_error)

    order = (
        "verified_pit_forward_eps", "company_pit_forward_per_ledger",
        "independent_qqq_soxx_peer_forward_multiples", "original_consensus_bytes",
        "same_date_price_and_regime", "sec_acceptance_or_conservative_next_session",
        "daily_adjusted_price_history", "decision_display_rights",
    )
    requirements = [results[key] for key in order]
    return {
        "contract_version": CONTRACT_VERSION, "evidence_classification": classification,
        "decision_time": decision_time, "target_tickers": list(tickers),
        "requirements": requirements, "evidence": list(unique.values()),
        "schema_valid": all(bool(row.get("schema_valid")) for row in requirements),
        "promotion_ready": all(bool(row.get("passed")) for row in requirements),
        "decision_created": False,
    }
