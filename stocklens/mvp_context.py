from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from .analysis import build_analysis_catalog, load_analysis_overlays, load_signal_research
from .market_calendar import assess_market_times
from .storage import SnapshotStore


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RESEARCH_DIR = ROOT / "research"


def with_runtime_freshness(universe, status: dict, now: datetime | None = None) -> dict:
    """Keep persisted collection health while reassessing freshness for the current session."""
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    assessment = assess_market_times(universe["market_time_utc"].tolist(), checked_at)
    current = deepcopy(status)
    current["recorded_freshness_state"] = status.get("freshness_state")
    current["runtime_freshness_state"] = assessment.state
    current["runtime_expected_session_date"] = assessment.expected_session_date
    current["runtime_checked_at_utc"] = checked_at.replace(microsecond=0).isoformat()
    current["runtime_freshness"] = assessment.to_dict()
    current["runtime_eligible_for_signals"] = bool(status.get("eligible_for_signals")) and assessment.can_publish
    return current


@st.cache_data(ttl=60, show_spinner=False)
def load_mvp_context(snapshot_mtime: float) -> tuple[list[dict], dict, dict]:
    del snapshot_mtime
    store = SnapshotStore(DATA_DIR)
    universe = store.load()
    status = with_runtime_freshness(universe, store.load_status())
    overlays = load_analysis_overlays(RESEARCH_DIR / "mvp-analysis-overlays.json")
    signals = load_signal_research(RESEARCH_DIR / "signal-research-summary.json")
    return build_analysis_catalog(universe, status, overlays), status, signals


def context() -> tuple[list[dict], dict, dict]:
    return load_mvp_context((DATA_DIR / "universe.csv").stat().st_mtime)


def kst_display(value: str | None, *, date_only: bool = False) -> str:
    if not value:
        return "확인 불가"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        fmt = "%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M KST"
        return parsed.astimezone(ZoneInfo("Asia/Seoul")).strftime(fmt)
    except (ValueError, TypeError):
        return str(value)
