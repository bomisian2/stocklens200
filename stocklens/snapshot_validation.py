from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .market_calendar import FreshnessAssessment, assess_market_times
from .storage import SnapshotStore


def stored_snapshot_errors(frame: pd.DataFrame, status: dict[str, Any]) -> list[str]:
    """Validate what was known when a last-good snapshot was persisted.

    This intentionally does not compare the snapshot with today's market session. A valid
    historical artifact must stay reproducible after another session completes.
    """
    errors: list[str] = []
    metrics = SnapshotStore.quality_metrics(frame)
    market_times = pd.to_datetime(frame["market_time_utc"], errors="coerce", utc=True, format="mixed")
    observed_sessions = sorted({value.date().isoformat() for value in market_times.dropna()})

    if status.get("freshness_state") != "verified":
        errors.append("stored snapshot was not verified when collected")
    if status.get("data_state") != "healthy":
        errors.append("stored snapshot data_state is not healthy")
    if status.get("company_count") != 200:
        errors.append("stored status company_count is not 200")
    if len(observed_sessions) != 1:
        errors.append("stored market timestamps do not identify exactly one session")
    elif status.get("market_session_date") != observed_sessions[0]:
        errors.append("stored status session does not match per-company market timestamps")
    if not (status.get("cross_validation") or {}).get("passed"):
        errors.append("stored independent-source cross-validation did not pass")
    if metrics["sector_coverage"] < 0.80 or metrics["country_coverage"] < 0.80:
        errors.append("stored metadata coverage is below 80%")
    return errors


def operational_freshness(
    frame: pd.DataFrame,
    status: dict[str, Any],
    now: datetime | None = None,
) -> tuple[FreshnessAssessment, list[str]]:
    """Reassess whether a snapshot is usable at the current operating time."""
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    assessment = assess_market_times(frame["market_time_utc"].tolist(), checked_at)
    errors: list[str] = []
    if assessment.state != "verified":
        errors.append(f"runtime freshness is {assessment.state}: {assessment.reason}")
    if status.get("market_session_date") != assessment.expected_session_date:
        errors.append("snapshot does not represent the latest completed market session")
    return assessment, errors
