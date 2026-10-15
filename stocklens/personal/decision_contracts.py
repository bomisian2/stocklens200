"""Strict offline evidence contracts for Same-Date decision promotion.

The validators in this module do not fetch data and do not create a decision.
They bind normalized records to preserved source bytes and distinguish a
synthetic fixture from evidence that is eligible for a local production audit.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "same-date-ready-contract-v1"
EVIDENCE_CLASSES = {"synthetic_fixture", "actual_local_evidence"}
ACCOUNTING_BASES = {"gaap", "non_gaap"}
FORECAST_HORIZONS = {"FY1", "NTM"}
PER_UNITS = {"multiple"}
EPS_UNITS = {"currency_per_diluted_share"}
USAGE_SCOPES = {"personal_local_research"}
WEIGHTING_METHODS = {"equal_weight", "market_cap"}
TIMING_BASES = {"sec_acceptance_timestamp", "conservative_next_session"}
PRICE_ADJUSTMENT_POLICIES = {"split_and_cash_dividend_adjusted"}
SPLIT_POLICIES = {"price_and_eps_split_adjusted"}

CONTRACT_ROLES = (
    "pit_forward_eps",
    "company_pit_forward_per",
    "relative_forward_multiples",
    "filing_timing",
    "adjusted_price_history",
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
    """Raised when an offline contract is malformed or internally inconsistent."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


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
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise DecisionContractError(f"{label} must be a finite positive number")
    return number


