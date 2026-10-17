from __future__ import annotations

import streamlit as st

from stocklens.analysis import ANALYSIS_AREAS, find_company
from stocklens.mvp_context import context, kst_display
from stocklens.ui import render_area_details, render_market_alert, state_text


catalog, status, _ = context()
render_market_alert(status)
tickers = [row["ticker"] for row in catalog]
default_ticker = st.session_state.get("selected_ticker", "GOOGL")
if default_ticker not in tickers:
    default_ticker = tickers[0]

st.title("기업별 상세 분석")
ticker = st.selectbox("기업 선택", tickers, index=tickers.index(default_ticker), format_func=lambda value: f"{value} · {find_company(catalog, value)['company_name']}", key="company_ticker")
st.session_state["selected_ticker"] = ticker
company = find_company(catalog, ticker)

st.subheader(f"{company['company_name']} · {ticker}")
overview = st.columns(4, border=True)
overview[0].metric("시총 순위", f"{company['rank']}위")
overview[1].metric("시장 가격", f"${company['price']:,.2f}")
overview[2].metric("시장 기준일", company["market_session_date"])
overview[3].metric("운영 투자판단", "보류")
st.caption(f"{company['sector']} · {company['industry']} · {company['country']}")

st.subheader("데이터 확보 및 검증 상태")
for start in (0, 3):
    columns = st.columns(3, border=True)
    for column, area_name in zip(columns, ANALYSIS_AREAS[start : start + 3]):
        with column:
            st.caption({"market": "시장 데이터", "business": "사업 경쟁력", "financial": "재무 품질", "outlook": "미래 전망", "valuation": "기업 가치", "technical": "기술 분석"}[area_name])
            area = company["areas"][area_name]
            st.markdown(f"**{state_text(area['state'])}**")
            st.caption(area["summary"])

render_area_details("1. 기업 개요와 현재 시장 데이터", company["areas"]["market"], expanded=True)
render_area_details("2. 주요 사업과 경쟁력", company["areas"]["business"])
render_area_details("3. 확인된 재무 실적", company["areas"]["financial"])
render_area_details("4. 미래 전망과 공식 가이던스", company["areas"]["outlook"])
render_area_details("5. 적정가 또는 가치평가 보류 상태", company["areas"]["valuation"])
render_area_details("6. 기술적 분석과 단기 신호 검증 상태", company["areas"]["technical"])

if company.get("research_value"):
    st.info("상단 ‘GOOGL 연구’ 화면에서 연구 근거를 확인할 수 있습니다. 연구 범위는 목표가격·매수가가 아닙니다.")

with st.expander("7. 주요 위험 및 판단 무효화 조건"):
    st.markdown("**주요 위험**")
    for item in company.get("risks") or ["검증된 기업별 위험 자료가 아직 없습니다."]:
        st.write(f"- {item}")
    st.markdown("**판단 무효화 조건**")
    for item in company.get("invalidation_conditions") or ["새 공시와 원천자료 확보 후 재검토가 필요합니다."]:
        st.write(f"- {item}")

with st.expander("8. 출처·기준일·마지막 갱신 시각"):
    for area_name in ANALYSIS_AREAS:
        area = company["areas"][area_name]
        st.markdown(f"**{area_name} · {state_text(area['state'])}**")
        area_date = company["market_session_date"] if area_name == "market" else kst_display(area.get("as_of"), date_only=True)
        st.caption(f"기준일: {area_date}")
        for source in area.get("sources") or []:
            st.markdown(f"- [{source['label']}]({source['url']})")
    st.caption(f"시장 데이터 수집 시각: {kst_display(company['collected_at_utc'])}")

st.warning("자료가 부족한 영역에서는 임의 재무 수치·적정가·매수 신호를 생성하지 않습니다.")
