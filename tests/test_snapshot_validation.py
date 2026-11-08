from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from stocklens.snapshot_validation import operational_freshness, stored_snapshot_errors

from test_storage import valid_frame


def valid_status(session_date: str = "2026-01-02") -> dict:
    return {
        "data_state": "healthy",
        "freshness_state": "verified",
        "market_session_date": session_date,
        "company_count": 200,
        "cross_validation": {"passed": True},
    }


def test_historical_snapshot_integrity_does_not_depend_on_wall_clock() -> None:
    frame = valid_frame()
    assert stored_snapshot_errors(frame, valid_status()) == []

    _, current_errors = operational_freshness(
        frame,
        valid_status(),
        now=datetime(2026, 1, 6, 3, tzinfo=timezone.utc),
    )
    assert any("latest completed" in item for item in current_errors)


def test_current_snapshot_passes_operational_freshness() -> None:
    frame = valid_frame()
    assessment, errors = operational_freshness(
        frame,
        valid_status(),
        now=datetime(2026, 1, 3, 3, tzinfo=timezone.utc),
    )
    assert assessment.state == "verified"
    assert errors == []


def test_stored_snapshot_rejects_session_mismatch() -> None:
    errors = stored_snapshot_errors(valid_frame(), valid_status("2025-12-31"))
    assert any("does not match" in item for item in errors)


def test_market_data_rights_manifest_fails_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    rights = json.loads((root / "docs" / "market-data-rights.json").read_text(encoding="utf-8"))
    assert rights["public_deployment_allowed"] is False
    assert {item["id"] for item in rights["providers"]} == {"yahoo_screener", "nasdaq_screener"}
    assert all(item["public_display"] != "allowed" for item in rights["providers"])
