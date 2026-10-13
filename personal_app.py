from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from stocklens.personal.analysis import COMPANY_PROFILES, compute_personal_analysis


DATA_DIR = Path(os.environ.get("STOCKLENS_PERSONAL_DATA_DIR", "private_data"))


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    return value if isinstance(value, dict) else None


@st.cache_data(show_spinner=False)
def load_json(path_text: str, modified_ns: int) -> dict[str, Any] | None:
    del modified_ns
    return _load_json(Path(path_text))


@st.cache_data(show_spinner=False)
def load_market(path_text: str, modified_ns: int) -> pd.DataFrame:
    del modified_ns
    path = Path(path_text)
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _mtime(path: Path) -> int:
    return path.stat().st_mtime_ns if path.exists() else 0


def _format_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "미확보"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}" if not float(value).is_integer() else f"{value:,.0f}"
    return str(value)


def _pct(value: float | None) -> str:
    return "미확보" if value is None else f"{value:.1%}"


st.set_page_config(page_title="StockLens 200 개인 연구", page_icon="🔒", layout="wide")
st.title("🔒 StockLens 200 개인 연구")
st.caption("로컬 전용 · 원시 데이터는 Git에 저장되지 않음 · 연구 참고이며 투자 권고 아님")

status_path = DATA_DIR / "status.json"
status = load_json(str(status_path), _mtime(status_path))
if status is None:
    st.warning("수집 데이터가 없습니다. 먼저 `python scripts/personal_refresh.py`를 실행하세요.")
    st.stop()

st.info(
    f"공식 재무 검증 {status.get('financial_tickers_verified', 0)}종목 / "
    f"{status.get('financial_periods_verified', 0)}개 기간 · "
    f"시장 데이터 {status.get('market', {}).get('status', 'unavailable')}"
)

financial_dir = DATA_DIR / "normalized" / "financials"
available = [path.stem for path in sorted(financial_dir.glob("*.json"))]
if not available:
    st.error("정규화된 공식 재무자료가 없습니다.")
    st.stop()

ticker = st.selectbox("기업", available)
financial_path = financial_dir / f"{ticker}.json"
financial = load_json(str(financial_path), _mtime(financial_path))
if not financial:
    st.error("재무 파일을 읽을 수 없습니다.")
    st.stop()

market_path = DATA_DIR / "market" / f"{ticker}.csv"
manifest_path = DATA_DIR / "market" / f"{ticker}.manifest.json"
qqq_path = DATA_DIR / "market" / "QQQ.csv"
market = load_market(str(market_path), _mtime(market_path))
qqq = load_market(str(qqq_path), _mtime(qqq_path))
manifest = load_json(str(manifest_path), _mtime(manifest_path)) or {}

can_analyze = ticker in COMPANY_PROFILES and any(row.get("kind") == "ttm" for row in financial.get("periods", []))
analysis = compute_personal_analysis(financial, market, qqq, manifest) if can_analyze else None

st.header(f"{financial['company']} ({ticker})")
st.write(f"공식 재무 상태: **{financial['status']}** · 마지막 로컬 수집: {financial['collected_at_utc']}")
if ticker in COMPANY_PROFILES:
    st.subheader("1. 주요 사업")
    st.write(COMPANY_PROFILES[ticker]["business"])

st.subheader("2. 최신 검증 가격")
if analysis and analysis["market"]["status"] != "unavailable" and not market.empty:
    latest = market.iloc[-1]
    st.metric("EOD 종가", f"${float(latest['close']):,.2f}", help="Alpha Vantage 원시·비조정 종가")
    st.caption(
        f"기준일 {analysis['market']['price_as_of']} · 수집 {analysis['market']['collected_at_utc']} · "
        f"상태 {analysis['market']['status']} · {analysis['market']['price_policy']}"
    )
else:
    st.warning("개인 API 키로 검증된 가격이 없습니다. 가격·시가총액·PER·FCF yield를 계산하지 않습니다.")

st.subheader("3. 공식 실적과 TTM")
period_rows: list[dict[str, Any]] = []
for period in financial["periods"]:
    metrics = period["metrics"]
    period_rows.append({
        "기간": period["label"], "기준일": period["period_end"], "근거": period["accounting_basis"],
        "매출": _format_value(metrics.get("revenue")), "영업이익": _format_value(metrics.get("operating_income")),
        "순이익": _format_value(metrics.get("net_income")), "GAAP EPS": _format_value(metrics.get("diluted_eps")),
        "희석주식": _format_value(metrics.get("diluted_shares")), "CFO": _format_value(metrics.get("cfo")),
        "CAPEX": _format_value(metrics.get("capex")), "FCF": _format_value(metrics.get("fcf")),
    })
