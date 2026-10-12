from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import pandas as pd
import streamlit as st

from stocklens.storage import SnapshotError, SnapshotStore


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SOURCE_LABELS = {
    "nasdaq_screener": "Nasdaq 전체 상장 종목",
    "yahoo_screener": "Yahoo 거래소 스크리너",
    "last_good_cache": "마지막 정상 스냅샷(캐시)",
}

st.set_page_config(
    page_title="StockLens 200 v2",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; max-width: 1500px;}
    div[data-testid="stMetric"] {background: #13283a; border: 1px solid #24445c; border-radius: 14px; padding: 14px;}
    div[data-testid="stMetric"] * {color: #f8fafc !important;}
    @media (max-width: 700px) {
      .block-container {padding: .8rem .65rem 4rem;}
      div[data-testid="stMetric"] {padding: 10px;}
      h1 {font-size: 1.7rem !important;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60, show_spinner=False)
def load_snapshot(mtime: float) -> tuple[pd.DataFrame, dict]:
    del mtime
    store = SnapshotStore(DATA_DIR)
    return store.load(), store.load_status()


def kst_display(value: str | None) -> str:
    if not value:
        return "확인 불가"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    except (ValueError, TypeError):
        return str(value)


st.title("StockLens 200 v2")
st.caption("미국 거래소 상장기업 시가총액 Top 200 · Actions 수집 / 앱은 검증된 스냅샷만 표시")

try:
    snapshot_path = DATA_DIR / "universe.csv"
    frame, status = load_snapshot(snapshot_path.stat().st_mtime)
except (OSError, SnapshotError) as exc:
    st.error("검증된 200개 기업 스냅샷이 없습니다. 수집 작업을 먼저 실행해야 합니다.")
    st.code("python scripts/update_data.py", language="bash")
    st.caption(f"내부 상태: {type(exc).__name__}")
    st.stop()

source = str(status.get("source") or frame["source"].iloc[0])
fallback_market_time = pd.to_datetime(frame["market_time_utc"], utc=True).median().isoformat()
data_time = str(status.get("market_data_as_of_utc") or fallback_market_time)
data_state = str(status.get("data_state") or ("degraded" if status.get("degraded") else "healthy"))
market_session = str(status.get("market_session_date") or "확인 불가")
if data_state == "stale":
    st.error(
        "신규 수집 검증에 실패했고 저장 데이터도 최신 완료 거래일보다 오래되었습니다. "
        "이 스냅샷은 신규 매매 신호 계산에 사용할 수 없습니다."
    )
elif data_state == "unverified":
    st.error("거래 시각을 검증할 수 없어 마지막 정상 스냅샷만 표시합니다. 신규 매매 신호는 보류됩니다.")
elif status.get("used_cache"):
    st.warning(
        "외부 공급원이 모두 실패하여 마지막 정상 스냅샷을 표시합니다. "
        f"데이터 기준: {kst_display(data_time)}"
    )
elif status.get("degraded"):
    st.info("1차 공급원 장애 후 백업 공급원으로 정상 갱신했습니다.")

metrics = st.columns(4)
metrics[0].metric("검증 기업 수", f"{len(frame):,}개")
metrics[1].metric("후보 상장증권", f"{int(status.get('candidate_count') or 0):,}개")
metrics[2].metric("200위 시가총액", f"${frame['market_cap'].iloc[-1] / 1e9:,.1f}B")
metrics[3].metric("거래 세션", market_session)

with st.sidebar:
    st.header("데이터 상태")
    st.write(SOURCE_LABELS.get(source, source))
    st.caption(f"상태: {data_state} · 거래 세션: {market_session}")
    st.caption(f"시장 시각 중앙값: {kst_display(data_time)}")
    st.caption(f"수집 시각: {kst_display(status.get('data_generated_at_utc'))}")
    st.caption("앱 화면에서는 외부 금융 API를 호출하지 않습니다.")
    if st.button("저장 데이터 다시 읽기", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.caption("EPS·PER·안전마진은 사용자가 입력하지 않는 자동 검증 방식으로 2단계에서 연결됩니다.")

tab_universe, tab_value, tab_signal, tab_status = st.tabs(
    ["Top 200", "적정가 엔진", "단기 신호", "수집 상태"]
)

with tab_universe:
    q_col, sector_col = st.columns([2, 1])
    query = q_col.text_input("기업명 또는 티커", placeholder="NVDA, Berkshire...")
    sectors = ["전체", *sorted(s for s in frame["sector"].dropna().unique() if s != "Unknown")]
    sector = sector_col.selectbox("섹터", sectors)

    view = frame.copy()
    if query:
        mask = view["ticker"].str.contains(query, case=False, na=False) | view["name"].str.contains(
            query, case=False, na=False
        )
        view = view[mask]
    if sector != "전체":
        view = view[view["sector"] == sector]

    display = view[
        ["rank", "ticker", "name", "sector", "country", "market_cap", "price", "change_percent"]
    ].rename(
        columns={
            "rank": "순위",
            "ticker": "티커",
            "name": "기업명",
            "sector": "섹터",
            "country": "본사 국가(가용 시)",
            "market_cap": "시가총액",
            "price": "최근가",
            "change_percent": "등락률",
        }
    )
    st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        height=610,
        column_config={
            "순위": st.column_config.NumberColumn(format="%d", width="small"),
            "티커": st.column_config.TextColumn(width="small"),
            "시가총액": st.column_config.NumberColumn(format="$%,.0f"),
            "최근가": st.column_config.NumberColumn(format="$%.2f"),
            "등락률": st.column_config.NumberColumn(format="%+.2f%%"),
        },
    )
    st.caption(
        "미국 거래소 상장 보통주·ADR을 대상으로 하며, ETF·ETN·워런트·권리·우선주·채권은 제외합니다. "
        "복수 주식 클래스는 거래량이 큰 대표 티커 하나만 유지합니다."
    )

with tab_value:
    valuation_path = DATA_DIR / "valuations.csv"
    if valuation_path.exists():
        valuations = pd.read_csv(valuation_path)
        valuation_status_path = DATA_DIR / "valuation_status.json"
        valuation_status = (
            json.loads(valuation_status_path.read_text(encoding="utf-8"))
            if valuation_status_path.exists()
            else {}
        )
        financial_freshness = str(valuation_status.get("financial_freshness_state") or "unverified")
        if financial_freshness == "stale":
            st.warning(
                "재무 공급원 갱신에 실패해 마지막 정상 평가 스냅샷을 보존 중입니다. "
                "이 결과는 신규 매매 신호에 사용되지 않습니다."
            )
        elif financial_freshness != "verified":
            st.warning(
                "재무 데이터 최신성을 검증하지 못했습니다. 검증 완료 전에는 신규 평가·매매 신호에 사용하지 않습니다."
            )
        evaluated = valuations[valuations["status"] == "evaluated"].copy()
        provisional = valuations[valuations["status"] == "provisional"].copy()
        value_metrics = st.columns(5)
        value_metrics[0].metric("검증 평가", f"{len(evaluated):,}개")
        value_metrics[1].metric("과거기반 잠정가", f"{len(provisional):,}개")
        value_metrics[2].metric("판단 보류", f"{len(valuations) - len(evaluated) - len(provisional):,}개")
        value_metrics[3].metric("저평가 판정", f"{int(evaluated['judgement'].eq('저평가').sum()):,}개")
        value_metrics[4].metric("모델 버전", str(valuation_status.get("model_version") or "확인 불가"))

        st.caption(
            f"평가 시장 기준: {kst_display(valuation_status.get('valuation_as_of_market_utc'))} · "
            "SEC 제출 재무와 공식 미 국채금리를 사용하며 현재 주가는 적정가 계산식에 넣지 않습니다."
        )
        view_columns = [
            "ticker", "company_name", "price", "conservative_value", "base_value",
            "optimistic_value", "upside_to_base", "favorable_buy_low", "favorable_buy_high",
            "confidence_score", "judgement", "status",
        ]
        table = valuations[view_columns].rename(
            columns={
                "ticker": "티커", "company_name": "기업명", "price": "현재가",
                "conservative_value": "보수 적정가", "base_value": "기본 적정가",
                "optimistic_value": "낙관 적정가", "upside_to_base": "기본 대비 여력",
                "favorable_buy_low": "매수 구간 하단", "favorable_buy_high": "매수 구간 상단",
                "confidence_score": "신뢰도", "judgement": "판단", "status": "상태",
            }
        )
        table["기본 대비 여력"] = table["기본 대비 여력"] * 100
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            height=500,
            column_config={
                "현재가": st.column_config.NumberColumn(format="$%.2f"),
                "보수 적정가": st.column_config.NumberColumn(format="$%.2f"),
                "기본 적정가": st.column_config.NumberColumn(format="$%.2f"),
                "낙관 적정가": st.column_config.NumberColumn(format="$%.2f"),
                "기본 대비 여력": st.column_config.NumberColumn(format="%+.1f%%"),
                "매수 구간 하단": st.column_config.NumberColumn(format="$%.2f"),
                "매수 구간 상단": st.column_config.NumberColumn(format="$%.2f"),
                "신뢰도": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
            },
        )

        selected_ticker = st.selectbox("상세 계산 확인", valuations["ticker"].tolist())
        selected = valuations[valuations["ticker"] == selected_ticker].iloc[0]
        st.subheader(f"{selected_ticker} · {selected['company_name']}")
        if selected["status"] == "provisional":
            st.info(f"과거 실적 기반 잠정가이며 투자판단은 보류합니다: {selected['change_reason']}")
            st.write(f"**사용 모델:** {selected['methods_used']} · 신뢰도 {int(selected['confidence_score'])}/100")
            with st.expander("잠정 계산 근거"):
                st.json(json.loads(selected["model_details_json"]))
        elif selected["status"] != "evaluated":
            st.warning(f"판단 보류: {selected['change_reason']}")
        else:
            st.write(f"**성장 동력:** {selected['growth_drivers']}")
            st.write(f"**위험 요인:** {selected['risk_factors']}")
            st.write(f"**사용 모델:** {selected['methods_used']} · 신뢰도 {int(selected['confidence_score'])}/100")
            st.write(f"**EPS 전망 수정 추세:** {selected['eps_revision_trend']}")
            with st.expander("모델별 계산 근거"):
                st.json(json.loads(selected["model_details_json"]))

        history_path = DATA_DIR / "valuation_history.csv"
        if history_path.exists():
            history = pd.read_csv(history_path)
            history = history[history["ticker"] == selected_ticker].sort_values("evaluated_at_utc")
            if not history.empty:
                st.caption("날짜별 적정가 변화")
                st.dataframe(history, hide_index=True, width="stretch")
        limitations = valuation_status.get("limitations") or []
        if limitations:
            with st.expander("현재 데이터 제약"):
                for limitation in limitations:
                    st.write(f"- {limitation}")
    else:
        st.warning("2단계 개발 중: 검증된 적정가 스냅샷이 아직 생성되지 않았습니다.")
        st.write("SEC 재무 수집·시점 검증·모델 입력 게이트를 통과한 결과만 이 탭에 표시합니다.")

with tab_signal:
    signals_path = DATA_DIR / "signals.csv"
    if signals_path.exists():
        st.dataframe(pd.read_csv(signals_path), hide_index=True, width="stretch")
    else:
        st.warning("3단계 미완료: 워크포워드 단기 신호와 QQQ 초과수익 검증 결과가 아직 없습니다.")
        st.write("표본 수 기준을 통과하기 전에는 대시보드 상단에 매수 신호를 표시하지 않습니다.")

with tab_status:
    st.json(status)
    if status.get("provider_errors"):
        st.caption("공급원 오류는 응답 본문·환경변수·비밀정보를 제외한 유형만 저장합니다.")
    st.write(
        "현재 완료: 전체 거래소 후보 수집, 비기업 증권 제거, 복수 클래스 중복 제거, 시총 정렬, "
        "3회 지수 백오프 재시도, 백업 공급원, 마지막 정상 캐시, 원자적 저장, 앱 무네트워크 표시."
    )

st.divider()
st.caption(
    "연구용 정보 서비스이며 투자 권유가 아닙니다. 무료 공급원 지연·정정·종목 분류 오차가 있을 수 있습니다."
)
