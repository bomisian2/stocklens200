from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from stocklens.research_preview import ResearchPreviewError, load_research_preview


ROOT = Path(__file__).resolve().parent
PREVIEW_PATH = ROOT / "research" / "googl-v2.4.1-preview.json"

st.set_page_config(
    page_title="GOOGL 연구 미리보기 · StockLens 200",
    page_icon="🔬",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """
    <style>
      .block-container {max-width: 920px; padding-top: 3.75rem; padding-bottom: 4rem;}
      .hold-badge {
        display: inline-flex; padding: .32rem .65rem; border-radius: 999px;
        background: #f1f5f9; color: #334155; font-size: .8rem; font-weight: 700;
      }
      .range-card {
        border: 1px solid #cbd5e1; border-radius: 18px; padding: 1.2rem;
        background: linear-gradient(145deg, #f8fafc, #ffffff); margin: .65rem 0 1rem;
      }
      .range-label {font-size: .84rem; color: #475569; font-weight: 700;}
      .range-value {font-size: clamp(2rem, 8vw, 3.25rem); line-height: 1.1; font-weight: 820; color: #0f172a; margin: .35rem 0;}
      .range-note {font-size: .88rem; color: #64748b;}
      .driver-card {border-left: 4px solid #2563eb; padding: .55rem .75rem; margin-bottom: .65rem; background: #f8fafc; border-radius: 0 10px 10px 0;}
      .driver-rank {font-size: .75rem; color: #2563eb; font-weight: 800; letter-spacing: .04em;}
      .driver-name {font-size: 1rem; font-weight: 750; color: #0f172a;}
      .driver-span {font-size: .85rem; color: #475569; margin-top: .15rem;}
      .branch-card {border: 1px solid #e2e8f0; border-radius: 14px; padding: .9rem; background: #ffffff; min-height: 172px;}
      .branch-title {font-weight: 780; color: #0f172a;}
      .branch-range {font-size: 1.28rem; font-weight: 800; color: #1d4ed8; margin: .3rem 0;}
      @media (max-width: 700px) {
        .block-container {padding: 3.5rem .65rem 3.5rem;}
        h1 {font-size: 1.62rem !important; line-height: 1.2 !important;}
        h2 {font-size: 1.28rem !important;}
        div[data-testid="stHorizontalBlock"] {flex-wrap: wrap; gap: .5rem;}
        div[data-testid="stColumn"] {min-width: 100% !important; width: 100% !important; flex: 1 1 100% !important;}
        .range-card {padding: 1rem; border-radius: 14px;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def display_date(value: str, *, include_time: bool = False) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    converted = parsed.astimezone(ZoneInfo("Asia/Seoul"))
    return converted.strftime("%Y-%m-%d %H:%M KST" if include_time else "%Y-%m-%d")


try:
    preview = load_research_preview(PREVIEW_PATH)
except ResearchPreviewError as exc:
    st.error("검증된 연구 미리보기 데이터를 불러올 수 없습니다.")
    st.caption(f"내부 상태: {type(exc).__name__}")
    st.stop()

st.warning("B등급 연구 참고 · 투자 신호 아님")
st.title(f"{preview['company_name']} · {preview['ticker']}")
st.caption("공식 실적과 공개된 연구 가정을 분리해 보여 주는 격리 미리보기입니다.")

date_columns = st.columns(2)
date_columns[0].metric("공식 재무 기준일", preview["financial_as_of"])
date_columns[1].metric("주가 기준일", preview["market_session_date"])
date_columns[1].caption("미국 거래일 기준")

value_range = preview["research_per_share_range"]
st.markdown(
    f"""
    <div class="range-card">
      <div class="range-label">연구용 주당 외곽 범위 · USD</div>
      <div class="range-value">${value_range['low']:.2f}—${value_range['high']:.2f}</div>
      <div class="range-note">공식 중간 적정가 없음 · 목표가격/매수구간/저평가율 아님</div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.markdown('<span class="hold-badge">운영 자격 false · 판단 hold</span>', unsafe_allow_html=True)

st.subheader("가장 큰 불확실성")
for driver in preview["top_uncertainties"]:
    st.markdown(
        f"""
        <div class="driver-card">
          <div class="driver-rank">영향도 {driver['rank']}위</div>
          <div class="driver-name">{driver['name']}</div>
          <div class="driver-span">기계적 주당 영향 폭 ${driver['mechanical_per_share_span']:.2f}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with st.expander("영향도 근거 자세히 보기"):
    for driver in preview["top_uncertainties"]:
        st.markdown(f"**{driver['rank']}위 · {driver['name']}**")
        st.write(driver["basis"])
        st.caption(driver["warning"])

st.subheader("주주가치 브리지 처리")
branch_columns = st.columns(2)
for column, branch in zip(branch_columns, preview["branches"], strict=True):
    with column:
        st.markdown(
            f"""
            <div class="branch-card">
              <div class="branch-title">{branch['title']}</div>
              <div class="branch-range">${branch['low']:.2f}—${branch['high']:.2f}</div>
              <div>{branch['description']}</div>
              <div class="range-note">주식 수 경계 {branch['share_range_billions']}B</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.caption("두 방식은 상호배타적입니다. 우선주 청구권 차감과 전환 희석을 동시에 적용하지 않습니다.")

with st.expander("브리지 숫자와 회계 처리 보기"):
    bridge = preview["bridge_summary"]
    st.write(f"연구 기업가치: ${bridge['enterprise_value_low_billions']:.3f}B—${bridge['enterprise_value_high_billions']:.3f}B")
    st.write(f"현금·투자자산 장부가: ${bridge['cash_and_investments_billions']:.3f}B")
    st.write(f"금융부채: ${bridge['financial_debt_billions']:.3f}B")
    st.write(f"NCI 장부가 대용치: ${bridge['nci_proxy_billions']:.3f}B")
    st.write(f"우선주 청산우선권: ${bridge['preferred_liquidation_billions']:.3f}B")
    st.caption(bridge["operating_lease_policy"])
    st.caption(bridge["investment_recognition_policy"])

st.subheader("공식 근거와 검증 상태")
source = preview["official_source"]
source_columns = st.columns(2)
source_columns[0].metric("SEC Accession", source["accession"])
source_columns[1].metric("원본 SHA-256", "미확보")
st.write(f"접수 시각: {display_date(source['accepted_at_utc'], include_time=True)}")
st.caption(source["local_acquisition_status"])
st.link_button("공식 10-Q 열기", source["url"], width="stretch")

st.subheader("정식 적정가를 만들지 않은 이유")
for blocker in preview["formal_blockers"]:
    st.write(f"- {blocker}")

with st.expander("연구 전용 데이터 경계"):
    st.code(
        "operational_use = false\n"
        "eligible_for_signals = false\n"
        "eligible_for_ranking = false\n"
        "eligible_for_alerts = false\n"
        "formal_fair_value = null\n"
        "official_midpoint = null",
        language="text",
    )
    st.write("이 파일은 운영 data/ 디렉터리, valuation CSV, 신호, 알림 및 Top 200 정렬에서 사용되지 않습니다.")

st.warning(preview["disclaimer"])
st.caption("StockLens 200 연구 미리보기 · 별도 승인 전 main 병합 및 운영 배포 금지")
