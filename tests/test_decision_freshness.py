from datetime import datetime, timezone

from stocklens.personal.decision_freshness import assess_decision_freshness


def test_decision_freshness_marks_latest_completed_session_current() -> None:
    result = assess_decision_freshness(
        market_session="2026-10-27",
        evaluated_at=datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
    )

    assert result == {
        "status": "current",
        "market_session": "2026-10-27",
        "expected_session": "2026-10-27",
        "lag_sessions": 0,
    }


def test_decision_freshness_reports_trading_session_lag() -> None:
    result = assess_decision_freshness(
        market_session="2026-10-23",
        evaluated_at=datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
    )

    assert result["status"] == "stale"
    assert result["expected_session"] == "2026-10-27"
    assert result["lag_sessions"] == 2


def test_decision_freshness_blocks_future_session() -> None:
    result = assess_decision_freshness(
        market_session="2026-10-28",
        evaluated_at=datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
    )

    assert result["status"] == "future_blocked"
    assert result["lag_sessions"] is None
