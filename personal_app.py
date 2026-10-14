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

qqq_path = DATA_DIR / "market" / "QQQ.csv"
comparison_qqq = load_market(str(qqq_path), _mtime(qqq_path))
comparison_results: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
for comparison_ticker in ("AMAT", "AVGO"):
    comparison_financial_path = DATA_DIR / "normalized" / "financials" / f"{comparison_ticker}.json"
    comparison_market_path = DATA_DIR / "market" / f"{comparison_ticker}.csv"
    comparison_manifest_path = DATA_DIR / "market" / f"{comparison_ticker}.manifest.json"
    comparison_financial = load_json(str(comparison_financial_path), _mtime(comparison_financial_path))
    comparison_market = load_market(str(comparison_market_path), _mtime(comparison_market_path))
    comparison_manifest = load_json(str(comparison_manifest_path), _mtime(comparison_manifest_path)) or {}
    if comparison_financial and not comparison_market.empty:
        comparison_results[comparison_ticker] = (
            comparison_financial,
            compute_personal_analysis(
                comparison_financial,
                comparison_market,
                comparison_qqq,
                comparison_manifest,
            ),
        )

if comparison_results:
    st.subheader("AMAT·AVGO 실제 투자 분석 비교")
    st.caption("동일한 공통 계약과 로컬 검증 캐시로 계산합니다. 대용 가치와 기술지표는 연구 참고이며 매수 신호가 아닙니다.")
    comparison_columns = st.columns(2)
    for comparison_column, comparison_ticker in zip(comparison_columns, ("AMAT", "AVGO"), strict=True):
        with comparison_column:
            with st.container(border=True):
                if comparison_ticker not in comparison_results:
                    st.markdown(f"**{comparison_ticker}**")
                    st.warning("공식 재무 또는 검증 가격이 없어 비교 분석을 계산하지 않았습니다.")
                    continue
                comparison_financial, comparison_analysis = comparison_results[comparison_ticker]
                comparison_ttm = next(
                    row for row in comparison_financial["periods"] if row.get("kind") == "ttm"
                )
                comparison_metrics = comparison_analysis["fundamental"]["metrics"]
                comparison_valuation = comparison_analysis["valuation_references"]
                comparison_technical = comparison_analysis["technical"]
                comparison_guidance = comparison_analysis["fundamental"]["guidance_analysis"]
                comparison_guidance_mid = comparison_guidance.get("comparison", {}).get("midpoint")
                st.markdown(f"### {comparison_ticker} · {comparison_financial['company']}")
                st.write(COMPANY_PROFILES[comparison_ticker]["business"])
                st.info(comparison_analysis["practical_summary"]["one_line"])
                st.metric(
                    "검증 EOD 종가",
                    f"${comparison_technical['latest_close']:,.2f}",
                    help=f"기준일 {comparison_analysis['market']['price_as_of']} · 원시 비조정 종가",
                )
                st.markdown(
                    f"**TTM 실적** · 매출 {_money_millions(comparison_ttm['metrics'].get('revenue'))} · "
                    f"영업이익 {_money_millions(comparison_ttm['metrics'].get('operating_income'))} · "
                    f"FCF {_money_millions(comparison_ttm['metrics'].get('fcf'))}"
                )
                st.markdown(
                    f"**수익성** · 영업마진 {_pct(_metric_value(comparison_metrics, 'ttm_operating_margin'))} · "
                    f"FCF 마진 {_pct(_metric_value(comparison_metrics, 'ttm_fcf_margin'))}"
                )
                st.markdown(
                    f"**공식 가이던스** · {comparison_guidance.get('period', '미확보')} · "
                    + (
                        f"매출 중간값은 직전 분기 대비 {_pct(comparison_guidance_mid)}"
                        if comparison_guidance_mid is not None
                        else "수치 비교 미확보"
                    )
                )
                st.markdown(
                    f"**기술** · EMA20 {_number(comparison_technical.get('ema20'), prefix='$')} · "
                    f"EMA50 {_number(comparison_technical.get('ema50'), prefix='$')} · "
                    f"RSI14 {_number(comparison_technical.get('rsi14'), decimals=1)} · "
                    f"QQQ 대비 63일 {_pct(comparison_analysis['relative_strength'].get('relative_return_63d'))}"
                )
                st.markdown(
                    f"**대용 가치** · P/FCF {_number(comparison_valuation.get('proxy_p_fcf'), suffix='배', decimals=1)} · "
                    f"FCF yield {_pct(comparison_valuation.get('proxy_ttm_fcf_yield'))}"
                )
                st.caption("희석 가중평균 주식 수 대용치이므로 확정 시가총액·정식 적정가가 아닙니다.")

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
estimates_path = DATA_DIR / "estimates" / "normalized" / f"{ticker}.json"
market = load_market(str(market_path), _mtime(market_path))
qqq = load_market(str(qqq_path), _mtime(qqq_path))
manifest = load_json(str(manifest_path), _mtime(manifest_path)) or {}
estimates_snapshot = load_json(str(estimates_path), _mtime(estimates_path))

