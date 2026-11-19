from __future__ import annotations

from datetime import date, datetime, timezone

from stocklens.market_calendar import (
    assess_market_times,
    is_xnys_session,
    latest_completed_session,
    market_phase,
    next_xnys_session,
    xnys_session_bounds,
)


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


def test_intraday_quote_is_not_promoted_to_a_completed_session() -> None:
    now = datetime(2026, 10, 13, 15, 28, tzinfo=timezone.utc)
    result = assess_market_times(["2026-10-13T15:27:00+00:00"] * 200, now)

    assert market_phase(now) == "regular_session"
    assert result.state == "unverified"
    assert result.expected_session_date == "2026-10-12"
    assert result.observed_session_dates == ("2026-10-13",)
    assert result.evaluation_phase == "regular_session"
    assert "expected=2026-10-12" in result.reason
    assert "observed=2026-10-13" in result.reason


def test_same_session_quote_is_eligible_only_after_regular_close() -> None:
    now = datetime(2026, 10, 13, 20, 30, tzinfo=timezone.utc)
    result = assess_market_times(["2026-10-13T20:00:00+00:00"] * 200, now)

    assert market_phase(now) == "post_close"
    assert result.state == "verified"
    assert result.expected_session_date == "2026-10-13"
    assert result.evaluation_phase == "post_close"


def test_missing_nasdaq_times_remain_unverified_with_safe_counts() -> None:
    now = datetime(2026, 10, 13, 23, 15, tzinfo=timezone.utc)
    result = assess_market_times([None] * 200, now)

    assert result.state == "unverified"
    assert result.valid_time_count == 0
    assert result.missing_time_count == 200
    assert result.invalid_time_count == 0
    assert result.evaluation_phase == "post_close"
    assert "missing=200" in result.reason


def test_collection_time_never_substitutes_for_missing_market_time() -> None:
    now = datetime(2026, 10, 13, 23, 15, tzinfo=timezone.utc)
    result = assess_market_times([None] * 199 + ["not-a-time"], now)

    assert result.state == "unverified"
    assert result.market_time_max_utc is None
    assert result.missing_time_count == 199
    assert result.invalid_time_count == 1


def test_session_helpers_reject_weekend_and_skip_to_monday() -> None:
    assert is_xnys_session(date(2026, 8, 29)) is False
    assert next_xnys_session(date(2026, 8, 28)) == date(2026, 8, 31)


def test_session_bounds_distinguish_regular_close() -> None:
    opened, closed = xnys_session_bounds("2026-08-27")
    assert opened.isoformat() == "2026-08-27T13:30:00+00:00"
    assert closed.isoformat() == "2026-08-27T20:00:00+00:00"
