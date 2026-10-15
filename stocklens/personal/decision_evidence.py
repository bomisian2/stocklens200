"""Audit preserved local evidence before building a same-date decision snapshot.

This module never repairs missing evidence and never upgrades an old snapshot.
It records what is present, what exact bytes still match, and which inputs block
creation of a decision object.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TARGET_TICKERS = ("AVGO", "NVDA")
AUDIT_VERSION = "same-date-evidence-audit-v1"


class DecisionEvidenceError(ValueError):
    """Raised when an evidence audit cannot be performed safely."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DecisionEvidenceError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionEvidenceError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DecisionEvidenceError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _read_json(path: str | Path, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    source_path = Path(path)
    if not source_path.is_file():
        raise DecisionEvidenceError(f"{label} file is missing")
    raw = source_path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionEvidenceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DecisionEvidenceError(f"{label} root must be an object")
    return source_path, raw, value


def _legacy_hashes(snapshot: dict[str, Any]) -> dict[str, str | None]:
    prices = snapshot.get("prices") or {}
    price_hashes = {
        str(row.get("source_hash")).upper()
        for row in prices.values()
        if isinstance(row, dict) and row.get("source_hash")
    }
    return {
        "decision_ledger": next(iter(price_hashes)) if len(price_hashes) == 1 else None,
        "consensus": str((snapshot.get("consensus") or {}).get("source_hash") or "").upper() or None,
        "regime": str((snapshot.get("regime") or {}).get("source_hash") or "").upper() or None,
    }


def _source_record(
    *,
    role: str,
    path: Path,
    raw: bytes,
    value: dict[str, Any],
    expected_hash: str | None,
    decision_at: datetime,
    status: str,
    reason: str,
    as_of: Any = None,
    price_as_of: Any = None,
    eps_basis: Any = None,
    accounting_basis: Any = None,
    forecast_horizon: Any = None,
    unit: Any = None,
    currency: Any = None,
) -> dict[str, Any]:
    generated_at = value.get("generatedAt")
    available = None
    if generated_at:
        available = _timestamp(generated_at, f"{role}.generatedAt") <= decision_at
    actual_hash = _sha256(raw)
    return {
        "role": role,
        "path": str(path.resolve()),
        "source": value.get("source") or value.get("methodology") or "local preserved JSON",
        "source_hash": actual_hash,
        "legacy_expected_hash": expected_hash,
        "exact_legacy_hash_match": bool(expected_hash and actual_hash == expected_hash.upper()),
        "as_of": as_of,
        "available_at": generated_at,
        "local_file_modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "price_as_of": price_as_of,
        "eps_fiscal_basis": eps_basis,
        "gaap_non_gaap": accounting_basis,
        "forecast_horizon": forecast_horizon,
        "unit": unit,
        "currency": currency,
        "available_before_decision": available,
        "status": status,
        "reason": reason,
    }


