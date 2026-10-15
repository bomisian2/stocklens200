from __future__ import annotations

import hashlib
import json
from pathlib import Path

from stocklens.personal.decision_evidence import audit_same_date_evidence, write_audit_report


FIXTURES = Path(__file__).parent / "fixtures" / "same_date_sources"


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _legacy_snapshot(tmp_path: Path) -> Path:
    consensus_hash = hashlib.sha256((FIXTURES / "consensus.json").read_bytes()).hexdigest().upper()
    regime_hash = hashlib.sha256((FIXTURES / "regime.json").read_bytes()).hexdigest().upper()
    ledger_hash = hashlib.sha256((FIXTURES / "decision-ledger.json").read_bytes()).hexdigest().upper()
    path = tmp_path / "same-date-inputs-v1.json"
    _write_json(path, {
        "schema_version": "same-date-input-v1",
        "calculation_version": "same-date-investment-decision-v1",
        "decision_time": "2026-10-14T12:52:02.418Z",
        "prices": {ticker: {"price_as_of": "2026-10-13", "source_hash": ledger_hash} for ticker in ("AVGO", "NVDA")},
        "consensus": {"basis": "unverified_street_basis", "source_hash": consensus_hash},
        "regime": {"source_hash": regime_hash},
    })
    return path


def _price_history(tmp_path: Path) -> Path:
    path = tmp_path / "price-history.json"
    _write_json(path, {
        "generatedAt": "2026-10-14T12:40:00Z",
        "methodology": "fixture monthly close history",
        "records": {ticker: [{"date": f"2026-{month:02d}-28", "close": 100.0 + month} for month in range(1, 11)] for ticker in ("AVGO", "NVDA")},
    })
    return path


def test_evidence_audit_does_not_upgrade_legacy_snapshot_or_create_decision(tmp_path: Path) -> None:
    report = audit_same_date_evidence(
        legacy_snapshot_path=_legacy_snapshot(tmp_path),
        decision_ledger_path=FIXTURES / "decision-ledger.json",
        consensus_path=FIXTURES / "consensus.json",
        regime_path=FIXTURES / "regime.json",
        price_history_path=_price_history(tmp_path),
        decision_time="2026-10-14T13:00:00Z",
    )
    assert report["legacy_calculation_version"] == "same-date-investment-decision-v1"
    assert report["requested_calculation_version"] == "same-date-investment-decision-v1.1"
    assert report["historical_reconstruction_allowed"] is False
    assert report["decision_creation_allowed"] is False
    assert report["decision_created"] is False
    assert report["block_stage"] == "evidence_preflight"
    requirement = {row["id"]: row for row in report["requirements"]}
    assert requirement["verified_pit_forward_eps"]["passed"] is False
    assert requirement["daily_adjusted_price_history"]["passed"] is False
    assert requirement["independent_qqq_soxx_peer_forward_multiples"]["passed"] is False


def test_evidence_report_is_deterministic_and_written_only_to_requested_path(tmp_path: Path) -> None:
    kwargs = {
        "legacy_snapshot_path": _legacy_snapshot(tmp_path),
        "decision_ledger_path": FIXTURES / "decision-ledger.json",
        "consensus_path": FIXTURES / "consensus.json",
        "regime_path": FIXTURES / "regime.json",
        "price_history_path": _price_history(tmp_path),
        "decision_time": "2026-10-14T13:00:00Z",
    }
    first = audit_same_date_evidence(**kwargs)
    second = audit_same_date_evidence(**kwargs)
    assert first == second
    output = write_audit_report(first, tmp_path / "private" / "evidence-audit-v1.json")
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["decision_created"] is False