st.dataframe(pd.DataFrame(period_rows), hide_index=True, width="stretch")
st.caption("금액 단위: USD million. TTM = FY2025 + 9M FY2026 - 9M FY2025. 검증되지 않은 TTM EPS는 비워 둡니다.")

st.subheader("4. 성장·수익성·현금흐름")
if analysis:
    metrics = analysis["fundamental"]["metrics"]
    cards = st.columns(4)
    cards[0].metric("TTM/FY 매출 변화", _pct(metrics["ttm_revenue_change_vs_fy2025"]["value"]), help="전년동기 성장률이 아닌 rolling TTM 대 FY2025 변화")
    cards[1].metric("TTM 영업이익률", _pct(metrics["ttm_operating_margin"]["value"]))
    cards[2].metric("TTM FCF 마진", _pct(metrics["ttm_fcf_margin"]["value"]))
    net_cash = metrics["known_net_cash"]["value_usd_millions"]
    cards[3].metric("확인 범위 순현금", "미확보" if net_cash is None else f"${net_cash:,.0f}M")
    if metrics["known_net_cash"].get("note"):
        st.caption(metrics["known_net_cash"]["note"])
else:
    st.info("이 fixture/기업에는 공통 투자지표 계약이 적용되지 않습니다.")

st.subheader("5. 공식 가이던스")
st.json(financial.get("guidance", {"status": "unavailable"}), expanded=False)
st.caption("회사 공시의 GAAP/Non-GAAP 표기를 그대로 유지하며 서로 혼용하지 않습니다.")

st.subheader("6. 기술지표와 주가 추세")
if not analysis or analysis["technical"].get("status") == "unavailable":
    st.warning("가격 이력이 없어 EMA·RSI·52주 낙폭·거래량 추세·QQQ 상대강도를 계산하지 않았습니다.")
else:
    tech = analysis["technical"]
    st.json({
        "EMA20": tech.get("ema20"), "EMA50": tech.get("ema50"), "EMA200": tech.get("ema200"),
        "RSI14": tech.get("rsi14"), "52주 고점 대비": tech.get("drawdown_from_52w_high"),
        "거래량 추세": tech.get("volume_trend_20d_vs_prior_20d"),
        "QQQ 상대강도": analysis["relative_strength"], "부족 사유": tech.get("missing_reasons", []),
    }, expanded=False)
    st.line_chart(market.set_index("date")["close"], height=260)
    st.caption("원시 비조정 종가 기반 설명 지표입니다. 주식분할·배당 영향을 포함한 백테스트나 매수 신호가 아닙니다.")

st.subheader("7. 현재 계산 가능한 가치 참고 지표")
if analysis:
    st.json(analysis["fundamental"]["valuation_inputs"], expanded=False)
else:
    st.info("가치 참고 지표 계약을 적용할 수 없습니다.")

st.subheader("8. 중요한 위험과 왜곡 요소")
for risk in COMPANY_PROFILES.get(ticker, {}).get("risks", ["검증된 위험 분류가 없습니다."]):
    st.write(f"- {risk}")
if financial.get("earnings_quality"):
    st.json(financial["earnings_quality"], expanded=False)

st.subheader("9. 계산 불가능한 분석")
st.error("정식 적정가·목표가격·매수구간·통계적으로 검증된 매수 신호: 보류")
st.write("가격, point-in-time 주식 수, 일관된 TTM EPS, 전망 및 독립 검증 신호가 모두 충족되지 않았습니다.")

audit_path = DATA_DIR / "audit" / "financials" / f"{ticker}.json"
audit = load_json(str(audit_path), _mtime(audit_path))
with st.expander("원문 교차검증·출처·무결성"):
    if audit:
        st.write(f"독립 검사: **{audit['status']}** · {audit['checks_passed']}/{audit['checks_total']} 통과")
    provenance = pd.DataFrame(financial["documents"])[["key", "published_at", "period_end", "distribution", "sha256", "url"]]
    st.dataframe(provenance, hide_index=True, width="stretch")
    st.warning("원문 재배포 권한을 주장하지 않습니다. 원본과 시장 데이터는 개인 로컬 캐시에만 보관됩니다.")