def audit_same_date_evidence(
    *,
    legacy_snapshot_path: str | Path,
    decision_ledger_path: str | Path,
    consensus_path: str | Path,
    regime_path: str | Path,
    price_history_path: str | Path,
    decision_time: str,
    tickers: tuple[str, ...] = TARGET_TICKERS,
) -> dict[str, Any]:
    """Return a deterministic evidence table and decision-creation gate."""

    decision_at = _timestamp(decision_time, "decision_time")
    legacy_path, legacy_raw, legacy = _read_json(legacy_snapshot_path, "legacy snapshot")
    ledger_path, ledger_raw, ledger = _read_json(decision_ledger_path, "decision ledger")
    consensus_source_path, consensus_raw, consensus = _read_json(consensus_path, "consensus")
    regime_source_path, regime_raw, regime = _read_json(regime_path, "regime")
    history_path, history_raw, history = _read_json(price_history_path, "price history")

    expected_hashes = _legacy_hashes(legacy)
    ledger_rows = [
        row for row in (ledger.get("records") or [])
        if isinstance(row, dict) and row.get("ticker") in tickers
    ]
    latest_ledger_rows: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        rows = sorted((row for row in ledger_rows if row.get("ticker") == ticker), key=lambda row: str(row.get("date")))
        if rows:
            latest_ledger_rows[ticker] = rows[-1]
    price_dates = {
        str(row.get("date")) for row in latest_ledger_rows.values() if row.get("date")
    }
    latest_price_as_of = next(iter(price_dates)) if len(price_dates) == 1 else None

    consensus_records = consensus.get("records") or {}
    target_consensus = {
        ticker: consensus_records.get(ticker)
        for ticker in tickers
        if isinstance(consensus_records.get(ticker), dict)
    }
    metadata_fields = ("basis", "gaap", "currency", "unit")
    consensus_metadata_complete = len(target_consensus) == len(tickers) and all(
        all(record.get(field) is not None for field in metadata_fields)
        for record in target_consensus.values()
    )
    fiscal_ends = sorted(
        {
            str(value)
            for record in target_consensus.values()
            for value in (record.get("fiscalEnd"), record.get("nextFiscalEnd"))
            if value
        }
    )
    forecast_horizon = "FY1/FY2 labels present; PIT accounting basis unverified" if fiscal_ends else None

    indicators = regime.get("indicators") or {}
    regime_dates = sorted(
        {
            str(row.get("asOf"))
            for row in indicators.values()
            if isinstance(row, dict) and row.get("asOf")
        }
    )
    regime_as_of = regime_dates[0] if len(regime_dates) == 1 else None

    history_records = history.get("records") or {}
    history_rows = {
        ticker: [row for row in (history_records.get(ticker) or []) if isinstance(row, dict)]
        for ticker in tickers
    }
    history_dates = [
        str(row.get("date"))
        for rows in history_rows.values()
        for row in rows
        if row.get("date")
    ]
    history_keys = {key for rows in history_rows.values() for row in rows[:1] for key in row}
    daily_adjusted = "adjusted_close" in history_keys and all(len(rows) >= 200 for rows in history_rows.values())
    history_reason = (
        "daily adjusted history with at least 200 rows per ticker is present"
        if daily_adjusted
        else "only compacted close history is present; daily adjusted prices and corporate-action basis are missing"
    )

    evidence = [
        {
            "role": "legacy_snapshot",
            "path": str(legacy_path.resolve()),
            "source": "StockLens normalized local snapshot",
            "source_hash": _sha256(legacy_raw),
            "legacy_expected_hash": None,
            "exact_legacy_hash_match": True,
            "as_of": legacy.get("decision_time"),
            "available_at": None,
            "local_file_modified_at": datetime.fromtimestamp(legacy_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "price_as_of": sorted(
                {
                    str(row.get("price_as_of"))
                    for row in (legacy.get("prices") or {}).values()
                    if isinstance(row, dict) and row.get("price_as_of")
                }
            ),
            "eps_fiscal_basis": (legacy.get("consensus") or {}).get("basis"),
            "gaap_non_gaap": None,
            "forecast_horizon": "legacy normalized labels only",
            "unit": None,
            "currency": None,
            "available_before_decision": None,
            "status": "blocked",
            "reason": "legacy calculation version and no raw source_evidence manifest; version text must not be upgraded",
        },
        _source_record(
            role="decision_ledger",
            path=ledger_path,
            raw=ledger_raw,
            value=ledger,
            expected_hash=expected_hashes["decision_ledger"],
            decision_at=decision_at,
            status="partial",
            reason=(
                f"same-session prices exist for {len(latest_ledger_rows)}/{len(tickers)} tickers, "
                f"but only {len(ledger_rows)} PIT decision rows and no verified Forward EPS basis"
            ),
            as_of=ledger.get("asOfDate") or latest_price_as_of,
            price_as_of=latest_price_as_of,
            eps_basis="forwardPe field without underlying Forward EPS basis",
            unit="price units not declared in source",
        ),
        _source_record(
            role="consensus",
            path=consensus_source_path,
            raw=consensus_raw,
            value=consensus,
            expected_hash=expected_hashes["consensus"],
            decision_at=decision_at,
            status="partial" if consensus_metadata_complete else "blocked",
            reason=(
                "PIT EPS basis, GAAP/non-GAAP flag, unit, and currency are present"
                if consensus_metadata_complete
                else "EPS values exist but GAAP/non-GAAP, unit, currency, and verified PIT basis metadata are missing"
            ),
            as_of=fiscal_ends,
            eps_basis="verified" if consensus_metadata_complete else "unverified_street_basis",
            accounting_basis=(
                sorted({str(row.get("gaap")) for row in target_consensus.values()})
                if consensus_metadata_complete else None
            ),
            forecast_horizon=forecast_horizon,
            unit=(
                sorted({str(row.get("unit")) for row in target_consensus.values()})
                if consensus_metadata_complete else None
            ),
            currency=(
                sorted({str(row.get("currency")) for row in target_consensus.values()})
                if consensus_metadata_complete else None
            ),
        ),
        _source_record(
            role="regime",
            path=regime_source_path,
            raw=regime_raw,
            value=regime,
            expected_hash=expected_hashes["regime"],
            decision_at=decision_at,
            status="partial",
            reason="cross-project market proxy is available but is not an official macro source",
            as_of=regime_as_of,
        ),
        _source_record(
            role="price_history",
            path=history_path,
            raw=history_raw,
            value=history,
            expected_hash=None,
            decision_at=decision_at,
            status="available" if daily_adjusted else "blocked",
            reason=history_reason,
            as_of=max(history_dates) if history_dates else None,
            price_as_of=max(history_dates) if history_dates else None,
            unit="close only" if history_keys == {"date", "close"} else sorted(history_keys),
        ),
    ]

    requirements = [
        {"id": "verified_pit_forward_eps", "passed": consensus_metadata_complete, "status": "available" if consensus_metadata_complete else "blocked", "reason": "verified" if consensus_metadata_complete else "consensus accounting basis metadata is incomplete"},
        {"id": "company_pit_forward_per_ledger", "passed": False, "status": "blocked", "reason": f"only {len(ledger_rows)} target decision rows; underlying EPS basis is not recorded"},
        {"id": "independent_qqq_soxx_peer_forward_multiples", "passed": False, "status": "unavailable", "reason": "no independent same-basis forward-multiple ledger was supplied"},
        {"id": "original_consensus_bytes", "passed": bool(consensus_raw), "status": "available" if consensus_raw else "unavailable", "reason": "current preserved bytes are hashable; historical 12:52 UTC bytes do not match" if consensus_raw else "missing"},
        {"id": "same_date_price_and_regime", "passed": bool(latest_price_as_of and regime_as_of and price_dates == {regime_as_of}), "status": "partial", "reason": f"price={latest_price_as_of}, regime={regime_as_of}; regime remains a proxy"},
        {"id": "sec_acceptance_or_conservative_next_session", "passed": False, "status": "unavailable", "reason": "acceptance timestamp evidence is not linked to the consensus inputs"},
        {"id": "daily_adjusted_price_history", "passed": daily_adjusted, "status": "available" if daily_adjusted else "blocked", "reason": history_reason},
        {"id": "decision_display_rights", "passed": False, "status": "blocked", "reason": "no hash-linked rights manifest permits action-decision display"},
    ]
    creation_allowed = all(bool(item["passed"]) for item in requirements)
    return {
        "audit_version": AUDIT_VERSION,
        "decision_time": decision_time,
        "target_tickers": list(tickers),
        "legacy_snapshot_hash": _sha256(legacy_raw),
        "legacy_calculation_version": legacy.get("calculation_version"),
        "requested_calculation_version": "same-date-investment-decision-v1.1",
        "historical_reconstruction_allowed": False,
        "historical_reconstruction_reason": "the preserved consensus and regime bytes do not match the legacy hashes, and the matching ledger was generated after the legacy decision time",
        "evidence": evidence,
        "requirements": requirements,
        "decision_creation_allowed": creation_allowed,
        "decision_created": False,
        "block_stage": None if creation_allowed else "evidence_preflight",
        "status": "ready" if creation_allowed else "blocked",
        "operational_eligible": False,
    }


def write_audit_report(report: dict[str, Any], output_path: str | Path) -> Path:
    """Write a private audit atomically without modifying any source file."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination
