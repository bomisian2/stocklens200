from __future__ import annotations

from typing import Any

import streamlit as st

from .analysis import STATE_LABELS
from .mvp_context import kst_display


STATE_ICONS = {"verified": ":material/verified:", "partial": ":material/pending:", "research_only": ":material/science:", "stale": ":material/history:", "unavailable": ":material/block:", "blocked": ":material/gpp_bad:"}


def state_text(state: str) -> str:
    return STATE_LABELS.get(state, state)


def render_state(area: dict[str, Any]) -> None:
    state = str(area["state"])
    st.markdown(f"{STATE_ICONS.get(state, '')} **{state_text(state)}**")
    st.write(area["summary"])
    if area.get("as_of"):
        st.caption(f"기준일: {kst_display(str(area['as_of']), date_only=True)}")
    if area.get("hold_reason"):
        st.caption(f"보류 사유: {area['hold_reason']}")


def render_area_details(title: str, area: dict[str, Any], *, expanded: bool = False) -> None:
    with st.expander(title, expanded=expanded):
        render_state(area)
        for detail in area.get("details") or []:
            st.write(f"- {detail}")
        sources = area.get("sources") or []
        if sources:
            st.markdown("**출처**")
            for source in sources:
                st.markdown(f"- [{source['label']}]({source['url']})")


def render_market_alert(status: dict[str, Any]) -> None:
    state = str(status.get("data_state") or "unverified")
    if state == "stale":
        st.error("시장 스냅샷이 오래되어 신규 분석이나 신호에 사용할 수 없습니다.")
    elif state == "unverified":
        st.error("시장 시각을 검증할 수 없어 신규 분석과 신호를 보류합니다.")
    elif status.get("used_cache"):
        st.warning("공급원 장애로 마지막 정상 시장 스냅샷을 표시합니다.")
    elif status.get("degraded"):
        st.info("일부 공급원 문제가 있었지만 검증된 시장 스냅샷을 표시합니다.")
