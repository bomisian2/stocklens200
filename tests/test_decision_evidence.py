from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.decision_evidence import (
    AUDIT_VERSION,
    REQUIRED_REQUIREMENT_IDS,
    REQUIRED_SOURCE_ROLES,
    DecisionEvidenceError,
    audit_same_date_evidence,
    load_and_validate_evidence_audit,
    write_audit_report,
)


FIXTURES = Path(__file__).parent / "fixtures" / "same_date_sources"


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _ready_audit(tmp_path: Path) -> tuple[Path, dict[str, Path], dict]:
    source_paths: dict[str, Path] = {}
    for role in REQUIRED_SOURCE_ROLES:
        path = tmp_path / f"{role}.json"
        payload = {
            "role": role,
            "decision_time": "2026-10-14T23:00:00Z",
            "calculation_version": "same-date-investment-decision-v1.1",
        }
        _write_json(path, payload)
        source_paths[role] = path
    report = {
        "audit_version": AUDIT_VERSION,
        "decision_time": "2026-10-14T23:00:00Z",
        "target_tickers": ["AVGO", "NVDA"],
        "legacy_snapshot_hash": _hash(source_paths["legacy_snapshot"]),
        "decision_snapshot_hash": _hash(source_paths["decision_snapshot"]),
        "status": "ready",
        "decision_creation_allowed": True,
        "decision_created": False,
        "block_stage": None,
        "operational_eligible": False,
        "requirements": [
            {"id": requirement_id, "passed": True, "status": "passed", "reason": "fixture"}
            for requirement_id in REQUIRED_REQUIREMENT_IDS
        ],
        "evidence": [
            {
                "role": role,
                "path": str(path.resolve()),
                "source_hash": _hash(path),
            }
            for role, path in source_paths.items()
        ],
    }
    audit_path = tmp_path / "audit.json"
    _write_json(audit_path, report)
    return audit_path, source_paths, report


def _validate(audit_path: Path, source_paths: dict[str, Path]) -> dict:
    return load_and_validate_evidence_audit(
        audit_path,
        expected_audit_sha256=_hash(audit_path) if audit_path.is_file() else None,
        decision_time="2026-10-14T23:00:00Z",
        target_tickers=("AVGO", "NVDA"),
        source_paths=source_paths,
    )


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


def test_ready_audit_passes_only_when_every_hash_and_field_matches(tmp_path: Path) -> None:
    audit_path, source_paths, report = _ready_audit(tmp_path)
    assert _validate(audit_path, source_paths) == report


def test_missing_audit_fails_closed(tmp_path: Path) -> None:
    _, source_paths, _ = _ready_audit(tmp_path)
    missing = tmp_path / "missing.json"
    with pytest.raises(DecisionEvidenceError, match="evidence audit is missing"):
        load_and_validate_evidence_audit(
            missing,
            expected_audit_sha256="0" * 64,
            decision_time="2026-10-14T23:00:00Z",
            target_tickers=("AVGO", "NVDA"),
            source_paths=source_paths,
        )


def test_invalid_json_and_wrong_audit_version_fail_closed(tmp_path: Path) -> None:
    audit_path, source_paths, report = _ready_audit(tmp_path)
    audit_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(DecisionEvidenceError, match="not valid UTF-8 JSON"):
        _validate(audit_path, source_paths)

    report["audit_version"] = "unapproved-audit-v0"
    _write_json(audit_path, report)
    with pytest.raises(DecisionEvidenceError, match="version is not approved"):
        _validate(audit_path, source_paths)


def test_boolean_only_forgery_and_false_requirement_fail_closed(tmp_path: Path) -> None:
    audit_path, source_paths, report = _ready_audit(tmp_path)
    _write_json(audit_path, {"decision_creation_allowed": True})
    with pytest.raises(DecisionEvidenceError, match="version is not approved"):
        _validate(audit_path, source_paths)

    report["requirements"][0]["passed"] = False
    _write_json(audit_path, report)
    with pytest.raises(DecisionEvidenceError, match="requirements not passed"):
        _validate(audit_path, source_paths)


def test_source_changed_after_audit_fails_closed(tmp_path: Path) -> None:
    audit_path, source_paths, _ = _ready_audit(tmp_path)
    source_paths["consensus"].write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(DecisionEvidenceError, match="current source hash mismatch: consensus"):
        _validate(audit_path, source_paths)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("decision_time", "2026-10-14T22:59:59Z", "decision_time does not match snapshot"),
        ("target_tickers", ["AVGO"], "target tickers do not match"),
    ],
)
def test_decision_time_and_ticker_mismatch_fail_closed(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    audit_path, source_paths, report = _ready_audit(tmp_path)
    report[field] = value
    _write_json(audit_path, report)
    with pytest.raises(DecisionEvidenceError, match=message):
        _validate(audit_path, source_paths)


def test_audit_file_hash_is_pinned(tmp_path: Path) -> None:
    audit_path, source_paths, _ = _ready_audit(tmp_path)
    with pytest.raises(DecisionEvidenceError, match="audit SHA-256 mismatch"):
        load_and_validate_evidence_audit(
            audit_path,
            expected_audit_sha256="0" * 64,
            decision_time="2026-10-14T23:00:00Z",
            target_tickers=("AVGO", "NVDA"),
            source_paths=source_paths,
        )
