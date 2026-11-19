from __future__ import annotations

import pytest

from scripts.validate_snapshot import validate_current_freshness, validate_snapshot_integrity


def _report() -> dict:
    return {
        "company_count": 200,
        "data_state": "healthy",
        "freshness_state": "verified",
        "market_session_date": "2026-10-09",
        "expected_session_date": "2026-10-12",
        "quality": {"sector_coverage": 0.96, "country_coverage": 0.965},
        "cross_validation": {"passed": True},
    }


def test_historical_snapshot_integrity_is_independent_of_current_session() -> None:
    validate_snapshot_integrity(_report())


def test_live_freshness_rejects_an_old_market_session() -> None:
    with pytest.raises(SystemExit, match="latest completed market session"):
        validate_current_freshness(_report())


def test_integrity_rejects_an_unverified_stored_snapshot() -> None:
    report = _report()
    report["freshness_state"] = "unverified"
    with pytest.raises(SystemExit, match="was not verified"):
        validate_snapshot_integrity(report)
