from __future__ import annotations

from datetime import datetime, timezone

from stocklens.market_calendar import assess_market_times, latest_completed_session


def test_calendar_skips_us_market_holiday() -> None:
    session = latest_completed_session(datetime(2026, 7, 4, 12, tzinfo=timezone.utc))
    assert session.session_date == "2026-07-02"


def test_calendar_close_reflects_daylight_saving_time() -> None:
    winter = latest_completed_session(datetime(2026, 1, 6, 1, tzinfo=timezone.utc))
    summer = latest_completed_session(datetime(2026, 7, 7, 1, tzinfo=timezone.utc))
    assert winter.close_utc.endswith("21:00:00+00:00")
    assert summer.close_utc.endswith("20:00:00+00:00")


def test_freshness_states_verified_stale_unverified_and_degraded() -> None:
    now = datetime(2026, 1, 3, 12, tzinfo=timezone.utc)
    assert assess_market_times(["2026-01-02T21:00:00+00:00"] * 200, now).state == "verified"
    assert assess_market_times(["2025-12-31T21:00:00+00:00"] * 200, now).state == "stale"
    assert assess_market_times([None] * 200, now).state == "unverified"
    mixed = ["2026-01-02T21:00:00+00:00"] * 199 + ["2025-12-31T21:00:00+00:00"]
    assert assess_market_times(mixed, now).state == "degraded"
