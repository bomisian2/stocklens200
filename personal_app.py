from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from stocklens.personal.analysis import COMPANY_PROFILES, compute_personal_analysis
from stocklens.personal.market_data import compute_technical_snapshot
from stocklens.personal.universe import PERSONAL_PILOT_UNIVERSE, pilot_tickers


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


def _money_millions(value: float | None) -> str:
    return "미확보" if value is None else f"${value:,.0f}M"


def _metric_value(metrics: dict[str, Any], key: str) -> Any:
    return metrics.get(key, {}).get("value")


def _number(value: float | None, *, prefix: str = "", suffix: str = "", decimals: int = 2) -> str:
    return "미확보" if value is None else f"{prefix}{value:,.{decimals}f}{suffix}"


st.set_page_config(page_title="StockLens 200 개인 연구", page_icon="🔒", layout="wide")
st.title("🔒 StockLens 200 개인 연구")
st.caption("로컬 전용 · 원시 데이터는 Git에 저장되지 않음 · 연구 참고이며 투자 권고 아님")

status_path = DATA_DIR / "status.json"
status = load_json(str(status_path), _mtime(status_path))
if status is None:
    st.warning("수집 데이터가 없습니다. 먼저 `python scripts/personal_refresh.py`를 실행하세요.")
    st.stop()

st.info(
    f"사용 가능한 공식 재무 스냅샷 "
    f"{status.get('financial_tickers_available', status.get('financial_tickers_verified', 0))}종목 / "
    f"{status.get('financial_periods_available', status.get('financial_periods_verified', 0))}개 기간 · "
    f"시장 데이터 {status.get('market', {}).get('status', 'unavailable')}"
)

market_results = {
    str(row.get("ticker", "")).upper(): row
    for row in status.get("market", {}).get("results", [])
}
market_summary_rows: list[dict[str, Any]] = []
for market_ticker in (*pilot_tickers(), "QQQ"):
    result = market_results.get(market_ticker, {})
    summary_path = DATA_DIR / "market" / f"{market_ticker}.csv"
    summary_frame = load_market(str(summary_path), _mtime(summary_path))
    technical = compute_technical_snapshot(summary_frame)
    diagnostic_code = result.get("diagnostic_code")
    reason = result.get("message")
    if result.get("status") == "blocked" and not diagnostic_code:
        diagnostic_code = "legacy_unclassified_response"
        reason = "기존 어댑터가 안전한 공급자 오류 분류를 저장하지 않음"
    market_summary_rows.append(
        {
            "종목": market_ticker,
            "수집 상태": result.get("status", "unavailable"),
            "기준일": result.get("as_of"),
            "행": result.get("rows", 0),
            "EMA20": technical.get("ema20"),
            "EMA50": technical.get("ema50"),
            "RSI14": technical.get("rsi14"),
            "EMA200": technical.get("ema200"),
            "52주 낙폭": technical.get("drawdown_from_52w_high"),
            "진단": diagnostic_code,
            "사유": reason,
        }
    )

st.subheader("개인용 시장 데이터 현황")
market_summary = pd.DataFrame(market_summary_rows)
st.dataframe(
    market_summary[["종목", "수집 상태", "기준일", "행"]],
    hide_index=True,
    width="stretch",
)
st.caption("실제 로컬 캐시만 표시합니다. 기술지표 계산 성공은 검증된 매수 신호를 뜻하지 않습니다.")

financial_dir = DATA_DIR / "normalized" / "financials"
financial_summary_rows: list[dict[str, Any]] = []
for registered_ticker, registry in PERSONAL_PILOT_UNIVERSE.items():
    registered_path = financial_dir / f"{registered_ticker}.json"
    registered = load_json(str(registered_path), _mtime(registered_path))
    periods = (registered or {}).get("periods", [])
    financial_summary_rows.append({
        "종목": registered_ticker,
        "기업": registry["company"],
        "재무 상태": (registered or {}).get("status", registry["financial_source_state"]),
        "재무기간": len(periods),
        "최신 재무일": max((row.get("period_end", "") for row in periods), default=None),
        "회계/통화": f"{registry['accounting_standard']} / {registry['currency']}",
        "보류 사유": registry.get("financial_source_reason"),
    })

st.subheader("10개 기업 공식 재무 연결")
st.dataframe(
    pd.DataFrame(financial_summary_rows)[["종목", "재무 상태", "재무기간", "최신 재무일", "회계/통화"]],
    hide_index=True,
    width="stretch",
)
st.caption("verified와 partial만 실제 로컬 원문·해시 검사를 통과한 자료입니다. blocked는 숫자를 생성하지 않습니다.")

