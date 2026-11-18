from __future__ import annotations

import streamlit as st

from stocklens.analysis import find_company
from stocklens.mvp_context import context


catalog, _, _ = context()
company = find_company(catalog, "GOOGL")
research = company["research_value"]

st.warning("B등급 연구 참고 · 투자 신호 아님")
st.title("Alphabet Inc. · GOOGL")
st.caption("공식 실적과 연구 가정을 분리한 읽기 전용 화면입니다. 운영 valuation CSV와 신호에 연결되지 않습니다.")

metrics = st.columns(3, border=True)
metrics[0].metric("공식 재무 기준일", "2026-06-30")
metrics[1].metric("시장 기준일", "2026-10-09")
metrics[2].metric("운영 자격", "false · hold")

st.subheader("주당 연구 참고 외곽 범위")
st.markdown(f"## ${research['range']['low']:.2f} ~ ${research['range']['high']:.2f}")
st.caption("공식 중간 적정가 없음 · 현재가 자동 비교 없음 · 목표가격·매수구간·저평가율 아님")

st.subheader("가장 큰 불확실성")
for item in research["top_uncertainties"]:
    with st.container(border=True):
        st.markdown(f"**{item['rank']}위 · {item['name']}**")
        st.caption(f"연구 그리드의 기계적 주당 가치 폭: ${item['mechanical_span']:.2f}")

st.subheader("주주가치 브리지 처리")
branch_columns = st.columns(2, border=True)
for column, branch in zip(branch_columns, research["bridge_branches"]):
    with column:
        st.markdown(f"**{branch['name']}**")
        st.write(f"${branch['low']:.2f} ~ ${branch['high']:.2f}")
        st.caption(branch["basis"])
st.caption("두 분기는 상호배타적이며 우선주 청구권 차감과 전환 희석을 동시에 적용하지 않습니다.")

with st.expander("공식 실적 및 연구 근거"):
    for detail in company["areas"]["financial"]["details"]:
        st.write(f"- {detail}")
    st.markdown(f"[가치평가 Draft PR #2]({research['source_pr']})")

with st.expander("정식 적정가를 만들지 않은 이유"):
    st.write(f"- {company['areas']['valuation']['hold_reason']}")
    st.write("- 정규화 EPS와 완전한 point-in-time 주주가치 입력이 없습니다.")
    st.write("- 연구 자료는 운영 파일, 종목 추천, 알림 및 매매 신호에 사용할 수 없습니다.")

with st.expander("공식 공시 식별과 원본 상태"):
    source = research["source_status"]
    st.write(f"SEC accession: {source['accession']}")
    st.write(f"접수 시각: {source['accepted_at_utc']}")
    st.write(f"원본 해시 검증: {source['raw_file_hash_verified']}")
    st.caption(source["acquisition_state"])

st.warning(research["disclaimer"])
