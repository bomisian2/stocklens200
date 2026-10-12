from __future__ import annotations

import pandas as pd
import streamlit as st

from stocklens.analysis import ANALYSIS_AREAS, STATE_LABELS, coverage_metrics
from stocklens.mvp_context import context, kst_display
from stocklens.ui import render_market_alert


catalog, status, signals = context()
coverage = coverage_metrics(catalog, signals)
st.title("데이터 상태")
st.caption("전체 시스템의 시장 데이터 상태와 개별 분석 영역의 준비 상태를 분리해 표시합니다.")
render_market_alert(status)

first = st.columns(3, border=True)
first[0].metric("Top 200 시장 데이터", f"{coverage['company_count']}개")
first[1].metric("시장 건강 상태", str(status.get("data_state") or "확인 불가"))
first[2].metric("시장 기준일", str(status.get("market_session_date") or "확인 불가"))
second = st.columns(4, border=True)
second[0].metric("공식 재무 확보", f"{coverage['official_financial_companies']}개")
second[1].metric("연구 가치 가능", f"{coverage['research_value_companies']}개")
second[2].metric("정식 가치평가", f"{coverage['formal_valuation_companies']}개")
second[3].metric("독립 검증 신호", f"{coverage['independent_signal_count']}개")

rows = []
for company in catalog:
    unavailable = [name for name in ANALYSIS_AREAS[1:] if company["areas"][name]["state"] in {"unavailable", "blocked", "stale"}]
    rows.append({
        "ticker": company["ticker"],
        "company_name": company["company_name"],
        "financial_periods": company["official_financial_periods"],
        "valuation_state": STATE_LABELS[company["areas"]["valuation"]["state"]],
        "blocked_areas": ", ".join(unavailable) if unavailable else "없음",
        "primary_hold_reason": company["areas"]["valuation"]["hold_reason"],
    })
st.subheader("종목별 보류 상태")
st.dataframe(
    pd.DataFrame(rows),
    hide_index=True,
    height=500,
    column_config={
        "ticker": "티커",
        "company_name": "기업명",
        "financial_periods": st.column_config.NumberColumn("공식 재무기간", format="%d"),
        "valuation_state": "가치평가 상태",
        "blocked_areas": "부족 영역",
        "primary_hold_reason": "주요 보류 사유",
    },
)

with st.expander("시장 수집·검증 상세"):
    st.write(f"시장 데이터 기준: {kst_display(status.get('market_data_as_of_utc'))}")
    st.write(f"마지막 수집 시각: {kst_display(status.get('data_generated_at_utc'))}")
    st.write(f"공급원: {status.get('data_source')} · 독립 검증: {status.get('verification_source')}")
    st.json({"quality": status.get("quality"), "cross_validation": status.get("cross_validation")})
    if status.get("provider_errors"):
        st.warning("공급원 오류가 기록되어 있습니다. 응답 본문과 비밀정보는 표시하지 않습니다.")
        st.json(status["provider_errors"])

st.caption("앱은 외부 금융 API를 직접 호출하지 않으며 저장된 스냅샷과 격리 연구 요약만 읽습니다.")