existing_tickers = [path.stem for path in sorted(financial_dir.glob("*.json"))]
selection_options = list(pilot_tickers()) + [value for value in existing_tickers if value not in PERSONAL_PILOT_UNIVERSE]
default_ticker = "AMAT" if "AMAT" in existing_tickers else (existing_tickers[0] if existing_tickers else selection_options[0])
ticker = st.selectbox(
    "기업",
    selection_options,
    index=selection_options.index(default_ticker),
    format_func=lambda value: (
        f"{value} · {PERSONAL_PILOT_UNIVERSE[value]['company']}"
        if value in PERSONAL_PILOT_UNIVERSE else value
    ),
)
financial_path = financial_dir / f"{ticker}.json"
financial = load_json(str(financial_path), _mtime(financial_path))
if not financial:
    registry = PERSONAL_PILOT_UNIVERSE[ticker]
    st.header(f"{registry['company']} ({ticker})")
    st.subheader("A. 현재 상황")
    st.write(COMPANY_PROFILES[ticker]["business"])
    st.warning(
        f"공식 재무 상태: {registry['financial_source_state']} · "
        f"{registry.get('financial_source_reason', '검증된 공식 원문이 아직 없습니다.')}"
    )
    state_cards = st.columns(3)
    state_cards[0].metric("공식 재무기간", "0개")
    state_cards[1].metric("가격 이력", "미확보")
    state_cards[2].metric("운영 신호", "0개")
    st.info(f"회계 기준/통화: {registry['accounting_standard']} / {registry['currency']}")
    st.markdown("**지금 판단할 수 없는 항목**")
    st.write("- 실적 추세, 가치 참고 지표, 기술적 위치, 정식 적정가와 매수 신호")
    st.markdown("**필요한 다음 자료**")
    st.write("- 공식 연차·분기 원문과 게시시각, 검증된 개인용 가격 이력")
    st.markdown("**핵심 위험**")
    for risk in COMPANY_PROFILES[ticker]["risks"]:
        st.write(f"- {risk}")
    st.error("운영 판단: hold · 원문 미확보 상태에서 숫자나 매수 신호를 생성하지 않음")
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
st.caption(f"공식 재무 상태: {financial['status']} · 마지막 로컬 수집: {financial['collected_at_utc']}")
if ticker in COMPANY_PROFILES:
    st.subheader("A. 현재 상황")
    st.write(COMPANY_PROFILES[ticker]["business"])
    if analysis:
        st.info(analysis["practical_summary"]["one_line"])

if analysis and analysis["market"]["status"] != "unavailable" and not market.empty:
    latest = market.iloc[-1]
    tech = analysis["technical"]
    headline = st.columns(3)
    headline[0].metric("검증 EOD 종가", f"${float(latest['close']):,.2f}", help="Alpha Vantage 원시·비조정 종가")
    headline[1].metric("최근 5거래일", _pct(tech.get("return_5d")))
    headline[2].metric("최근 20거래일", _pct(tech.get("return_20d")))
    st.caption(
        f"기준일 {analysis['market']['price_as_of']} · 수집 {analysis['market']['collected_at_utc']} · "
        f"상태 {analysis['market']['status']} · {analysis['market']['price_policy']}"
    )
else:
    selected_market_result = market_results.get(ticker, {})
    diagnostic_code = selected_market_result.get("diagnostic_code") or (
        "legacy_unclassified_response" if selected_market_result.get("status") == "blocked" else "unavailable"
    )
    reason = selected_market_result.get("message") or "검증된 가격 파일이 없습니다."
    if diagnostic_code == "legacy_unclassified_response":
        reason = "기존 어댑터가 공급자 오류 유형을 저장하지 않아 원인을 확정할 수 없습니다."
    st.warning(
        f"가격 상태: {selected_market_result.get('status', 'unavailable')} · "
        f"진단: {diagnostic_code} · {reason}"
    )
    st.caption("가격·시가총액·PER·FCF yield를 계산하지 않습니다.")

if analysis:
    summary = analysis["practical_summary"]
    with st.container(border=True):
        st.markdown("**지금 확인된 강점**")
        for item in summary["strengths"]:
            st.write(f"- {item}")
    with st.container(border=True):
        st.markdown("**현재 가격에 반영된 기대**")
        st.write(summary["embedded_expectations"])
        st.caption(summary["price_comparison_rule"])
    with st.container(border=True):
        st.markdown("**지금 판단할 수 없는 항목**")
        for item in summary["unknowns"]:
            st.write(f"- {item}")
    with st.container(border=True):
        st.markdown("**다음 실적·가격 조건에서 확인할 것**")
        for item in summary["watch_conditions"]:
            st.write(f"- {item}")

