from __future__ import annotations

from datetime import datetime
from typing import Any

from stocklens.market_calendar import latest_completed_session, xnys_sessions


def assess_decision_freshness(*, market_session: str, evaluated_at: datetime) -> dict[str, Any]:
    """Compare a pinned decision session with the latest completed XNYS session.

    This is a display-only assessment. It never rebuilds or mutates a decision.
    """

    expected = latest_completed_session(evaluated_at).session_date
    if market_session == expected:
        return {
            "status": "current",
            "market_session": market_session,
            "expected_session": expected,
            "lag_sessions": 0,
        }
    if market_session > expected:
        return {
            "status": "future_blocked",
            "market_session": market_session,
            "expected_session": expected,
            "lag_sessions": None,
        }

    sessions = xnys_sessions(market_session, expected)
    lag_sessions = max(len(sessions) - 1, 1)
    return {
        "status": "stale",
        "market_session": market_session,
        "expected_session": expected,
        "lag_sessions": lag_sessions,
    }
