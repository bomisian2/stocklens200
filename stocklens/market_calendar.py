from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from statistics import median
from typing import Iterable

import exchange_calendars as xcals
import pandas as pd


@dataclass(frozen=True)
class MarketSession:
    session_date: str
    open_utc: str
    close_utc: str


@dataclass(frozen=True)
class FreshnessAssessment:
    state: str
    expected_session_date: str
    observed_session_dates: tuple[str, ...]
    valid_time_count: int
    missing_time_count: int
    invalid_time_count: int
    market_time_min_utc: str | None
    market_time_median_utc: str | None
    market_time_max_utc: str | None
    reason: str

    @property
    def can_publish(self) -> bool:
        return self.state == "verified"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@lru_cache(maxsize=1)
def _xnys_calendar():
    # Explicit bounds avoid depending on the package's moving default window.
    return xcals.get_calendar("XNYS", start="2000-01-01", end="2035-12-31")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def latest_completed_session(now: datetime) -> MarketSession:
    """Return the most recent NYSE session whose regular close has passed."""
    current = _as_utc(now)
    calendar = _xnys_calendar()
    start = (current - timedelta(days=14)).date().isoformat()
    end = (current + timedelta(days=1)).date().isoformat()
    sessions = calendar.sessions_in_range(start, end)
    completed: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    current_timestamp = pd.Timestamp(current)
    for session in sessions:
        close = calendar.session_close(session)
        if close <= current_timestamp:
            completed.append((session, close))
    if not completed:
        raise ValueError("no completed XNYS session found")
    session, close = completed[-1]
    opened = calendar.session_open(session)
    return MarketSession(
        session_date=session.date().isoformat(),
        open_utc=opened.to_pydatetime().astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        close_utc=close.to_pydatetime().astimezone(timezone.utc).replace(microsecond=0).isoformat(),
    )


def next_session_date(session_date: str) -> str:
    """Return the XNYS session immediately after a verified session date."""
    calendar = _xnys_calendar()
    session = pd.Timestamp(session_date)
    if not calendar.is_session(session):
        raise ValueError(f"not an XNYS session: {session_date}")
    return calendar.next_session(session).date().isoformat()


def assess_market_times(values: Iterable[str | None], now: datetime) -> FreshnessAssessment:
    """Validate per-security market times against the latest completed US session."""
    expected = latest_completed_session(now)
    expected_open = datetime.fromisoformat(expected.open_utc)
    expected_close = datetime.fromisoformat(expected.close_utc)
    parsed: list[datetime] = []
    missing = 0
    invalid = 0

    for value in values:
        if not value:
            missing += 1
            continue
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            parsed.append(_as_utc(timestamp))
        except (TypeError, ValueError):
            invalid += 1

    observed_dates = tuple(sorted({value.date().isoformat() for value in parsed}))
    ordered = sorted(parsed)
    minimum = ordered[0].replace(microsecond=0).isoformat() if ordered else None
    maximum = ordered[-1].replace(microsecond=0).isoformat() if ordered else None
    middle = (
        datetime.fromtimestamp(median(value.timestamp() for value in ordered), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        if ordered
        else None
    )

    if missing or invalid or not parsed:
        state = "unverified"
        reason = "one or more market timestamps are missing or invalid"
    elif len(observed_dates) != 1:
        state = "degraded"
        reason = "market timestamps span multiple UTC dates"
    elif observed_dates[0] < expected.session_date:
        state = "stale"
        reason = "market timestamps precede the latest completed session"
    elif observed_dates[0] > expected.session_date:
        state = "unverified"
        reason = "market timestamps are later than the expected completed session"
    elif any(value < expected_open or value > expected_close + timedelta(minutes=15) for value in parsed):
        state = "degraded"
        reason = "market timestamps fall outside the expected regular-session window"
    else:
        state = "verified"
        reason = "all market timestamps match the latest completed XNYS session"

    return FreshnessAssessment(
        state=state,
        expected_session_date=expected.session_date,
        observed_session_dates=observed_dates,
        valid_time_count=len(parsed),
        missing_time_count=missing,
        invalid_time_count=invalid,
        market_time_min_utc=minimum,
        market_time_median_utc=middle,
        market_time_max_utc=maximum,
        reason=reason,
    )
