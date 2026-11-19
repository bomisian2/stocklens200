"""Deterministically build a same-date snapshot from preserved local raw JSON."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from stocklens.market_calendar import latest_completed_session
from stocklens.personal.decision_model import CALCULATION_VERSION, DecisionModelError


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _load(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    if not path.is_file():
        raise DecisionModelError(f"raw {label} file is missing")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionModelError(f"raw {label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DecisionModelError(f"raw {label} root must be an object")
    return raw, value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DecisionModelError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionModelError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DecisionModelError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _record_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise DecisionModelError(f"{label} must be a date or timestamp")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise DecisionModelError(f"{label} must be a date or timestamp") from exc


def build_same_date_snapshot(
    *,
    decision_ledger_path: str | Path,
    consensus_path: str | Path,
    regime_path: str | Path,
    output_dir: str | Path,
    decision_time: str,
    tickers: tuple[str, ...] = ("AVGO", "NVDA"),
) -> Path:
    """Copy exact raw bytes and emit a normalized, hash-linked snapshot."""

    decision_at = _timestamp(decision_time, "decision_time")
    expected_session = latest_completed_session(decision_at).session_date
    sources = {
        "decision_ledger": Path(decision_ledger_path),
        "consensus": Path(consensus_path),
        "regime": Path(regime_path),
    }
    loaded = {key: _load(path, key) for key, path in sources.items()}
    raw_ledger = loaded["decision_ledger"][1]
    raw_consensus = loaded["consensus"][1]
    raw_regime = loaded["regime"][1]
    available_at = {
        "decision_ledger": _timestamp(raw_ledger.get("generatedAt"), "decision_ledger.generatedAt"),
        "consensus": _timestamp(raw_consensus.get("generatedAt"), "consensus.generatedAt"),
        "regime": _timestamp(raw_regime.get("generatedAt"), "regime.generatedAt"),
    }
    future = [key for key, timestamp in available_at.items() if timestamp > decision_at]
    if future:
        raise DecisionModelError("future raw source is blocked: " + ", ".join(sorted(future)))

    records = raw_ledger.get("records")
    consensus_records = raw_consensus.get("records")
    indicators = raw_regime.get("indicators")
    if not isinstance(records, list) or not isinstance(consensus_records, dict) or not isinstance(indicators, dict):
        raise DecisionModelError("raw source schema is incomplete")

    prices: dict[str, Any] = {}
    normalized_consensus: dict[str, Any] = {}
    ledger_hash = _sha256(loaded["decision_ledger"][0])
    consensus_hash = _sha256(loaded["consensus"][0])
    regime_hash = _sha256(loaded["regime"][0])
    for ticker in tickers:
        matches = [row for row in records if row.get("ticker") == ticker and row.get("date") == expected_session]
        if len(matches) != 1:
            raise DecisionModelError(f"{ticker} has no unique decision-ledger row for {expected_session}")
        row = matches[0]
        row_inputs = row.get("inputs") or {}
        price_date = _record_date(row_inputs.get("priceAsOf"), f"{ticker}.priceAsOf")
        if price_date.isoformat() != expected_session or row.get("date") != expected_session:
            raise DecisionModelError(f"{ticker} price_as_of must equal market_session")
        prices[ticker] = {
            "value": row.get("price"),
            "market_session": expected_session,
            "price_as_of": expected_session,
            "available_at": raw_ledger["generatedAt"],
            "source": "preserved decision-ledger raw JSON",
            "source_hash": ledger_hash,
            "calculation_basis": "exact ledger close; no interpolation",
            "sector_state": row_inputs.get("sectorState"),
            "sector_etf": row_inputs.get("sectorEtf"),
            "rsi": row_inputs.get("rsi"),
            "above_sma50": row_inputs.get("sma50") == "위",
            "above_sma200": row_inputs.get("sma200") == "위",
        }
        source_consensus = consensus_records.get(ticker)
        if not isinstance(source_consensus, dict):
            raise DecisionModelError(f"{ticker} consensus record is missing")
        if _timestamp(source_consensus.get("collectedAt"), f"{ticker}.collectedAt") > decision_at:
            raise DecisionModelError(f"future consensus is blocked: {ticker}")
        normalized_consensus[ticker] = {
            "ticker": ticker,
            "fiscal_end": source_consensus.get("fiscalEnd"),
            "eps_low": source_consensus.get("lowEps"),
            "eps_base": source_consensus.get("consensusEps"),
            "eps_high": source_consensus.get("highEps"),
            "analyst_count": source_consensus.get("analystCount"),
            "revisions_up_4w": source_consensus.get("revisionsUp4w"),
            "revisions_down_4w": source_consensus.get("revisionsDown4w"),
            "revision_state": source_consensus.get("revisionState"),
            "next_fiscal_end": source_consensus.get("nextFiscalEnd"),
            "next_consensus_eps": source_consensus.get("nextConsensusEps"),
        }

    regime_as_of_values = {_record_date(row.get("asOf"), f"regime.{key}.asOf") for key, row in indicators.items()}
    if len(regime_as_of_values) != 1:
        raise DecisionModelError("regime indicators do not share one as_of date")
    regime_as_of = next(iter(regime_as_of_values))
    if regime_as_of > decision_at.date():
        raise DecisionModelError("future regime is blocked")

    output_root = Path(output_dir)
    evidence_dir = output_root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    for key, source_path in sources.items():
        destination = evidence_dir / f"{key}.json"
        shutil.copyfile(source_path, destination)
        manifest[key] = {
            "path": f"evidence/{key}.json",
            "sha256": _sha256(destination.read_bytes()),
            "available_at": available_at[key].isoformat().replace("+00:00", "Z"),
        }
    snapshot = {
        "schema_version": "same-date-input-v1",
        "decision_time": decision_time,
        "calculation_version": CALCULATION_VERSION,
        "status": "normalized_from_preserved_raw",
        "operational_eligible": False,
        "source_evidence": manifest,
        "prices": prices,
        "consensus": {
            "available_at": raw_consensus["generatedAt"],
            "source": raw_consensus.get("source"),
            "source_hash": consensus_hash,
            "source_snapshot_available": True,
            "calculation_basis": raw_consensus.get("methodology"),
            "basis": "unverified_street_basis",
            "records": normalized_consensus,
        },
        "regime": {
            "as_of": regime_as_of.isoformat(),
            "available_at": raw_regime["generatedAt"],
            "source": "preserved cross-project market proxy raw JSON",
            "source_hash": regime_hash,
            "state": raw_regime.get("state"),
            "score": raw_regime.get("score"),
            "status": "cross_project_market_proxy",
            "calculation_basis": raw_regime.get("methodology"),
            "indicators": indicators,
        },
        "limitations": [
            "Street EPS GAAP/non-GAAP basis is unverified",
            "market regime is a cross-project proxy, not official macro data",
            "no independent QQQ/SOXX/peer multiple ledger is present",
        ],
    }
    output_path = output_root / "same-date-inputs-v1.json"
    temporary = output_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return output_path
