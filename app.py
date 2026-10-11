from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

st.set_page_config(page_title="StockLens 200 v2", page_icon="📈", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; max-width: 1500px;}
    div[data-testid="stMetric"] {background: #13283a; border: 1px solid #24445c; border-radius: 14px; padding: 14px;}
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
        st.dataframe(pd.read_csv(valuation_path), hide_index=True, width="stretch")
    else:
        st.warning("2단계 미완료: 재무 항목 교차검증과 적정가 엔진은 아직 운영 데이터로 검증되지 않았습니다.")
        st.write("예정 검증: EPS 일관성 · 이익/매출 성장 · 잉여현금흐름 · 순부채 · 업종별 PER 범위")

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