can_analyze = ticker in COMPANY_PROFILES and any(row.get("kind") == "ttm" for row in financial.get("periods", []))
analysis = compute_personal_analysis(financial, market, qqq, manifest, estimates_snapshot) if can_analyze else None

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
    questions = analysis["investment_questions"]
    with st.container(border=True):
        st.markdown(f"**{questions['performance']['question']}**")
        st.write(questions["performance"]["answer"])
        st.caption(questions["performance"].get("warning", ""))
    with st.container(border=True):
        st.markdown(f"**{questions['growth_expectation']['question']}**")
        st.write(questions["growth_expectation"]["answer"])
        st.caption("연구 민감도이며 목표가격·성장 전망·매수 조건이 아닙니다.")
    with st.container(border=True):
        st.markdown(f"**{questions['technical']['question']}**")
        st.write(questions["technical"]["answer"])
    with st.container(border=True):
        st.markdown(f"**{questions['watch']['question']}**")
        for item in questions["watch"]["items"]:
            st.write(f"- {item}")
    with st.container(border=True):
        st.markdown(f"**{questions['unknowns']['question']}**")
        for item in questions["unknowns"]["items"]:
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

st.subheader("C. 가치 지도 · 연구 전용")
if analysis:
    references = analysis["valuation_references"]
    evidence_contract = analysis.get("valuation_evidence", {})
    evidence_axes = evidence_contract.get("axes", {})
    evidence_map = evidence_contract.get("map", {})
    st.warning(
        "정상 관심 범위와 정식 적정가는 아직 없습니다. 현재 화면은 시장 기대, DCF 스트레스 참고, "
        "확보된 품질 근거와 미확보 축을 분리해 보여줍니다."
    )
    with st.container(horizontal=True):
        for axis_id, label in (
            ("self_history", "자기 역사"),
            ("peer_relative", "피어 상대가치"),
            ("intrinsic_reverse", "내재가치·역산 기대"),
            ("quality_premium", "품질 프리미엄 증거"),
        ):
            axis = evidence_axes.get(axis_id, {})
            st.metric(
                label,
                axis.get("status", "unavailable"),
                border=True,
                help=axis.get("calculation_basis"),
            )
    st.info(evidence_map.get("reason", "가치 지도 근거가 미확보되었습니다."))
    map_rows = {
        "정상 관심 범위": evidence_map.get("normal_interest_range"),
        "높은 기대 구간": evidence_map.get("high_expectation_range"),
        "검증된 위기 스트레스 범위": evidence_map.get("validated_crisis_stress_range"),
    }
    with st.container(horizontal=True):
        for label, value in map_rows.items():
            st.metric(label, "미산출" if value is None else str(value), border=True)

    quality_axis = evidence_axes.get("quality_premium", {})
    quality_rows = quality_axis.get("evidence") or []
    with st.expander("품질 프리미엄 증거 · 점수로 합산하지 않음", expanded=True):
        for row in quality_rows:
            value = row.get("value", row.get("value_usd_millions"))
            if isinstance(value, float) and row.get("id") in {"revenue_change", "operating_margin", "fcf_margin"}:
                value_text = _pct(value)
            elif row.get("id") == "net_cash_or_debt" and value is not None:
                value_text = _money_millions(float(value))
            elif value is None:
                value_text = "미확보"
            elif isinstance(value, (dict, list)):
                value_text = "근거 연결됨"
            else:
                value_text = str(value)
            st.write(f"- {row['label']}: **{row.get('status', 'unavailable')}** · {value_text}")
            if row.get("note"):
                st.caption(row["note"])

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
        st.metric("대용 P/FCF", _number(references.get("proxy_p_fcf"), suffix="배", decimals=1))
        st.error("대용 시가가치는 실제 기준일 발행주식 수로 계산한 확정 시가총액이 아닙니다.")
        point_shares = references.get("point_in_time_share_evidence")
        point_reference = references.get("point_in_time_equity_reference")
        if point_shares:
            st.markdown("**공식 기준일 발행주식 수 증거**")
            share_cards = st.columns(3)
            share_cards[0].metric("발행 보통주", f"{point_shares['value_millions']:,.3f}M")
            share_cards[1].metric("주식 수 기준일", point_shares["as_of"])
            share_cards[2].metric("증거 상태", point_shares["source_integrity"])
            st.caption(
                f"종류: {point_shares['kind']} · 공시: {point_shares['published_at']} · "
                f"출처: {point_shares['source_label']}"
            )
            st.caption(
                f"접수번호: {point_shares.get('accession_number', '미확보')} · "
                f"공시일: {point_shares.get('filing_date', '미확보')} · "
                f"클래스: {point_shares.get('classes', '미확보')}"
            )
            if point_reference and point_reference.get("status") == "time_mismatched_research_only":
                point_cards = st.columns(2)
                point_cards[0].metric(
                    "시점 불일치 주식가치 참고",
                    _money_millions(point_reference.get("equity_value_usd_millions")),
                )
                point_cards[1].metric(
                    "시점 불일치 TTM FCF yield",
                    _pct(point_reference.get("ttm_fcf_yield")),
                )
                st.warning(
                    f"가격일 {point_reference['price_as_of']}과 주식 수 기준일 "
                    f"{point_reference['shares_as_of']}이 {point_reference['date_gap_days']}일 차이입니다. "
                    "따라서 확정 시가총액·정식 FCF yield로 사용하지 않습니다."
                )
            elif point_reference and point_reference.get("formal_market_cap_eligible"):
                st.success("가격일과 주식 수 기준일이 일치하는 검증 참고치입니다.")
        conditional = references.get("conditional_research_valuation", {})
        if conditional.get("status") == "research_only":
            st.markdown("**현재 시장 기대 · 운영 자격 false**")
            implied_range = conditional.get("implied_growth_range")
            if implied_range:
                st.info(
                    "현재 가격이 요구하는 5년 CFO-CAPEX 대용 성장률은 연구 할인·터미널 격자에서 "
                    f"{_pct(implied_range.get('low'))}~{_pct(implied_range.get('high'))}입니다. "
                    "높은 수치 자체를 비현실적 또는 고평가로 자동 판정하지 않습니다."
                )
            st.warning(
                "아래 값은 DCF 스트레스 참고이며 외부 전망치나 정상 적정가가 아닙니다. "
                "목표가격·매수구간으로 사용하지 않으며 "
                "어느 행도 공식 base case로 선택하지 않습니다."
            )
            st.markdown(f"**{conditional.get('range_label', 'DCF 스트레스 참고 범위')}**")
            scenario_rows = conditional.get("scenario_values", [])
            with st.container(horizontal=True):
                for scenario in scenario_rows:
                    st.metric(
                        scenario["label"],
                        f"${scenario['research_per_issued_share']:,.2f}",
                        border=True,
                        help=(
                            f"5년 FCF CAGR {scenario['growth_rate']:.1%} · 할인율 "
                            f"{scenario['discount_rate']:.1%} · 터미널 성장률 {scenario['terminal_growth']:.1%}"
                        ),
                    )
            outer = conditional.get("research_per_share_outer_range", {})
            st.caption(
                f"DCF 스트레스 격자 외곽 참고 ${outer.get('low', 0):,.2f}~${outer.get('high', 0):,.2f} · "
                f"현재가 ${conditional['price']:,.2f} ({conditional.get('price_as_of', '미확보')}) · "
                "정상 관심 범위·공식 중간 적정가 없음"
            )
            implied_rows = conditional.get("implied_growth_sensitivity", [])
            if implied_rows:
                implied_frame = pd.DataFrame(
                    {
                        "조건": [row["label"] for row in implied_rows],
                        "할인율": [row["discount_rate"] for row in implied_rows],
                        "터미널 성장률": [row["terminal_growth"] for row in implied_rows],
                        "현재가 내재 5년 FCF CAGR": [row.get("implied_fcf_growth") for row in implied_rows],
                        "터미널가치 비중": [row.get("terminal_value_share") for row in implied_rows],
                    }
                )
                st.dataframe(
                    implied_frame,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "할인율": st.column_config.NumberColumn(format="percent"),
                        "터미널 성장률": st.column_config.NumberColumn(format="percent"),
                        "현재가 내재 5년 FCF CAGR": st.column_config.NumberColumn(format="percent"),
                        "터미널가치 비중": st.column_config.NumberColumn(format="percent"),
                    },
                )
                st.caption(
                    "현재가 내재 성장률은 선택한 연구 할인율·터미널 성장률에 종속되며 미래 전망이 아닙니다. "
                    f"가격일 {conditional.get('price_as_of', '미확보')} · 주식 수 기준일 "
                    f"{conditional.get('shares_as_of', '미확보')} · 차이 {conditional.get('date_gap_days', '미확보')}일"
                )
            drivers = conditional.get("driver_sensitivity", [])
            if drivers:
                st.write("가치 결과 영향도 순위")
                for index, driver in enumerate(drivers, start=1):
                    st.write(
                        f"{index}. {driver['driver']} · 연구 격자 내 주당 영향 "
                        f"${abs(driver['per_share_impact']):,.2f}"
                    )
            with st.expander("조건부 연구 평가의 가정·한계"):
                policy = conditional["assumption_policy"]
                st.write(f"- 모델: `{conditional['model_version']}`")
                st.write(f"- 재사용 엔진: `{conditional['reused_engine']}`")
                st.write(f"- 현금흐름: {conditional['cash_flow_definition']}")
                st.write(f"- 가정 규칙: {policy['selection_rule']}")
                st.write(
                    f"- 가격일과 주식 수 기준일 차이: {conditional.get('date_gap_days', '미확보')}일"
                )
                for limitation in conditional["limitations"]:
                    st.write(f"- {limitation}")
        with st.expander("연구용 계산식과 근거"):
            for label, formula in references.get("formulas", {}).items():
                st.write(f"- {label}: `{formula}`")
            st.write(f"- 주식 수 공시 접수: {references.get('share_source_published_at', '미확보')}")
            st.write(f"- 주식 수 원문: {references.get('share_source_url', '미확보')}")
        sensitivity = references.get("fcf_growth_sensitivity", {})
        if sensitivity.get("rows"):
            with st.expander("5년 FCF 성장 민감도 · 연구 전용"):
                sensitivity_frame = pd.DataFrame(
                    {
                        "FCF CAGR": [row["fcf_cagr"] for row in sensitivity["rows"]],
                        "5년 후 FCF ($M)": [row["year5_fcf_usd_millions"] for row in sensitivity["rows"]],
                        "5년 후 대용 FCF yield": [row["year5_proxy_fcf_yield"] for row in sensitivity["rows"]],
                        "5년 후 대용 P/FCF": [row["year5_proxy_p_fcf"] for row in sensitivity["rows"]],
                    }
                )
                st.dataframe(
                    sensitivity_frame,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "FCF CAGR": st.column_config.NumberColumn(format="percent"),
                        "5년 후 FCF ($M)": st.column_config.NumberColumn(format="$%.0fM"),
                        "5년 후 대용 FCF yield": st.column_config.NumberColumn(format="percent"),
                        "5년 후 대용 P/FCF": st.column_config.NumberColumn(format="%.1f배"),
                    },
                )
                st.caption(sensitivity["formula"])
                for assumption in sensitivity["assumptions"]:
                    st.write(f"- {assumption}")
                st.warning("시나리오 성장률은 전망치가 아니라 민감도 격자입니다. 가격·주식 수·자본구조가 고정된다는 강한 가정 때문에 정식 가치평가에 사용할 수 없습니다.")
        st.write(f"현재 가격의 내재 기대: {references['interpretation']}")
        reverse_dcf = references.get("reverse_dcf", {})
        with st.expander("현재 가격 성장 기대 역산 준비 상태"):
            st.warning("역산 DCF 입력 미충족 · 성장률·할인율·터미널가치를 임의 생성하지 않습니다.")
            st.write(f"재사용 계약: `{reverse_dcf.get('reused_contract', '미확보')}`")
            for item in reverse_dcf.get("inputs", []):
                value = item.get("value")
                value_text = "미확보" if value is None else f"{value:,.6g}"
                st.write(f"- {item['label']}: {item['status']} · {value_text}")
            st.caption(reverse_dcf.get("reason", "역산 가치평가 입력 상태를 확인할 수 없습니다."))
    else:
        st.info(references.get("reason", "연구 참고 지표를 계산할 수 없습니다."))
        if references.get("diluted_share_proxy_millions") is not None:
            st.caption(
                f"{references.get('share_basis_period')} 희석 가중평균 주식수 "
                f"{references['diluted_share_proxy_millions']:,.0f}M은 확보했지만 가격이 없어 대용 시가가치를 계산하지 않았습니다."
            )
    estimates = analysis["future_estimates"]
    if estimates.get("status") == "research_only":
        st.warning("애널리스트 컨센서스 연구 참고 전용 · 회사 공식 가이던스와 혼합하지 않음")
        estimate_cards = st.columns(3)
        estimate_cards[0].metric("컨센서스 기간 수", estimates.get("period_count", 0))
        next_annual = estimates.get("next_annual") or {}
        estimate_cards[1].metric("다음 연간 기준", next_annual.get("fiscal_date_ending", "미확보"))
        estimate_cards[2].metric(
            "미래 PER 참고",
            f"{estimates['forward_per_reference']:.1f}배" if estimates.get("forward_per_reference") is not None else "미확보",
        )
        st.caption(
            f"수집시각 {estimates.get('collected_at_utc', '미확보')} · 공급자 스냅샷 시각 "
            f"{estimates.get('provider_snapshot_time_status', '미확보')} · 운영 자격 false"
        )
    else:
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
