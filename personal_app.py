from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from stocklens.personal.market_data import compute_technical_snapshot


DATA_DIR = Path(os.environ.get("STOCKLENS_PERSONAL_DATA_DIR", "private_data"))


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    return value if isinstance(value, dict) else None


@st.cache_data(show_spinner=False)
def load_financial(ticker: str, modified_ns: int) -> dict[str, Any] | None:
    del modified_ns
    return _load_json(DATA_DIR / "normalized" / "financials" / f"{ticker}.json")


@st.cache_data(show_spinner=False)
def load_market(ticker: str, modified_ns: int) -> pd.DataFrame:
    del modified_ns
    path = DATA_DIR / "market" / f"{ticker}.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _mtime(path: Path) -> int:
    return path.stat().st_mtime_ns if path.exists() else 0


def _format_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "미확보"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}" if not float(value).is_integer() else f"{value:,.0f}"
    return str(value)


st.set_page_config(page_title="StockLens 200 개인 연구", page_icon="🔒", layout="wide")
st.title("🔒 StockLens 200 개인 연구")
st.caption("로컬 전용 · 원시 데이터는 Git에 저장되지 않음 · 투자 권고 아님")

status_path = DATA_DIR / "status.json"
status = _load_json(status_path)
if status is None:
    st.warning("수집 데이터가 없습니다. 먼저 `python scripts/personal_refresh.py`를 실행하세요.")
    st.stop()

st.info(
    f"재무 검증 {status.get('financial_tickers_verified', 0)}종목 / "
    f"{status.get('financial_periods_verified', 0)}개 기간 · "
    f"시장 데이터 {status.get('market', {}).get('status', 'unavailable')}"
)

available = [
    path.stem for path in sorted((DATA_DIR / "normalized" / "financials").glob("*.json"))
]
if not available:
    st.error("정규화된 공식 재무자료가 없습니다.")
    st.stop()

ticker = st.selectbox("기업", available)
financial_path = DATA_DIR / "normalized" / "financials" / f"{ticker}.json"
financial = load_financial(ticker, _mtime(financial_path))
if not financial:
    st.error("재무 파일을 읽을 수 없습니다.")
    st.stop()

st.subheader(f"{financial['company']} ({ticker})")
st.write(f"상태: **{financial['status']}** · 마지막 로컬 수집: {financial['collected_at_utc']}")

period_rows: list[dict[str, Any]] = []
for period in financial["periods"]:
    row = {
        "기간": period["label"],
        "기준일": period["period_end"],
        "근거": period["accounting_basis"],
    }
    row.update({key: _format_value(value) for key, value in period["metrics"].items()})
    period_rows.append(row)
st.dataframe(pd.DataFrame(period_rows), hide_index=True, width="stretch")
st.caption("금액 단위: USD million. TTM은 명시된 연간+누계 증감 산식이며, 검증되지 않은 TTM EPS는 표시하지 않습니다.")

with st.expander("대차대조표·가이던스"):
    st.json({"balance_sheet": financial["balance_sheet"], "guidance": financial["guidance"]})

with st.expander("원문 출처·무결성"):
    provenance = pd.DataFrame(financial["documents"])[
        ["key", "published_at", "period_end", "distribution", "sha256", "url"]
    ]
    st.dataframe(provenance, hide_index=True, width="stretch")
    st.warning("원문 재배포 권한을 주장하지 않습니다. 원본은 개인 로컬 캐시에만 보관됩니다.")

market_path = DATA_DIR / "market" / f"{ticker}.csv"
market = load_market(ticker, _mtime(market_path))
st.subheader("시장·기술 지표")
technical = compute_technical_snapshot(market)
if technical.get("status") == "unavailable":
    st.warning("개인용 가격 API 키가 없어 시장 데이터와 EMA·RSI·52주 낙폭을 계산하지 않았습니다.")
else:
    st.json(technical)
    st.line_chart(market.set_index("date")["close"], height=260)

st.subheader("판단 게이트")
st.error("적정가·매수 신호: 보류")
st.write("공식 실적은 표시하지만, 시장 가격·point-in-time 전망·독립 검증 신호가 충족되지 않아 투자 판단을 생성하지 않습니다.")