def _load(path: str | Path, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise DecisionContractError(f"{label} is missing")
    raw = source.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionContractError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DecisionContractError(f"{label} root must be an object")
    return source, raw, value


def _common(
    value: dict[str, Any],
    *,
    role: str,
    decision_at: datetime,
    tickers: tuple[str, ...],
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
    manifest_path: Path, value: dict[str, Any], *, label: str, decision_at: datetime
) -> tuple[dict[str, Path], list[dict[str, Any]]]:
    rows = value.get("source_files")
    if not isinstance(rows, list) or not rows:
        raise DecisionContractError(f"{label}.source_files are missing")
    by_hash: dict[str, Path] = {}
    evidence: list[dict[str, Any]] = []
    root = manifest_path.parent.resolve()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DecisionContractError(f"{label}.source_files[{index}] is invalid")
        relative = row.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise DecisionContractError(f"{label}.source_files[{index}].path must be relative")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise DecisionContractError(f"{label}.source_files[{index}].path escapes root") from exc
        if not path.is_file():
            raise DecisionContractError(f"{label}.source_files[{index}] is missing")
        expected = _hash(row.get("source_hash"), f"{label}.source_files[{index}].source_hash")
        actual = _sha256(path.read_bytes())
        if actual != expected:
            raise DecisionContractError(f"{label}.source_files[{index}] source_hash mismatch")
        available_at = _timestamp(row.get("available_at"), f"{label}.source_files[{index}].available_at")
        if available_at > decision_at:
            raise DecisionContractError(f"{label}.source_files[{index}] is future information")
        if actual in by_hash:
            raise DecisionContractError(f"{label}.source_files contain a duplicate source_hash")
        by_hash[actual] = path
        evidence.append(
            {
                "role": f"contract_source:{label}:{index}",
                "path": str(path),
                "source_hash": actual,
                "available_at": available_at.isoformat(),
            }
        )
    return by_hash, evidence


def _record_source_hash(row: dict[str, Any], hashes: dict[str, Path], label: str) -> str:
    source_hash = _hash(row.get("source_hash"), f"{label}.source_hash")
    if source_hash not in hashes:
        raise DecisionContractError(f"{label}.source_hash is not linked to source_files")
    return source_hash


def _available(row: dict[str, Any], *, label: str, decision_at: datetime) -> datetime:
    available_at = _timestamp(row.get("available_at"), f"{label}.available_at")
    if available_at > decision_at:
        raise DecisionContractError(f"{label}.available_at is later than decision_time")
    return available_at


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
    return _result(
        requirement_id,
        classification=None,
        schema_valid=False,
        reason=reason,
    )


def _validate_eps(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path, raw, value = _load(path, "PIT Forward EPS manifest")
    classification = _common(value, role="pit_forward_eps", decision_at=decision_at, tickers=tickers)
    hashes, evidence = _source_files(manifest_path, value, label="pit_forward_eps", decision_at=decision_at)
    records = value.get("records")
    if not isinstance(records, list) or len(records) != len(tickers):
        raise DecisionContractError("pit_forward_eps.records must contain one row per ticker")
    metadata: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise DecisionContractError(f"pit_forward_eps.records[{index}] is invalid")
        ticker = row.get("ticker")
        if ticker not in tickers or ticker in seen:
            raise DecisionContractError("pit_forward_eps ticker set is invalid")
        seen.add(str(ticker))
        basis = row.get("accounting_basis")
        horizon = row.get("forecast_horizon")
        currency = row.get("currency")
        unit = row.get("unit")
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
        _date(row.get("as_of"), f"{ticker}.as_of")
        _available(row, label=str(ticker), decision_at=decision_at)
        source_hash = _record_source_hash(row, hashes, str(ticker))
        low = _number(row.get("eps_low"), f"{ticker}.eps_low")
        base = _number(row.get("eps_base"), f"{ticker}.eps_base")
        high = _number(row.get("eps_high"), f"{ticker}.eps_high")
        if not low <= base <= high:
            raise DecisionContractError(f"{ticker} EPS scenarios are not ordered")
        metadata[str(ticker)] = {
            "accounting_basis": str(basis),
            "forecast_horizon": str(horizon),
            "currency": str(currency),
            "unit": str(unit),
            "source_hash": source_hash,
        }
    shared = {(m["accounting_basis"], m["forecast_horizon"], m["currency"], m["unit"]) for m in metadata.values()}
    if len(shared) != 1:
        raise DecisionContractError("PIT Forward EPS basis, horizon, currency, and unit must match")
    result = _result(
        "verified_pit_forward_eps",
        classification=classification,
        schema_valid=True,
        reason="PIT EPS rows and preserved source bytes passed the offline contract",
        manifest_path=manifest_path,
        manifest_hash=_sha256(raw),
        evidence=evidence,
        details={"metadata": metadata, "source_hashes": sorted(hashes)},
    )
    return result, value


def _validate_company_per(
    path: str | Path,
    decision_at: datetime,
    tickers: tuple[str, ...],
    eps_result: dict[str, Any],
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "company PIT Forward PER ledger")
    classification = _common(value, role="company_pit_forward_per", decision_at=decision_at, tickers=tickers)
    hashes, evidence = _source_files(manifest_path, value, label="company_pit_forward_per", decision_at=decision_at)
    records = value.get("records")
    if not isinstance(records, list):
        raise DecisionContractError("company_pit_forward_per.records are missing")
    eps_meta = (eps_result.get("details") or {}).get("metadata") or {}
    eps_hashes = set((eps_result.get("details") or {}).get("source_hashes") or [])
    for ticker in tickers:
        rows = [row for row in records if isinstance(row, dict) and row.get("ticker") == ticker]
        if len(rows) < 20:
            raise DecisionContractError(f"{ticker} company PIT Forward PER history has fewer than 20 rows")
        dates: list[date] = []
        for index, row in enumerate(rows):
            label = f"{ticker}.per[{index}]"
            observation = _date(row.get("as_of"), f"{label}.as_of")
            dates.append(observation)
            available_at = _available(row, label=label, decision_at=decision_at)
            if available_at.date() >= observation:
                raise DecisionContractError(
                    f"{label}.underlying EPS was not available before the price session"
                )
            _record_source_hash(row, hashes, label)
            underlying_hash = _hash(row.get("underlying_eps_hash"), f"{label}.underlying_eps_hash")
            if underlying_hash not in eps_hashes:
                raise DecisionContractError(f"{label}.underlying_eps_hash is not linked to PIT EPS evidence")
            meta = eps_meta.get(ticker) or {}
            for field in ("accounting_basis", "forecast_horizon", "currency"):
                if row.get(field) != meta.get(field):
                    raise DecisionContractError(f"{label}.{field} does not match PIT EPS basis")
            if row.get("unit") not in PER_UNITS:
                raise DecisionContractError(f"{label}.unit is not approved")
            if row.get("split_adjustment_policy") not in SPLIT_POLICIES:
                raise DecisionContractError(f"{label}.split_adjustment_policy is not approved")
            price = _number(row.get("price"), f"{label}.price")
            eps = _number(row.get("forward_eps"), f"{label}.forward_eps")
            multiple = _number(row.get("forward_per"), f"{label}.forward_per")
            if not math.isclose(price / eps, multiple, rel_tol=1e-6, abs_tol=1e-6):
                raise DecisionContractError(f"{label}.forward_per calculation mismatch")
        if (max(dates) - min(dates)).days < 730:
            raise DecisionContractError(f"{ticker} company PIT Forward PER history spans less than two years")
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise DecisionContractError(
                f"{ticker} company PIT Forward PER dates must be unique and chronological"
            )
    return _result(
        "company_pit_forward_per_ledger",
        classification=classification,
        schema_valid=True,
        reason="same-basis point-in-time PER rows passed minimum count and span checks",
        manifest_path=manifest_path,
        manifest_hash=_sha256(raw),
        evidence=evidence,
        details={"row_count": len(records), "source_hashes": sorted(hashes)},
    )


def _validate_relative(
    path: str | Path,
    decision_at: datetime,
    tickers: tuple[str, ...],
    eps_result: dict[str, Any],
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "relative Forward multiples ledger")
    classification = _common(value, role="relative_forward_multiples", decision_at=decision_at, tickers=tickers)
    hashes, evidence = _source_files(manifest_path, value, label="relative_forward_multiples", decision_at=decision_at)
    records = value.get("records")
    if not isinstance(records, list):
        raise DecisionContractError("relative_forward_multiples.records are missing")
    required_roles = {"qqq", "soxx", "peer_basket"}
    by_role = {row.get("role"): row for row in records if isinstance(row, dict)}
    if set(by_role) != required_roles:
        raise DecisionContractError("relative Forward multiples must contain QQQ, SOXX, and peer_basket")
    eps_meta = next(iter(((eps_result.get("details") or {}).get("metadata") or {}).values()), {})
    as_of_values: set[str] = set()
    for role in sorted(required_roles):
        row = by_role[role]
        label = f"relative_forward_multiples.{role}"
        as_of_values.add(str(_date(row.get("as_of"), f"{label}.as_of")))
        _available(row, label=label, decision_at=decision_at)
        _record_source_hash(row, hashes, label)
        for field in ("accounting_basis", "forecast_horizon", "currency"):
            if row.get(field) != eps_meta.get(field):
                raise DecisionContractError(f"{label}.{field} does not match PIT EPS basis")
        if row.get("unit") not in PER_UNITS:
            raise DecisionContractError(f"{label}.unit is not approved")
        _number(row.get("forward_multiple"), f"{label}.forward_multiple")
        if row.get("weighting_method") not in WEIGHTING_METHODS:
            raise DecisionContractError(f"{label}.weighting_method is not approved")
        constituents = row.get("constituents")
        if not isinstance(constituents, list) or not constituents:
            raise DecisionContractError(f"{label}.constituents are missing")
        weights = [_number(item.get("weight"), f"{label}.constituent.weight") for item in constituents if isinstance(item, dict)]
        if len(weights) != len(constituents) or not math.isclose(sum(weights), 1.0, abs_tol=1e-6):
            raise DecisionContractError(f"{label}.constituent weights must sum to 1")
        for index, constituent in enumerate(constituents):
            constituent_label = f"{label}.constituents[{index}]"
            if not isinstance(constituent.get("ticker"), str) or not constituent["ticker"]:
                raise DecisionContractError(f"{constituent_label}.ticker is missing")
            for field in ("accounting_basis", "forecast_horizon", "currency"):
                if constituent.get(field) != eps_meta.get(field):
                    raise DecisionContractError(
                        f"{constituent_label}.{field} does not match PIT EPS basis"
                    )
    if len(as_of_values) != 1:
        raise DecisionContractError("relative Forward multiples do not share one as_of date")
    return _result(
        "independent_qqq_soxx_peer_forward_multiples",
        classification=classification,
        schema_valid=True,
        reason="QQQ, SOXX, and peer multiples share one verified EPS basis and date",
        manifest_path=manifest_path,
        manifest_hash=_sha256(raw),
        evidence=evidence,
        details={"as_of": next(iter(as_of_values)), "source_hashes": sorted(hashes)},
    )


def _validate_filing(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...]
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "filing timing manifest")
    classification = _common(value, role="filing_timing", decision_at=decision_at, tickers=tickers)
    hashes, evidence = _source_files(manifest_path, value, label="filing_timing", decision_at=decision_at)
    records = value.get("records")
    if not isinstance(records, list) or len(records) != len(tickers):
        raise DecisionContractError("filing_timing.records must contain one row per ticker")
    seen: set[str] = set()
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise DecisionContractError(f"filing_timing.records[{index}] is invalid")
        ticker = row.get("ticker")
        if ticker not in tickers or ticker in seen:
            raise DecisionContractError("filing_timing ticker set is invalid")
        seen.add(str(ticker))
        _record_source_hash(row, hashes, str(ticker))
        filing_date = _date(row.get("filing_date"), f"{ticker}.filing_date")
        usable = _date(row.get("usable_from_session"), f"{ticker}.usable_from_session")
        basis = row.get("timing_basis")
        if basis not in TIMING_BASES:
            raise DecisionContractError(f"{ticker}.timing_basis is not approved")
        if basis == "sec_acceptance_timestamp":
            accepted = _timestamp(row.get("accepted_at"), f"{ticker}.accepted_at")
            if accepted > decision_at:
                raise DecisionContractError(f"{ticker}.accepted_at is future information")
        elif usable <= filing_date:
            raise DecisionContractError(f"{ticker} same-day entry is blocked without acceptance timestamp")
        if usable > decision_at.date():
            raise DecisionContractError(f"{ticker}.usable_from_session is after decision_time")
    return _result(
        "sec_acceptance_or_conservative_next_session",
        classification=classification,
        schema_valid=True,
        reason="filing availability uses SEC acceptance or a conservative next-session rule",
        manifest_path=manifest_path,
        manifest_hash=_sha256(raw),
        evidence=evidence,
        details={"source_hashes": sorted(hashes)},
    )


def _validate_prices(
    path: str | Path, decision_at: datetime, tickers: tuple[str, ...]
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "adjusted price ledger")
    classification = _common(value, role="adjusted_price_history", decision_at=decision_at, tickers=tickers)
    hashes, evidence = _source_files(manifest_path, value, label="adjusted_price_history", decision_at=decision_at)
    if value.get("adjustment_policy") not in PRICE_ADJUSTMENT_POLICIES:
        raise DecisionContractError("adjusted price ledger adjustment_policy is not approved")
    if value.get("calendar") != "XNYS":
        raise DecisionContractError("adjusted price ledger calendar must be XNYS")
    if not isinstance(value.get("corporate_action_policy"), str) or not value["corporate_action_policy"].strip():
        raise DecisionContractError("adjusted price ledger corporate_action_policy is missing")
    records = value.get("records")
    if not isinstance(records, dict):
        raise DecisionContractError("adjusted price ledger records are missing")
    symbols = (*tickers, "QQQ", "SOXX")
    latest: dict[str, str] = {}
    for symbol in symbols:
        rows = records.get(symbol)
        if not isinstance(rows, list) or len(rows) < 200:
            raise DecisionContractError(f"{symbol} adjusted price history has fewer than 200 rows")
        dates: list[date] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise DecisionContractError(f"{symbol}.prices[{index}] is invalid")
            label = f"{symbol}.prices[{index}]"
            dates.append(_date(row.get("date"), f"{label}.date"))
            _number(row.get("adjusted_close"), f"{label}.adjusted_close")
            _record_source_hash(row, hashes, label)
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise DecisionContractError(f"{symbol} adjusted price dates must be unique and chronological")
        if dates[-1] > decision_at.date():
            raise DecisionContractError(f"{symbol} adjusted price history contains future information")
        latest[symbol] = dates[-1].isoformat()
    if len(set(latest.values())) != 1:
        raise DecisionContractError("adjusted price histories do not share one latest session")
    return _result(
        "daily_adjusted_price_history",
        classification=classification,
        schema_valid=True,
        reason="daily adjusted histories, corporate-action policy, and XNYS calendar passed",
        manifest_path=manifest_path,
        manifest_hash=_sha256(raw),
        evidence=evidence,
        details={"latest_session": next(iter(latest.values())), "source_hashes": sorted(hashes)},
    )


def _validate_rights(
    path: str | Path,
    decision_at: datetime,
    tickers: tuple[str, ...],
    required_hashes: set[str],
) -> dict[str, Any]:
    manifest_path, raw, value = _load(path, "rights manifest")
    classification = _common(value, role="rights", decision_at=decision_at, tickers=tickers)
    hashes, evidence = _source_files(manifest_path, value, label="rights", decision_at=decision_at)
    records = value.get("records")
    if not isinstance(records, list):
        raise DecisionContractError("rights.records are missing")
    by_hash: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise DecisionContractError(f"rights.records[{index}] is invalid")
        source_hash = _hash(row.get("source_hash"), f"rights.records[{index}].source_hash")
        if source_hash in by_hash:
            raise DecisionContractError("rights records contain a duplicate source_hash")
        by_hash[source_hash] = row
        if row.get("usage_scope") not in USAGE_SCOPES:
            raise DecisionContractError(f"rights for {source_hash} have an invalid usage_scope")
        for field in ("storage_allowed", "derivative_analysis_allowed", "local_display_allowed"):
            if row.get(field) is not True:
                raise DecisionContractError(f"rights for {source_hash} do not permit {field}")
        reviewed = _timestamp(row.get("reviewed_at"), f"rights[{source_hash}].reviewed_at")
        expires = _timestamp(row.get("expires_at"), f"rights[{source_hash}].expires_at")
        if reviewed > decision_at:
            raise DecisionContractError(f"rights for {source_hash} were reviewed after decision_time")
        if expires <= decision_at:
            raise DecisionContractError(f"rights for {source_hash} are expired")
    missing = required_hashes - set(by_hash)
    if missing:
        raise DecisionContractError("rights manifest does not cover every decision source_hash")
    return _result(
        "decision_display_rights",
        classification=classification,
        schema_valid=True,
        reason="storage, derivative analysis, and local display rights cover every source hash",
        manifest_path=manifest_path,
        manifest_hash=_sha256(raw),
        evidence=evidence,
        details={"covered_source_hashes": sorted(required_hashes), "source_hashes": sorted(hashes)},
    )


def validate_ready_contracts(
    contract_paths: dict[str, str | Path] | None,
    *,
    decision_time: str,
    tickers: tuple[str, ...],
    actual_evidence_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate all six offline manifests without creating a snapshot or decision."""

    decision_at = _timestamp(decision_time, "decision_time")
    paths = contract_paths or {}
    results: dict[str, dict[str, Any]] = {}
    loaded_classifications: set[str] = set()

    def run(role: str, validator: Any, *args: Any) -> dict[str, Any]:
        requirement_id = REQUIREMENT_BY_ROLE[role]
        path = paths.get(role)
        if path is None:
            return _blocked_result(requirement_id, f"{role} manifest is missing")
        try:
            result = validator(path, decision_at, tickers, *args)
        except (OSError, DecisionContractError) as exc:
            return _blocked_result(requirement_id, str(exc))
        classification = result.get("evidence_classification")
        if isinstance(classification, str):
            loaded_classifications.add(classification)
        return result

    try:
        eps_result, _ = _validate_eps(paths["pit_forward_eps"], decision_at, tickers)
    except KeyError:
        eps_result = _blocked_result("verified_pit_forward_eps", "pit_forward_eps manifest is missing")
    except (OSError, DecisionContractError) as exc:
        eps_result = _blocked_result("verified_pit_forward_eps", str(exc))
    if isinstance(eps_result.get("evidence_classification"), str):
        loaded_classifications.add(str(eps_result["evidence_classification"]))
    results["verified_pit_forward_eps"] = eps_result

    results["company_pit_forward_per_ledger"] = run(
        "company_pit_forward_per", _validate_company_per, eps_result
    )
    results["independent_qqq_soxx_peer_forward_multiples"] = run(
        "relative_forward_multiples", _validate_relative, eps_result
    )
    results["sec_acceptance_or_conservative_next_session"] = run(
        "filing_timing", _validate_filing
    )
    results["daily_adjusted_price_history"] = run(
        "adjusted_price_history", _validate_prices
    )

    required_hashes: set[str] = set()
    for requirement_id, result in results.items():
        if requirement_id == "decision_display_rights":
            continue
        required_hashes.update((result.get("details") or {}).get("source_hashes") or [])
    rights_path = paths.get("rights")
    if rights_path is None:
        rights_result = _blocked_result("decision_display_rights", "rights manifest is missing")
    else:
        try:
            rights_result = _validate_rights(rights_path, decision_at, tickers, required_hashes)
        except (OSError, DecisionContractError) as exc:
            rights_result = _blocked_result("decision_display_rights", str(exc))
    results["decision_display_rights"] = rights_result
    if isinstance(rights_result.get("evidence_classification"), str):
        loaded_classifications.add(str(rights_result["evidence_classification"]))

    relative_as_of = (results["independent_qqq_soxx_peer_forward_multiples"].get("details") or {}).get("as_of")
    price_as_of = (results["daily_adjusted_price_history"].get("details") or {}).get("latest_session")
    alignment_schema_valid = bool(relative_as_of and price_as_of and relative_as_of == price_as_of)
    classification = next(iter(loaded_classifications)) if len(loaded_classifications) == 1 else None
    results["same_date_price_and_regime"] = _result(
        "same_date_price_and_regime",
        classification=classification,
        schema_valid=alignment_schema_valid,
        reason=(
            f"relative multiples and adjusted prices share {price_as_of}"
            if alignment_schema_valid
            else f"relative multiples={relative_as_of}, adjusted prices={price_as_of}"
        ),
        details={"as_of": price_as_of},
    )
    original_schema_valid = bool(eps_result.get("schema_valid") and (eps_result.get("evidence") or []))
    results["original_consensus_bytes"] = _result(
        "original_consensus_bytes",
        classification=eps_result.get("evidence_classification"),
        schema_valid=original_schema_valid,
        reason=(
            "PIT Forward EPS records are linked to preserved source bytes"
            if original_schema_valid
            else "PIT Forward EPS manifest or preserved source-byte linkage is missing"
        ),
        evidence=list(eps_result.get("evidence") or []),
        details={"source_hashes": (eps_result.get("details") or {}).get("source_hashes") or []},
    )

    if len(loaded_classifications) > 1:
        for result in results.values():
            result["passed"] = False
            result["status"] = "blocked"
            result["reason"] = "evidence classifications are mixed across contracts"
    evidence: list[dict[str, Any]] = []
    for result in results.values():
        manifest_path = result.get("manifest_path")
        manifest_hash = result.get("manifest_hash")
        if manifest_path and manifest_hash:
            evidence.append(
                {
                    "role": f"contract_manifest:{result['id']}",
                    "path": manifest_path,
                    "source_hash": manifest_hash,
                }
            )
        evidence.extend(result.get("evidence") or [])
    unique_evidence = {
        (row["role"], row["path"], row["source_hash"]): row for row in evidence
    }
    if classification == "actual_local_evidence":
        approved_root = Path(actual_evidence_root).resolve() if actual_evidence_root else None
        root_error: str | None = None
        if approved_root is None:
            root_error = "actual evidence root is not approved"
        else:
            for row in unique_evidence.values():
                try:
                    Path(row["path"]).resolve().relative_to(approved_root)
                except (KeyError, TypeError, ValueError):
                    root_error = "actual evidence path is outside the approved root"
                    break
        if root_error:
            for result in results.values():
                result["passed"] = False
                result["status"] = "blocked"
                result["reason"] = root_error
    return {
        "contract_version": CONTRACT_VERSION,
        "evidence_classification": classification,
        "decision_time": decision_time,
        "target_tickers": list(tickers),
        "requirements": [results[key] for key in (
            "verified_pit_forward_eps",
            "company_pit_forward_per_ledger",
            "independent_qqq_soxx_peer_forward_multiples",
            "original_consensus_bytes",
            "same_date_price_and_regime",
            "sec_acceptance_or_conservative_next_session",
            "daily_adjusted_price_history",
            "decision_display_rights",
        )],
        "evidence": list(unique_evidence.values()),
        "schema_valid": all(bool(row.get("schema_valid")) for row in results.values()),
        "promotion_ready": all(bool(row.get("passed")) for row in results.values()),
        "decision_created": False,
    }