st.subheader("B. 실적 흐름")
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
st.dataframe(
    pd.DataFrame(period_rows), hide_index=True, width="stretch",
    column_config={"매출": st.column_config.TextColumn(width="small"), "영업이익": st.column_config.TextColumn(width="small")},
)
st.caption(
    f"금액 단위: {financial.get('currency', 'USD')} million · 회계 기준: {financial.get('accounting_standard', 'US GAAP')} · "
    "기간별 note에 TTM 산식을 명시 · FCF = 공시 CFO - 공시 CAPEX 참고 지표 · 검증되지 않은 TTM EPS는 비워 둠"
)

if analysis:
    metrics = analysis["fundamental"]["metrics"]
    cards = st.columns(3)
    cards[0].metric("TTM/FY 매출 변화", _pct(metrics["ttm_revenue_change_vs_fy2025"]["value"]), help="전년동기 성장률이 아닌 rolling TTM 대 FY2025 변화")
    cards[1].metric("TTM 영업이익률", _pct(metrics["ttm_operating_margin"]["value"]))
    cards[2].metric("TTM FCF 마진", _pct(metrics["ttm_fcf_margin"]["value"]))
    net_cash = metrics["known_net_cash"]["value_usd_millions"]
    st.metric("확인 범위 순현금", _money_millions(net_cash))
    if metrics["known_net_cash"].get("note"):
        st.caption(metrics["known_net_cash"]["note"])
else:
    gap = financial.get("data_gap") or "FY와 TTM을 함께 검증하지 못해 공통 투자지표를 계산하지 않았습니다."
    st.info(gap)

with st.expander("공식 가이던스와 산식", expanded=True):
    guidance_analysis = analysis["fundamental"]["guidance_analysis"] if analysis else {}
    if guidance_analysis.get("rows"):
        st.metric("가이던스 기간", guidance_analysis.get("period", "미확보"))
        for row in guidance_analysis["rows"]:
            st.metric(row["label"], row["value"], help=row["basis"])
        comparison = guidance_analysis.get("comparison", {})
        if comparison.get("midpoint") is not None:
            st.write(
                f"{comparison['label']}: 중간값 **{_pct(comparison['midpoint'])}**"
                + (
                    f" (범위 {_pct(comparison.get('low'))}~{_pct(comparison.get('high'))})"
                    if comparison.get("low") is not None else ""
                )
            )
            st.caption(comparison["formula"])
        st.warning(guidance_analysis["accounting_warning"])
    else:
        st.write("공식 가이던스 미확보")

st.subheader("C. 가치평가 참고")
if analysis:
    references = analysis["valuation_references"]
    if references.get("status") == "research_only":
        st.warning("연구 참고 전용 · 정식 시가총액/PER/적정가/매수구간이 아닙니다.")
        value_cards = st.columns(3)
        value_cards[0].metric("주식수 대용치", f"{references['diluted_share_proxy_millions']:,.0f}M")
        value_cards[1].metric("대용 시가가치", _money_millions(references["proxy_equity_value_usd_millions"]))
        value_cards[2].metric("대용 TTM FCF yield", _pct(references["proxy_ttm_fcf_yield"]))
        st.metric(
            f"{references['fy_gaap_eps_period']} GAAP EPS 참고 배수",
            f"{references['fy_gaap_eps_reference_multiple']:.1f}배",
        )
        st.caption(
            f"주식수 기준: {references['share_basis_period']} ({references['share_basis_as_of']}) · "
            "해당 분기 희석 가중평균 주식수(기준일 현재 실제 발행주식 수 아님)"
        )
        st.error("대용 시가가치는 실제 기준일 발행주식 수로 계산한 확정 시가총액이 아닙니다.")
        with st.expander("연구용 계산식과 근거"):
            for label, formula in references.get("formulas", {}).items():
                st.write(f"- {label}: `{formula}`")
            st.write(f"- 주식 수 공시 접수: {references.get('share_source_published_at', '미확보')}")
            st.write(f"- 주식 수 원문: {references.get('share_source_url', '미확보')}")
        st.write(f"현재 가격의 내재 기대: {references['interpretation']}")
    else:
        st.info(references.get("reason", "연구 참고 지표를 계산할 수 없습니다."))
        if references.get("diluted_share_proxy_millions") is not None:
            st.caption(
                f"{references.get('share_basis_period')} 희석 가중평균 주식수 "
                f"{references['diluted_share_proxy_millions']:,.0f}M은 확보했지만 가격이 없어 대용 시가가치를 계산하지 않았습니다."
            )
    estimates = analysis["future_estimates"]
    st.caption(
        "미래 EPS 컨센서스: 미확보 · Alpha Vantage에 관련 엔드포인트는 문서화되어 있으나 "
        "종목별 실제 응답·이용 범위·재현성을 검증하지 않아 미래 PER을 계산하지 않음"
    )
