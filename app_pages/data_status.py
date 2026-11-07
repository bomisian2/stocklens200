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

first = st.columns(4, border=True)
first[0].metric("Top 200 시장 데이터", f"{coverage['company_count']}개")
first[1].metric("저장 데이터 품질", str(status.get("data_state") or "확인 불가"))
first[2].metric("현재 최신성", str(status.get("runtime_freshness_state") or status.get("freshness_state") or "확인 불가"))
first[3].metric("시장 기준일", str(status.get("market_session_date") or "확인 불가"))
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
    st.write(f"수집 당시 최신성: {status.get('recorded_freshness_state') or status.get('freshness_state')} · 현재 최신성: {status.get('runtime_freshness_state') or '확인 불가'}")
    st.write(f"현재 시점 마지막 완료 거래일: {status.get('runtime_expected_session_date') or '확인 불가'}")
    st.write(f"실행 시점 신호 사용 자격: {status.get('runtime_eligible_for_signals', False)}")
    st.write(f"현재 최신성 확인 시각: {kst_display(status.get('runtime_checked_at_utc'))}")
    st.write(f"시장 데이터 기준: {kst_display(status.get('market_data_as_of_utc'))}")
    st.write(f"마지막 수집 시각: {kst_display(status.get('data_generated_at_utc'))}")
    st.write(f"공급원: {status.get('data_source')} · 독립 검증: {status.get('verification_source')}")
    st.json({"quality": status.get("quality"), "cross_validation": status.get("cross_validation")})
    if status.get("provider_errors"):
        st.warning("공급원 오류가 기록되어 있습니다. 응답 본문과 비밀정보는 표시하지 않습니다.")
        st.json(status["provider_errors"])

st.caption("앱은 외부 금융 API를 직접 호출하지 않으며 저장된 스냅샷과 격리 연구 요약만 읽습니다.")

st.subheader("공개 표시·재배포 권한")
st.error("운영 배포 보류 · Yahoo/yfinance와 Nasdaq 웹 데이터의 공개 대시보드 표시·재배포 권한이 확인되지 않았습니다.")
st.markdown(
    "- [Yahoo 이용약관](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)\n"
    "- [yfinance 사용상 주의](https://github.com/ranaroussi/yfinance#readme)\n"
    "- [Nasdaq 법적 고지](https://www.nasdaq.com/legal)"
)
st.caption("오픈소스 라이브러리의 라이선스와 라이브러리로 내려받은 시장 데이터의 재배포 권리는 서로 다릅니다.")