else:
    st.info("가치 참고 지표 계약을 적용할 수 없습니다.")

st.subheader("D. 기술적 분석")
if not analysis or analysis["technical"].get("status") == "unavailable":
    st.warning("가격 이력이 없어 EMA·RSI·52주 낙폭·거래량 추세·QQQ 상대강도를 계산하지 않았습니다.")
else:
    tech = analysis["technical"]
    tech_cards = st.columns(3)
    tech_cards[0].metric("EMA20", _number(tech.get("ema20"), prefix="$"))
    tech_cards[1].metric("EMA50", _number(tech.get("ema50"), prefix="$"))
    tech_cards[2].metric("RSI14", _number(tech.get("rsi14"), decimals=1))
    chart_frame = market[["date", "close"]].copy()
    chart_frame["EMA20"] = pd.to_numeric(chart_frame["close"], errors="coerce").ewm(span=20, adjust=False).mean()
    chart_frame["EMA50"] = pd.to_numeric(chart_frame["close"], errors="coerce").ewm(span=50, adjust=False).mean()
    chart_frame = chart_frame.rename(columns={"close": "종가"}).set_index("date")
    st.line_chart(chart_frame[["종가", "EMA20", "EMA50"]], height=300)
    range20 = tech.get("range_20d") or {}
    relative63 = analysis["relative_strength"].get("relative_return_63d")
    detail_cards = st.columns(3)
    detail_cards[0].metric("20일 관측 저가", f"${range20['low']:,.2f}" if range20 else "미확보")
    detail_cards[1].metric("20일 관측 고가", f"${range20['high']:,.2f}" if range20 else "미확보")
    detail_cards[2].metric("63일 QQQ 대비", _pct(relative63))
    st.write(f"기술 상태: **{analysis['interpretation']['technical']['label']}** · {analysis['interpretation']['technical']['reason']}")
    st.caption(f"20일 가격구간은 지지·저항의 관측 참고치일 뿐 확정 가격선이 아닙니다. 관측 100일 최대낙폭 {_pct(tech.get('observed_period_max_drawdown'))}.")
    st.caption("원시 비조정 종가 기반 설명 지표입니다. 주식분할·배당 영향을 포함한 백테스트나 매수 신호가 아닙니다.")

st.subheader("E. 과거 통계")
if analysis:
    signal_research = analysis["signal_research"]
    st.write(
        f"가격 이력 **{signal_research['history_sessions']}거래일** · 독립 검증 통과 전략 "
        f"**{signal_research['independent_validated_strategies']}개** · 현재 운영 신호 "
        f"**{signal_research['active_operational_signals']}개**"
    )
    st.info(signal_research["reason"])
    st.caption("PR #3 전략 코드는 운영 앱에 연결하지 않았습니다. 거래비용·독립 검증·충분한 표본을 충족하기 전에는 매수 추천을 만들지 않습니다.")

st.subheader("F. 최종 해석")
if analysis:
    interpretation = analysis["interpretation"]
    verdicts = st.columns(4)
    verdicts[0].metric("사업", interpretation["business"]["label"])
    verdicts[1].metric("실적", interpretation["performance"]["label"])
    verdicts[2].metric("가치", interpretation["value"]["label"])
    verdicts[3].metric("기술", interpretation["technical"]["label"])
    st.success(interpretation["overall"])
st.markdown("**핵심 위험**")
for risk in COMPANY_PROFILES.get(ticker, {}).get("risks", ["검증된 위험 분류가 없습니다."]):
    st.write(f"- {risk}")
if financial.get("earnings_quality"):
    with st.expander("EPS·회계 왜곡 점검"):
        st.json(financial["earnings_quality"], expanded=True)

st.error("운영 판단: hold · 정식 적정가·목표가격·매수구간·통계적으로 검증된 매수 신호 없음")
st.write("point-in-time 주식 수, 일관된 TTM EPS, 검증된 전망·할인율, 충분한 조정가격 이력과 독립 검증 신호가 모두 충족되지 않았습니다.")

audit_path = DATA_DIR / "audit" / "financials" / f"{ticker}.json"
audit = load_json(str(audit_path), _mtime(audit_path))
with st.expander("원문 교차검증·출처·무결성"):
    if audit:
        st.write(f"독립 검사: **{audit['status']}** · {audit['checks_passed']}/{audit['checks_total']} 통과")
    provenance = pd.DataFrame(financial["documents"])[["key", "published_at", "period_end", "distribution", "sha256", "url"]]
    st.dataframe(provenance, hide_index=True, width="stretch")
    st.warning("원문 재배포 권한을 주장하지 않습니다. 원본과 시장 데이터는 개인 로컬 캐시에만 보관됩니다.")
