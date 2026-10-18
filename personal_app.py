from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from stocklens.personal.analysis import COMPANY_PROFILES, compute_personal_analysis
from stocklens.personal.decision_evidence import load_and_validate_evidence_audit
from stocklens.personal.decision_model import (
    APPROVED_LOCAL_INPUT_SHA256,
    SUPPORTED_TICKERS as DECISION_SUPPORTED_TICKERS,
    DecisionModelError,
    backtest_pit_per_anchor,
    build_decision_analysis_snapshot,
    load_decision_config,
    load_decision_inputs,
)
from stocklens.personal.market_data import compute_technical_snapshot
from stocklens.personal.nvda_research import (
    NvdaResearchError,
    load_research_artifacts,
)
from stocklens.personal.market_regime import (
    MarketRegimeError,
    build_market_regime,
    build_qqq_proxy_observations,
    build_regime_adjusted_candidate,
    explain_market_regime_ko,
    load_market_regime_snapshot,
)
from stocklens.personal.universe import PERSONAL_PILOT_UNIVERSE, pilot_tickers
from stocklens.personal.top10_bridge import (
    APPROVED_CONSENSUS_SHA256,
    APPROVED_MACRO_SHA256,
    Top10BridgeError,
    build_forward_consensus_candidate,
    load_forward_consensus_snapshot,
    load_market_environment_proxy,
)
from stocklens.personal.v23_adapter import (
    APPROVED_LEDGER_SHA256,
    SUPPORTED_TICKERS as V23_SUPPORTED_TICKERS,
    V23LedgerError,
    build_self_history_market_premium,
    compare_v23_with_stocklens,
    load_v23_public_ledger,
)


DATA_DIR = Path(os.environ.get("STOCKLENS_PERSONAL_DATA_DIR", "private_data"))
V23_LEDGER_PATH = os.environ.get("STOCKLENS_V23_LEDGER_PATH")
V23_LEDGER_SHA256 = os.environ.get("STOCKLENS_V23_LEDGER_SHA256", APPROVED_LEDGER_SHA256)
REGIME_SNAPSHOT_PATH = os.environ.get("STOCKLENS_REGIME_SNAPSHOT_PATH")
TOP10_DATA_DIR = Path(
    os.environ.get(
        "STOCKLENS_TOP10_DATA_DIR",
        str(Path(__file__).resolve().parent.parent / "나스닥 Top10 대시보드" / "data"),
    )
)
TOP10_MACRO_PATH = Path(
    os.environ.get("STOCKLENS_TOP10_MACRO_PATH", str(TOP10_DATA_DIR / "macro-snapshot.json"))
)
TOP10_CONSENSUS_PATH = Path(
    os.environ.get("STOCKLENS_TOP10_CONSENSUS_PATH", str(TOP10_DATA_DIR / "top100-consensus.json"))
)
TOP10_MACRO_SHA256 = os.environ.get("STOCKLENS_TOP10_MACRO_SHA256", APPROVED_MACRO_SHA256)
TOP10_CONSENSUS_SHA256 = os.environ.get(
    "STOCKLENS_TOP10_CONSENSUS_SHA256", APPROVED_CONSENSUS_SHA256
)
SAME_DATE_INPUT_PATH = Path(
    os.environ.get(
        "STOCKLENS_SAME_DATE_INPUT_PATH",
        str(DATA_DIR / "decision" / "same-date-inputs-v1.json"),
    )
)
SAME_DATE_INPUT_SHA256 = os.environ.get(
    "STOCKLENS_SAME_DATE_INPUT_SHA256", APPROVED_LOCAL_INPUT_SHA256
)
SAME_DATE_SOURCE_ROOT = Path(
    os.environ.get("STOCKLENS_SAME_DATE_SOURCE_ROOT", str(SAME_DATE_INPUT_PATH.parent))
)
SAME_DATE_AUDIT_PATH = Path(
    os.environ.get(
        "STOCKLENS_DECISION_AUDIT_PATH",
        str(DATA_DIR / "decision" / "evidence-audit-v1.json"),
    )
)
SAME_DATE_AUDIT_SHA256 = os.environ.get("STOCKLENS_DECISION_AUDIT_SHA256")
SAME_DATE_LEGACY_INPUT_PATH = Path(
    os.environ.get(
        "STOCKLENS_SAME_DATE_LEGACY_INPUT_PATH",
        str(DATA_DIR / "decision" / "same-date-inputs-v1.json"),
    )
)
DECISION_CONFIG_PATH = Path(
    os.environ.get(
        "STOCKLENS_DECISION_CONFIG_PATH",
        str(Path(__file__).resolve().parent / "config" / "decision-model-v1.json"),
    )
)
TOP10_PRICE_HISTORY_PATH = Path(
    os.environ.get("STOCKLENS_TOP10_PRICE_HISTORY_PATH", str(TOP10_DATA_DIR / "price-history.json"))
)
NVDA_P51_ADJUDICATION_PATH = Path(
    os.environ.get(
        "STOCKLENS_NVDA_P51_ADJUDICATION_PATH",
        str(DATA_DIR / "decision" / "nvda-estimates-partial-adjudication-p5.1.json"),
    )
)
NVDA_P51_READ_MODEL_PATH = Path(
    os.environ.get(
        "STOCKLENS_NVDA_P51_READ_MODEL_PATH",
        str(DATA_DIR / "normalized" / "research" / "NVDA-consensus-p5.1.json"),
    )
)
NVDA_P51_DECISION_PATH = Path(
    os.environ.get(
        "STOCKLENS_NVDA_P51_DECISION_PATH",
        str(DATA_DIR / "decision" / "nvda-provisional-research-decision-p5.1.json"),
    )
)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    return value if isinstance(value, dict) else None


@st.cache_data(show_spinner=False, max_entries=256)
def load_json(path_text: str, modified_ns: int) -> dict[str, Any] | None:
    del modified_ns
    return _load_json(Path(path_text))


@st.cache_data(show_spinner=False, max_entries=64)
def load_market(path_text: str, modified_ns: int) -> pd.DataFrame:
    del modified_ns
    path = Path(path_text)
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


@st.cache_data(show_spinner=False, max_entries=8)
def load_v23_ledger(path_text: str, modified_ns: int, expected_sha256: str) -> dict[str, Any]:
    del modified_ns
    return load_v23_public_ledger(path_text, expected_sha256=expected_sha256)


def _evidence_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            (str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns)
            for path in root.rglob("*.json")
            if path.is_file()
        )
    )


@st.cache_data(show_spinner=False, max_entries=8)
def load_same_date_inputs(
    path_text: str,
    modified_ns: int,
    expected_sha256: str | None,
    source_root_text: str,
    source_signature: tuple[tuple[str, int, int], ...],
) -> dict[str, Any]:
    del modified_ns, source_signature
    return load_decision_inputs(
        path_text,
        expected_sha256=expected_sha256,
        source_root=source_root_text,
    )


@st.cache_data(show_spinner=False, max_entries=8)
def load_nvda_p51_artifacts(
    adjudication_path: str,
    adjudication_mtime: int,
    read_model_path: str,
    read_model_mtime: int,
    decision_path: str,
    decision_mtime: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    del adjudication_mtime, read_model_mtime, decision_mtime
    return load_research_artifacts(
        adjudication_path=adjudication_path,
        read_model_path=read_model_path,
        decision_path=decision_path,
    )


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


def _render_nvda_p51_research(
    adjudication: dict[str, Any],
    read_model: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    st.subheader("NVDA P5.1 부분유효 컨센서스 · 잠정 연구 판단")
    st.warning(
        "잠정 연구 판단(provisional_research) · operational_eligible=false. "
        "자동 주문·운영 신호·정식 적정가에는 전달되지 않습니다."
    )
    counts = adjudication["row_counts"]
    with st.container(horizontal=True):
        st.metric("전체 공급자 행", f"{counts['total']}건", border=True)
        st.metric("정상 행", f"{counts['valid']}건", border=True)
        st.metric("격리 행", f"{counts['quarantined']}건", border=True)
        st.metric("미래 연구 입력", f"{counts['future_valid']}건", border=True)
    st.caption(
        f"수집시각 {read_model['available_at']} · data_quality_status={read_model['data_quality_status']} · "
        f"accounting_basis={read_model['accounting_basis']} · 과거 PIT 사용 금지 · 원문 수정 없음"
    )
    for row in adjudication.get("quarantined_records", []):
        st.info(
            f"격리: 원문 {row['position']}번째 행 · {row.get('date')} · {row.get('horizon')} · "
            f"사유 {row.get('reason')} · canonical SHA-256 {row['canonical_sha256'][:16]}…"
        )

    st.markdown("#### 현재 시점 이후 정상 컨센서스 4건")
    future_rows = []
    for row in read_model.get("forward_records", []):
        eps = row["eps"]
        revenue = row["revenue"]
        future_rows.append({
            "기간말": row["period_date"],
            "구분": row["horizon"],
            "EPS 하단": eps["low"],
            "EPS 평균": eps["average"],
            "EPS 상단": eps["high"],
            "EPS 분석가": eps["analyst_count"],
            "매출 하단": revenue["low"],
            "매출 평균": revenue["average"],
            "매출 상단": revenue["high"],
            "매출 분석가": revenue["analyst_count"],
            "30일 상향/하향": f"{eps['revision_up_30d']}/{eps['revision_down_30d']}",
            "available_at": row["available_at"],
        })
    st.dataframe(
        pd.DataFrame(future_rows),
        hide_index=True,
        width="stretch",
        column_config={
            "EPS 하단": st.column_config.NumberColumn(format="$%.4f"),
            "EPS 평균": st.column_config.NumberColumn(format="$%.4f"),
            "EPS 상단": st.column_config.NumberColumn(format="$%.4f"),
            "매출 하단": st.column_config.NumberColumn(format="$%.0f"),
            "매출 평균": st.column_config.NumberColumn(format="$%.0f"),
            "매출 상단": st.column_config.NumberColumn(format="$%.0f"),
        },
    )

    price = decision["current_price"]
    selected = decision["selected_forward_period"]
    with st.container(horizontal=True):
        st.metric(
            "P5.1 현재가",
            f"${price['value']:,.2f}",
            f"{price['as_of']} · 비조정",
            border=True,
        )
        st.metric(
            "현재 implied forward multiple",
            f"{decision['current_implied_forward_multiple']:.2f}배",
            f"{selected['period_date']} {selected['horizon']}",
            border=True,
        )
        st.metric(
            "잠정 연구 기준값",
            f"${decision['provisional_research_base_value']:,.2f}",
            f"국면 조정 {decision['regime']['adjustment']:+.1%}",
            border=True,
        )
        st.metric("잠정 연구 행동", decision["research_action"], "신뢰도 낮음", border=True)

    action_text = str(decision["research_action_text"]).replace("$", r"\$")
    st.markdown(f"**행동:** {action_text}")
    buy_prices = decision["provisional_research_buy_prices"]
    st.success(
        f"잠정 연구 분할구간: \\${buy_prices[0]:,.2f} / \\${buy_prices[1]:,.2f} / \\${buy_prices[2]:,.2f}"
    )
    st.error(
        f"판단 무효 조건: {selected['period_date']} {selected['horizon']} 평균 EPS가 "
        f"${decision['invalidation_condition']['eps_average_below']:.4f} 아래로 낮아지면 재계산"
    )

    sensitivity = decision["provisional_research_values"]
    st.dataframe(
        pd.DataFrame(
            [
                {"EPS 시나리오": "하단", "EPS": selected["eps"]["low"], "잠정 연구값": sensitivity["low"]},
                {"EPS 시나리오": "평균", "EPS": selected["eps"]["average"], "잠정 연구값": sensitivity["base"]},
                {"EPS 시나리오": "상단", "EPS": selected["eps"]["high"], "잠정 연구값": sensitivity["high"]},
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "EPS": st.column_config.NumberColumn(format="$%.4f"),
            "잠정 연구값": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    with st.expander("배수·가중치·국면 조정 계산", expanded=True):
        st.write(
            f"- 연구 배수 프록시(독립 배수 아님): {decision['multiple_proxy']['value']:.2f}배 · "
            f"표본 {decision['multiple_proxy']['sample_count']}건 "
            f"({decision['multiple_proxy']['period_start']}~{decision['multiple_proxy']['period_end']})"
        )
        st.write(
            f"- 국면 전 목표 PER: {decision['pre_regime_target_per']:.2f}배 → "
            f"국면 후 {decision['target_per_after_regime']:.2f}배"
        )
        st.write(
            f"- 국면 전 기준값: ${decision['pre_regime_base_value']:,.2f} → "
            f"국면 후 ${decision['provisional_research_base_value']:,.2f}"
        )
        for axis, state in decision["axis_availability"].items():
            normalized = decision["weight_trace"]["normalized_available_only"].get(axis)
            weight = "제외" if normalized is None else f"{normalized:.0%}"
            st.write(f"- {axis}: {weight} · {state['reason']}")
        for trace in decision["regime"]["trace"]:
            st.write(f"- {trace}")
        st.warning(decision["circularity_notice"])
        st.warning(decision["accounting_basis_impact"])
        st.caption(decision["calculation_basis"])
    st.caption(
        "운영 판단: 미생성 · accounting basis와 독립 Forward PER 원장이 검증되기 전까지 "
        "operational_decision=None / operational_eligible=false"
    )


def _metric_value(metrics: dict[str, Any], key: str) -> Any:
    return metrics.get(key, {}).get("value")


def _render_same_date_decision(decision: dict[str, Any], backtest: dict[str, Any]) -> None:
    st.subheader("Same-Date Investment Decision Model v1")
    if decision.get("status") == "blocked":
        st.error(
            "독립 QA 차단 상태 · 행동 판단 비활성 · operational_eligible=false. "
            "TTM PER를 Forward PER로 표시하거나 unverified Street EPS와 결합하지 않습니다."
        )
        with st.container(horizontal=True):
            st.metric("동일시점 현재가", f"${decision['current_price']:,.2f}", border=True)
            st.metric("기준 적정가", "미산출", border=True)
            st.metric("3회 분할매수", "비활성", border=True)
            st.metric("현재 행동", "비활성", border=True)
        st.caption(
            f"판단시각 {decision['decision_time']} · 시장세션/가격 {decision['market_session']} · "
            f"컨센서스 공개 {decision['consensus_available_at']} · 시장환경 {decision['regime_as_of']}"
        )
        arithmetic = decision["candidate_arithmetic"]
        st.markdown("#### 산술 입력 · 행동 계산에는 미사용")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "항목": "Street EPS low/base/high",
                        "값": (
                            f"{arithmetic['street_eps']['low']:.2f} / "
                            f"{arithmetic['street_eps']['base']:.2f} / "
                            f"{arithmetic['street_eps']['high']:.2f}"
                        ),
                        "basis": arithmetic["street_eps"]["basis"],
                        "행동 계산 사용": "아니오",
                    },
                    {
                        "항목": "레거시 역사 P35",
                        "값": (
                            f"{arithmetic['historical_ttm_per_p35']:.2f}배"
                            if arithmetic.get("historical_ttm_per_p35") is not None
                            else "미확보"
                        ),
                        "basis": arithmetic["historical_multiple_basis"],
                        "행동 계산 사용": "아니오",
                    },
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        for blocker in decision["blockers"]:
            st.write(f"- {blocker}")
        st.warning(decision["action_text"])
        st.markdown("#### 백테스트 상태")
        st.write(f"- 상태: `{backtest['status']}`")
        st.write(f"- 미래정보 사용 판정: `{backtest['future_information_used']}`")
        st.write(f"- 사유: {backtest.get('reason', backtest.get('scope', '미검증'))}")
        passed = sum(bool(row["passed"]) for row in decision["promotion_gates"])
        st.markdown(f"#### 운영 승격 게이트 · {passed}/{len(decision['promotion_gates'])} 통과")
        st.dataframe(
            pd.DataFrame(
                [
                    {"조건": row["id"], "통과": "통과" if row["passed"] else "미통과", "근거": row["reason"]}
                    for row in decision["promotion_gates"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        return
    st.warning(
        "decision_status=candidate · operational_eligible=false. 행동 문구는 동일 시점 연구 판단이며, "
        "운영 승격 게이트를 통과하기 전 자동 주문·알림·공개 신호에는 연결하지 않습니다."
    )
    st.markdown(f"### {decision['ticker']} · {decision['action_text']}")
    st.write(
        f"**${decision['buy_prices'][0]:,.2f} / ${decision['buy_prices'][1]:,.2f} / "
        f"${decision['buy_prices'][2]:,.2f}에서 3회 분할매수**"
    )
    st.write(f"**${decision['reduction_review_price']:,.2f} 이상에서는 신규 매수 중단**")
    with st.container(horizontal=True):
        st.metric("동일시점 현재가", f"${decision['current_price']:,.2f}", border=True)
        st.metric("기준 적정가 · candidate", f"${decision['base_fair_value']:,.2f}", border=True)
        st.metric(
            "현재 가격 부담도",
            decision["price_burden"],
            f"기준값 대비 {decision['valuation_gap']:+.1%}",
            border=True,
        )
        st.metric("목표 Forward PER", f"{decision['target_per']:.2f}배", border=True)
        st.metric("현재 행동", decision["action"], border=True)
        st.metric(
            "판단 신뢰도",
            f"{decision['confidence']['label']} · {decision['confidence']['score']}/100",
            border=True,
        )
    st.caption(
        f"판단시각 {decision['decision_time']} · 시장세션/가격 {decision['market_session']} · "
        f"컨센서스 공개 {decision['consensus_available_at']} · 시장환경 {decision['regime_as_of']} · "
        f"가격 지연 {decision['price_lag_calendar_days']}일"
    )
    st.caption(
        f"계산 버전 {decision['calculation_version']} · SHA-256 {decision['source_hash'][:16]}… · "
        f"EPS basis {decision['eps_basis']}"
    )

    trace = decision["calculation_trace"]
    trace_rows = [
        {
            "축": "자체 역사 Forward PER",
            "가중치": trace["historical_forward_per"]["weight"],
            "입력 PER": trace["historical_forward_per"]["per"],
            "기여 PER": trace["historical_forward_per"]["weighted_contribution"],
            "근거": trace["historical_forward_per"]["percentile"],
        },
        {
            "축": "EPS 성장·추정치 변화",
            "가중치": trace["growth_revision"]["weight"],
            "입력 PER": trace["growth_revision"]["per"],
            "기여 PER": trace["growth_revision"]["weighted_contribution"],
            "근거": (
                f"다음 EPS 성장 {trace['growth_revision']['raw_next_year_eps_growth']:.1%} · "
                f"모델 반영 상한 {trace['growth_revision']['bounded_growth']:.1%}"
            ),
        },
        {
            "축": "시장·섹터 프리미엄",
            "가중치": trace["market_sector_premium"]["weight"],
            "입력 PER": trace["market_sector_premium"]["per"],
            "기여 PER": trace["market_sector_premium"]["weighted_contribution"],
            "근거": f"독립 동일시점 원장 · {trace['market_sector_premium']['source']}",
        },
    ]
    st.dataframe(
        pd.DataFrame(trace_rows),
        hide_index=True,
        width="stretch",
        column_config={
            "가중치": st.column_config.NumberColumn(format="percent"),
            "입력 PER": st.column_config.NumberColumn(format="%.2f×"),
            "기여 PER": st.column_config.NumberColumn(format="%.2f×"),
        },
    )
    st.info(
        f"시장 국면 조정 {trace['regime']['adjustment']:+.1%}: "
        + " · ".join(trace["regime"]["trace"][:-1])
    )
    st.caption(
        f"보수/기준/낙관 연구값 ${decision['scenario_values']['conservative']:,.2f} / "
        f"${decision['scenario_values']['base']:,.2f} / ${decision['scenario_values']['optimistic']:,.2f}. "
        "최종 매수구간은 이 넓은 외곽 범위가 아니라 자동 3단계 가격만 사용합니다."
    )

    with st.expander("판단 변경 조건·계산 근거", expanded=False):
        for condition in decision["invalidation_conditions"]:
            st.write(f"- {condition}")
        st.write(f"- 분할 간격: 1차 대비 {decision['buy_ladder_span_of_first_price']:.1%}")
        st.write(f"- 분할 기준: {decision['buy_ladder_basis']}")
        st.write(f"- 계산: {decision['calculation_basis']}")
        for name, digest in decision["source_hashes"].items():
            st.write(f"- {name} SHA-256: `{digest}`")

    st.markdown("#### Point-in-time 원장 부분 검증")
    st.caption(backtest["scope"])
    backtest_rows = []
    for horizon, result in backtest["horizons"].items():
        backtest_rows.append(
            {
                "기간": horizon,
                "표본": result["sample_count"],
                "적중률": result["hit_rate"],
                "중앙 수익률": result["median_return"],
                "QQQ 초과수익": result["qqq_excess_return"],
                "최대 낙폭": result["maximum_drawdown"],
            }
        )
    st.dataframe(
        pd.DataFrame(backtest_rows),
        hide_index=True,
        width="stretch",
        column_config={
            "적중률": st.column_config.NumberColumn(format="percent"),
            "중앙 수익률": st.column_config.NumberColumn(format="percent"),
            "QQQ 초과수익": st.column_config.NumberColumn(format="percent"),
            "최대 낙폭": st.column_config.NumberColumn(format="percent"),
        },
    )
    st.caption(
        f"원장 {backtest['ledger_row_count']}건 · P35 신호 {backtest['signal_count']}건 · "
        f"미래정보 사용 {str(backtest['future_information_used']).lower()} · "
        "MDD는 월말 종가 기준 · QQQ 월별 원장 미확보로 초과수익 미산출"
    )

    passed = sum(bool(row["passed"]) for row in decision["promotion_gates"])
    st.markdown(f"#### 운영 승격 게이트 · {passed}/{len(decision['promotion_gates'])} 통과")
    st.dataframe(
        pd.DataFrame(
            [
                {"조건": row["id"], "통과": "통과" if row["passed"] else "미통과", "근거": row["reason"]}
                for row in decision["promotion_gates"]
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _number(value: float | None, *, prefix: str = "", suffix: str = "", decimals: int = 2) -> str:
    return "미확보" if value is None else f"{prefix}{value:,.{decimals}f}{suffix}"


def _render_v23_comparison(
    ticker: str,
    *,
    market_path: Path,
    manifest_path: Path,
    financial_path: Path,
    market_regime: dict[str, Any],
) -> dict[str, Any] | None:
    if ticker not in V23_SUPPORTED_TICKERS:
        return
    st.subheader("v23 reconstructed PIT 비교 · 읽기 전용")
    if not V23_LEDGER_PATH:
        st.info("외부 v23 원장 경로가 설정되지 않아 비교 화면을 열지 않았습니다.")
        st.caption("원본을 저장소에 복사하지 않으며 `STOCKLENS_V23_LEDGER_PATH`로만 읽습니다.")
        return

    ledger_path = Path(V23_LEDGER_PATH)
    try:
        ledger = load_v23_ledger(str(ledger_path), _mtime(ledger_path), V23_LEDGER_SHA256)
        v23_ticker = ledger["tickers"][ticker]
        comparison = compare_v23_with_stocklens(
            v23_ticker,
            market_path=market_path,
            manifest_path=manifest_path,
            financial_path=financial_path,
        )
    except (OSError, json.JSONDecodeError, V23LedgerError, KeyError, TypeError, ValueError) as exc:
        st.error(f"v23 원장 검증 실패: {exc}")
        return

    st.warning(
        "복원형 시점 데이터(reconstructed PIT) · 연구 전용 · operational_eligible=false. "
        "SEC acceptance timestamp가 없어 filingDate를 날짜 프록시로 사용하며 같은 날 종가에는 장중 누출 위험이 있습니다."
    )
    period = v23_ticker["historical_period"]
    st.caption(
        f"v23 {ledger['version']} · 생성 {ledger['generated_at']} · SHA-256 {ledger['ledger_sha256'][:12]}… · "
        f"PER 원장 {v23_ticker['historical_row_count']}건 ({period['start']}~{period['end']})"
    )

    stocklens = comparison["stocklens"]
    v23 = comparison["v23"]
    source_columns = st.columns(2, gap="medium")
    with source_columns[0]:
        with st.container(border=True):
            st.markdown("#### StockLens")
            st.metric(
                "가격",
                _number(stocklens["price"]["value"], prefix="$", decimals=2),
                help=f"기준일 {stocklens['price']['as_of']} · {stocklens['price']['calculation_basis']}",
            )
            st.metric(
                "TTM 희석 EPS",
                _number(stocklens["ttm_eps"]["value"], prefix="$", decimals=2),
                help=stocklens["ttm_eps"]["calculation_basis"],
            )
            st.metric(
                "TTM PER",
                _number(stocklens["ttm_per"]["value"], suffix="배", decimals=2),
                help=stocklens["ttm_per"]["calculation_basis"],
            )
            st.caption(
                f"가격일 {stocklens['price']['as_of'] or '미확보'} · "
                f"EPS 기준일 {stocklens['ttm_eps']['as_of'] or '미확보'}"
            )
    with source_columns[1]:
        with st.container(border=True):
            st.markdown("#### v23 복원 원장")
            st.metric(
                "가격",
                _number(v23["price"]["value"], prefix="$", decimals=2),
                help=f"기준일 {v23['price']['as_of']} · {v23['price']['calculation_basis']}",
            )
            st.metric(
                "TTM 희석 EPS",
                _number(v23["ttm_eps"]["value"], prefix="$", decimals=2),
                help=v23["ttm_eps"]["calculation_basis"],
            )
            st.metric(
                "TTM PER",
                _number(v23["ttm_per"]["value"], suffix="배", decimals=2),
                help=v23["ttm_per"]["calculation_basis"],
            )
            st.caption(
                f"가격일 {v23['price']['as_of']} · EPS 기간말 {v23['ttm_eps']['as_of']} · "
                f"EPS 공개일 프록시 {v23['ttm_eps']['available_at']}"
            )

    if comparison["comparison_status"] == "exact_date_match":
        difference = comparison["exact_date_price_difference"]["value"]
        st.success(
            f"동일 가격일 {v23['price']['as_of']} 교차검증: StockLens와 v23 종가 차이 "
            f"${difference:+.6f}. EPS·PER 정의는 합치거나 덮어쓰지 않았습니다."
        )
    elif comparison["comparison_status"] == "date_mismatch":
        st.warning(
            f"가격 기준일이 {comparison['date_gap_days']}일 다릅니다. 양쪽 값을 분리 표시하며 "
            "가격 차이·정상 관심 범위·운영 판단을 계산하지 않습니다."
        )
    else:
        st.info("StockLens 로컬 가격이 없어 v23 연구 입력만 표시합니다. 날짜 정렬이나 비교값은 만들지 않습니다.")

    self_history = build_self_history_market_premium(v23_ticker)
    self_history["regime_adjusted_candidate"] = build_regime_adjusted_candidate(
        self_history,
        market_regime,
    )
    if TOP10_CONSENSUS_PATH.is_file():
        try:
            consensus_snapshot = load_forward_consensus_snapshot(
                TOP10_CONSENSUS_PATH,
                expected_sha256=TOP10_CONSENSUS_SHA256,
            )
            consensus_record = consensus_snapshot["tickers"].get(ticker)
            if consensus_record:
                self_history["forward_consensus_candidate"] = build_forward_consensus_candidate(
                    consensus_record,
                    current_price=float(v23["price"]["value"]),
                    current_price_as_of=str(v23["price"]["as_of"]),
                    self_history=self_history,
                )
        except (OSError, json.JSONDecodeError, Top10BridgeError, KeyError, TypeError, ValueError) as exc:
            self_history["forward_consensus_error"] = str(exc)
    st.markdown("### 자기 역사 TTM PER 분포")
    st.warning(
        "reconstructed PIT 연구 전용 · operational_eligible=false. "
        "DCF 스트레스 가격·할인율·거시 프록시·피어 배수는 이 계산에 사용하지 않았습니다."
    )
    normal_candidate = self_history["normal_interest_candidate"]
    with st.container(horizontal=True):
        st.metric(
            "정상 관심 후보 · 연구 전용",
            (
                f"${normal_candidate['low']:,.2f}~${normal_candidate['high']:,.2f}"
                if normal_candidate["low"] is not None else "미산출"
            ),
            border=True,
            help="현재 v23 TTM EPS × 전체기간 PER P35~P50. 정식 적정가나 매수구간이 아닙니다.",
        )
        st.metric("현재 v23 TTM PER", f"{self_history['current_ttm_per']:.2f}배", border=True)
        st.metric("정제 역사 백분위", f"{self_history['current_percentile']:.1f}%", border=True)
        st.metric(
            "사용 표본",
            f"{self_history['sample_count_used']}/{self_history['sample_count_total']}",
            border=True,
        )

    location_labels = {
        "below_aggressive_interest_candidate": "P25 미만",
        "aggressive_interest_candidate": "적극 관심 후보(P25~P35)",
        "normal_interest_candidate": "정상 관심 후보(P35~P50)",
        "neutral_hold_candidate": "중립·보유 평가 구간(P50~P70)",
        "high_expectation_candidate": "높은 기대 구간(P70~P85)",
        "above_high_expectation_candidate": "P85 초과",
        "unavailable": "미산출",
    }
    st.info(
        f"현재 v23 가격 ${v23['price']['value']:,.2f} / PER {self_history['current_ttm_per']:.2f}배는 "
        f"정제 전체기간 분포에서 **{location_labels[self_history['current_location']]}**에 위치합니다."
    )
    if self_history["current_location"] == "below_aggressive_interest_candidate":
        st.warning(
            "역사 하단이라는 사실만으로 저평가로 단정하지 않습니다. 최근 EPS 급증으로 분모가 일시적으로 "
            "커졌는지, 사업·마진 구조가 달라졌는지를 다음 공시에서 확인해야 합니다."
        )
    elif self_history["current_location"] in {"high_expectation_candidate", "above_high_expectation_candidate"}:
        st.warning(
            "역사 상단이라는 사실만으로 고평가로 단정하지 않습니다. 높은 성장률·마진 지속성과 시장 지배력이 "
            "상단 배수를 지지하는지 별도로 검증해야 합니다."
        )

    candidate_rows = []
    for key, label in (
        ("normal_interest_candidate", "정상 관심 후보"),
        ("aggressive_interest_candidate", "적극 관심 후보"),
        ("neutral_hold_candidate", "중립·보유 평가 구간"),
        ("high_expectation_candidate", "높은 기대 구간"),
    ):
        candidate = self_history[key]
        candidate_rows.append({
            "구간": label,
            "PER 기준": "~".join(candidate["basis_percentiles"]),
            "하단": candidate["low"],
            "상단": candidate["high"],
            "상태": candidate["status"],
        })
    st.dataframe(
        pd.DataFrame(candidate_rows),
        hide_index=True,
        width="stretch",
        column_config={
            "하단": st.column_config.NumberColumn(format="$%.2f"),
            "상단": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

    full_percentiles = self_history["full_history_percentiles"]
    recent = self_history["recent_5y_percentiles"]
    recent_percentiles = recent["values"]
    primary_percentiles = ("P10", "P25", "P35", "P50", "P70", "P85", "P90")
    percentile_frame = pd.DataFrame({
        "분위수": primary_percentiles,
        "전체 기간 PER": [full_percentiles.get(key) for key in primary_percentiles],
        "최근 5년 PER": [recent_percentiles.get(key) for key in primary_percentiles],
    })
    st.dataframe(
        percentile_frame,
        hide_index=True,
        width="stretch",
        column_config={
            "전체 기간 PER": st.column_config.NumberColumn(format="%.2f배"),
            "최근 5년 PER": st.column_config.NumberColumn(format="%.2f배"),
        },
    )
    st.caption(
        f"전체 원본 {self_history['sample_count_total']}건 · 사용 {self_history['sample_count_used']}건 · "
        f"제외 {self_history['excluded_count']}건 · 최근 5년 사용 {recent['sample_count_used']}건"
        + ("(20표본 미달, 보조 분포만 표시)" if not recent["sample_gate_passed"] else "")
    )
    crosscheck = self_history["current_percentile_crosscheck"]
    st.caption(
        f"v23 원정책 현재 백분위 교차검증: 보고 {crosscheck['reported_v23']:.4f}% · "
        f"독립 재계산 {crosscheck['recomputed_v23_policy']:.4f}% · 차이 "
        f"{crosscheck['delta_percentage_points']:+.6f}%p"
    )
    with st.expander("자기 역사 표본·제외 규칙·한계", expanded=False):
        policy = self_history["exclusion_policy"]
        st.write(f"- 무효값 규칙: {policy['invalid']}")
        st.write(f"- 극단값 규칙: {policy['outliers']}")
        st.write(
            f"- Tukey 경계: {policy['lower_fence']:.2f}배~{policy['upper_fence']:.2f}배 · "
            f"무효 제외 {policy['invalid_count']}건 · 극단값 제외 {policy['outlier_count']}건"
        )
        st.write(f"- 기준일: {self_history['as_of']} · available_at: {self_history['available_at']}")
        st.write(f"- 원천 SHA-256: `{self_history['source_hash']}`")
        st.write(f"- 표본 신뢰도: `{self_history['sample_confidence']}`")
        for limitation in self_history["limitations"]:
            st.write(f"- {limitation}")

    latest_fcf = v23_ticker["current"]["latest_reported_fcf"]
    fcf_yield = v23_ticker["current"]["fcf_yield"]
    if fcf_yield["value"] is None:
        st.caption("v23 FCF yield: null 유지 · 다른 값으로 대체하지 않음")
    if ticker == "NVDA" and latest_fcf["value"] is None:
        st.caption("NVDA Q2 FCF: null 유지 · 누계 차감 공식 미검증 상태")

    with st.expander("PER 원장·출처·계산 근거", expanded=False):
        history_rows = []
        for row in v23_ticker["historical_per"]:
            history_rows.append({
                "공시일 프록시": row["filing_date"],
                "가격일": row["price"]["as_of"],
                "회계기간말": row["fiscal_period_end"],
                "가격": row["price"]["value"],
                "TTM EPS": row["ttm_eps"]["value"],
                "TTM PER": row["ttm_per"]["value"],
                "당시까지 백분위": row["history_percentile"]["value"],
            })
        history_frame = pd.DataFrame(history_rows)
        if not history_frame.empty:
            st.line_chart(history_frame.set_index("공시일 프록시")["TTM PER"], height=220)
            st.dataframe(history_frame.tail(10), hide_index=True, width="stretch")
        st.markdown("**정의와 계보**")
        for label, field in (
            ("v23 가격", v23["price"]),
            ("v23 TTM EPS", v23["ttm_eps"]),
            ("v23 TTM PER", v23["ttm_per"]),
            ("StockLens 가격", stocklens["price"]),
            ("StockLens TTM EPS", stocklens["ttm_eps"]),
            ("StockLens TTM PER", stocklens["ttm_per"]),
        ):
            st.write(
                f"- {label}: `{field['status']}` · as_of `{field['as_of']}` · "
                f"available_at `{field['available_at']}` · hash `{field['source_hash'][:12]}…`"
            )
            st.caption(field["calculation_basis"])
        st.error("정식 적정가·매수구간·운영 신호로 연결하지 않음")

    return self_history


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
qqq_manifest_path = DATA_DIR / "market" / "QQQ.manifest.json"
analysis_as_of = datetime.now(timezone.utc).isoformat()
regime_error: str | None = None
try:
    default_regime_snapshot = DATA_DIR / "macro" / "market_regime_snapshot.json"
    selected_regime_snapshot = (
        Path(REGIME_SNAPSHOT_PATH) if REGIME_SNAPSHOT_PATH else default_regime_snapshot
    )
    if selected_regime_snapshot.is_file():
        market_regime = load_market_regime_snapshot(
            selected_regime_snapshot,
            analysis_as_of=analysis_as_of,
        )
    else:
        qqq_proxy_observations = build_qqq_proxy_observations(qqq_path, qqq_manifest_path)
        market_regime = build_market_regime(
            qqq_proxy_observations,
            analysis_as_of=analysis_as_of,
        )
except (OSError, json.JSONDecodeError, MarketRegimeError, ValueError) as exc:
    regime_error = str(exc)
    market_regime = build_market_regime([], analysis_as_of=analysis_as_of)

cross_project_market_proxy: dict[str, Any] | None = None
cross_project_proxy_error: str | None = None
if TOP10_MACRO_PATH.is_file():
    try:
        cross_project_market_proxy = load_market_environment_proxy(
            TOP10_MACRO_PATH,
            expected_sha256=TOP10_MACRO_SHA256,
        )
    except (OSError, json.JSONDecodeError, Top10BridgeError, TypeError, ValueError) as exc:
        cross_project_proxy_error = str(exc)
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
same_date_decision: dict[str, Any] | None = None
same_date_backtest: dict[str, Any] | None = None
same_date_error: str | None = None
same_date_audit: dict[str, Any] | None = None
if ticker in DECISION_SUPPORTED_TICKERS:
    try:
        decision_inputs = load_same_date_inputs(
            str(SAME_DATE_INPUT_PATH),
            _mtime(SAME_DATE_INPUT_PATH),
            expected_sha256=SAME_DATE_INPUT_SHA256,
            source_root_text=str(SAME_DATE_SOURCE_ROOT),
            source_signature=_evidence_signature(SAME_DATE_SOURCE_ROOT),
        )
        if not V23_LEDGER_PATH:
            raise DecisionModelError("approved v23 ledger path is not configured")
        source_evidence = decision_inputs.get("source_evidence") or {}
        source_paths = {
            "legacy_snapshot": SAME_DATE_LEGACY_INPUT_PATH,
            "decision_snapshot": SAME_DATE_INPUT_PATH,
            "decision_ledger": SAME_DATE_SOURCE_ROOT
            / str((source_evidence.get("decision_ledger") or {}).get("path") or ""),
            "consensus": SAME_DATE_SOURCE_ROOT
            / str((source_evidence.get("consensus") or {}).get("path") or ""),
            "regime": SAME_DATE_SOURCE_ROOT
            / str((source_evidence.get("regime") or {}).get("path") or ""),
            "price_history": TOP10_PRICE_HISTORY_PATH,
            "v23_ledger": Path(V23_LEDGER_PATH),
            "decision_config": DECISION_CONFIG_PATH,
        }
        same_date_audit = load_and_validate_evidence_audit(
            SAME_DATE_AUDIT_PATH,
            expected_audit_sha256=SAME_DATE_AUDIT_SHA256,
            decision_time=str(decision_inputs.get("decision_time") or ""),
            target_tickers=tuple(sorted(DECISION_SUPPORTED_TICKERS)),
            source_paths=source_paths,
        )
        evidence_classification = same_date_audit.get("evidence_classification")
        if evidence_classification == "synthetic_fixture":
            st.warning(
                "Same-Date 증거 구분: 합성 fixture · 계약/화면 시험 전용이며 "
                "실제 원본 통과 또는 운영 승격이 아닙니다."
            )
        elif evidence_classification == "actual_local_evidence":
            st.caption("Same-Date 증거 구분: 해시로 고정된 로컬 실제 원본")
        else:
            st.warning("Same-Date 증거 구분: 미확인 · 운영 판단 생성 차단 대상")
        decision_config = load_decision_config(DECISION_CONFIG_PATH)
        decision_ledger_path = Path(V23_LEDGER_PATH)
        decision_ledger = load_v23_ledger(
            str(decision_ledger_path),
            _mtime(decision_ledger_path),
            V23_LEDGER_SHA256,
        )
        decision_ticker = decision_ledger["tickers"][ticker]
        same_date_decision = build_decision_analysis_snapshot(
            ticker,
            inputs=decision_inputs,
            self_history=decision_ticker["self_history"],
            config=decision_config,
        )
        price_history = load_json(
            str(TOP10_PRICE_HISTORY_PATH),
            _mtime(TOP10_PRICE_HISTORY_PATH),
        ) or {}
        monthly_history = price_history.get("records", {}).get(ticker, [])
        if not isinstance(monthly_history, list):
            raise DecisionModelError(f"{ticker} monthly price history is unavailable")
        same_date_backtest = backtest_pit_per_anchor(decision_ticker, monthly_history)
    except (
        OSError,
        json.JSONDecodeError,
        DecisionModelError,
        V23LedgerError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        same_date_error = str(exc)

if same_date_decision and same_date_backtest:
    _render_same_date_decision(same_date_decision, same_date_backtest)
elif ticker in DECISION_SUPPORTED_TICKERS:
    st.warning(
        "Same-Date 모듈 비활성 · decision 객체 미생성 · "
        "행동 판단·적정가·분할매수 표시 0개 · 운영 승격 결과 없음"
    )
    st.caption(f"차단 사유: {same_date_error or '검증된 입력이 없습니다.'}")
    if same_date_audit:
        st.caption(
            f"입력 사전감사 {same_date_audit.get('status', 'unknown')} · "
            f"차단 단계 {same_date_audit.get('block_stage') or '없음'} · "
            f"decision 생성 {bool(same_date_audit.get('decision_created'))}"
        )
        with st.expander("동일시점 입력 증거표"):
            requirements = same_date_audit.get("requirements") or []
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "입력": row.get("id"),
                            "상태": row.get("status"),
                            "통과": bool(row.get("passed")),
                            "사유": row.get("reason"),
                        }
                        for row in requirements
                        if isinstance(row, dict)
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

if ticker == "NVDA":
    p51_paths = (
        NVDA_P51_ADJUDICATION_PATH,
        NVDA_P51_READ_MODEL_PATH,
        NVDA_P51_DECISION_PATH,
    )
    if all(path.is_file() for path in p51_paths):
        try:
            p51_adjudication, p51_read_model, p51_decision = load_nvda_p51_artifacts(
                str(NVDA_P51_ADJUDICATION_PATH),
                _mtime(NVDA_P51_ADJUDICATION_PATH),
                str(NVDA_P51_READ_MODEL_PATH),
                _mtime(NVDA_P51_READ_MODEL_PATH),
                str(NVDA_P51_DECISION_PATH),
                _mtime(NVDA_P51_DECISION_PATH),
            )
            _render_nvda_p51_research(p51_adjudication, p51_read_model, p51_decision)
        except (OSError, json.JSONDecodeError, NvdaResearchError, KeyError, TypeError, ValueError) as exc:
            st.error(f"NVDA P5.1 연구 자료 검증 실패: {exc}")
    else:
        st.caption("NVDA P5.1 부분유효 연구 read model이 아직 생성되지 않았습니다.")

v23_self_history = _render_v23_comparison(
    ticker,
    market_path=market_path,
    manifest_path=manifest_path,
    financial_path=financial_path,
    market_regime=market_regime,
)
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
regime_labels = {
    "expansion": "확장",
    "slowdown": "둔화",
    "neutral_mixed": "중립·혼재",
    "stable_disinflation": "물가 안정·둔화",
    "inflationary": "인플레이션",
    "risk_on": "리스크온",
    "risk_off": "리스크오프",
    "neutral": "중립",
}
if analysis:
    references = analysis["valuation_references"]
    evidence_contract = analysis.get("valuation_evidence", {})
    evidence_axes = evidence_contract.get("axes", {})
    evidence_map = evidence_contract.get("map", {})
    conditional = references.get("conditional_research_valuation", {})
    implied_range = conditional.get("implied_growth_range")
    if v23_self_history and v23_self_history.get("sample_gate_passed"):
        st.warning(
            "v23 자기 역사 PER 기반 정상 관심 후보는 연구 전용으로 표시합니다. 정식 적정가와 운영 매수 판단은 "
            "여전히 비활성이며, 정확성 부족을 더 낮은 가치로 바꾸지 않습니다."
        )
    else:
        st.warning(
            "정상 관심 범위와 정식 적정가는 아직 없습니다. 정확성 부족을 더 낮은 가치로 바꾸지 않으며, "
            "현재 시장 기대와 확보·미확보 근거를 먼저 보여줍니다."
        )

    st.markdown("#### 현재 시장 국면 · 읽기 전용")
    with st.container(horizontal=True):
        for axis_key, label in (
            ("economy_regime", "경기"),
            ("inflation_regime", "물가"),
            ("risk_regime", "리스크"),
        ):
            axis = market_regime[axis_key]
            st.metric(
                label,
                regime_labels.get(axis["regime"], axis["regime"]),
                f"신뢰 {axis['confidence']} · 커버리지 {axis['coverage']:.0%}",
                border=True,
            )
    st.caption(
        f"국면 기준일 {market_regime.get('as_of') or '미확보'} · 상태 {market_regime['status']} · "
        f"전체 커버리지 {market_regime['overall_coverage']:.0%} · operational_eligible=false"
    )
    if regime_error:
        st.warning(f"로컬 국면 입력 검증 실패로 빈 프록시 상태를 사용했습니다: {regime_error}")

    st.markdown("#### 교차 프로젝트 시장환경 프록시 · 공식 국면과 분리")
    if cross_project_market_proxy:
        proxy = cross_project_market_proxy
        proxy_indicators = proxy["indicators"]
        with st.container(horizontal=True):
            st.metric("Top10 프록시 상태", proxy["state"], f"점수 {proxy['score']:.0f}", border=True)
            st.metric(
                "10년물 프록시",
                f"{proxy_indicators['tenYear']['value']:.2f}%",
                f"20일 {proxy_indicators['tenYear']['change20']:+.2f}%p",
                border=True,
            )
            st.metric(
                "VIX 프록시",
                f"{proxy_indicators['vix']['value']:.2f}",
                f"20일 {proxy_indicators['vix']['change20']:+.2f}%",
                border=True,
            )
            st.metric(
                "QQQ 프록시",
                f"${proxy_indicators['qqq']['value']:.2f}",
                f"20일 {proxy_indicators['qqq']['change20']:+.2f}%",
                border=True,
            )
        st.caption(
            f"cross_project_market_proxy · 기준일 {proxy['as_of']} · 생성 {proxy['available_at']} · "
            f"SHA-256 {proxy['source_hash'][:12]}… · operational_eligible=false"
        )
        st.info(
            "이 프록시는 공식 경기·물가·정책금리 입력을 대체하지 않으며 PER 밴드를 자동 변경하지 않습니다. "
            + proxy["interpretation"]
        )
        with st.expander("교차 프로젝트 프록시 방향·결측 상세", expanded=False):
            for trace in proxy["rule_trace"]:
                st.write(f"- {trace}")
            st.markdown("**미확보 공식 입력**")
            for missing in proxy["missing_official_inputs"]:
                st.write(f"- {missing}")
            st.write(f"- PER 정책: {proxy['per_band_policy']}")
            st.write(f"- 원천 해시: `{proxy['source_hash']}`")
    elif cross_project_proxy_error:
        st.warning(f"교차 프로젝트 시장환경 스냅샷 검증 실패: {cross_project_proxy_error}")
    else:
        st.caption("승인된 로컬 Top10 시장환경 스냅샷이 없어 이 프록시 블록은 비활성입니다.")

    adjusted_history = (
        v23_self_history.get("regime_adjusted_candidate", {})
        if v23_self_history else {}
    )
    with st.container(horizontal=True):
        normal_history = (
            v23_self_history.get("normal_interest_candidate", {})
            if v23_self_history else {}
        )
        normal_history_value = (
            f"${normal_history['low']:,.2f}~${normal_history['high']:,.2f}"
            if normal_history.get("low") is not None and normal_history.get("high") is not None
            else "미산출"
        )
        st.metric(
            "자기 역사 정상 관심 후보" if v23_self_history else "정상 관심 범위",
            normal_history_value,
            border=True,
            help="v23 현재 TTM EPS × 정제 역사 PER P35~P50. 정식 적정가나 운영 매수구간이 아닙니다.",
        )
        adjusted_history_value = (
            f"${adjusted_history['low']:,.2f}~${adjusted_history['high']:,.2f}"
            if adjusted_history.get("low") is not None and adjusted_history.get("high") is not None
            else "미산출"
        )
        st.metric(
            "국면 조정 후보 · 연구 전용",
            adjusted_history_value,
            "~".join(adjusted_history.get("basis_percentiles", [])) or "근거 미확보",
            border=True,
            help="현재 국면이 선택한 자기 역사 PER 분위수. 정식 적정가·매수구간·신호가 아닙니다.",
        )
        st.metric(
            "현재 시장 요구 성장",
            (
                f"{_pct(implied_range.get('low'))}~{_pct(implied_range.get('high'))}"
                if implied_range else "미산출"
            ),
            border=True,
            help="연구 할인·터미널 격자별 현재가 역산값이며 전망치나 적정가가 아닙니다.",
        )
        st.metric(
            "품질 근거 상태",
            evidence_axes.get("quality_premium", {}).get("status", "unavailable"),
            border=True,
        )
        st.metric("운영 투자판단", "비활성", border=True)
    st.caption("valuation_evidence operational_eligible=false · 연구 결과는 운영 매수·매도 판단에 연결되지 않습니다.")
    forward_candidate = (
        v23_self_history.get("forward_consensus_candidate", {})
        if v23_self_history else {}
    )
    if forward_candidate:
        st.markdown("#### TTM PER × 미래 EPS basis 불일치 산술표 · 행동 계산 미사용")
        st.warning(
            "unverified_street_basis · 연구 전용 · operational_eligible=false. "
            "GAAP/비GAAP 정의가 검증되지 않았고 TTM EPS·회사 가이던스·자체 추정과 합치지 않습니다."
        )
        with st.container(horizontal=True):
            st.metric(
                "미래 EPS 저/기준/고",
                (
                    f"${forward_candidate['eps']['low']:.2f} / "
                    f"${forward_candidate['eps']['base']:.2f} / "
                    f"${forward_candidate['eps']['high']:.2f}"
                ),
                f"애널리스트 {forward_candidate['analyst_count']}명 · 분산 {forward_candidate['dispersion']:.1%}",
                border=True,
            )
            st.metric(
                "현재가 기준 미래 PER",
                f"{forward_candidate['forward_per']['base']:.2f}배",
                f"EPS 고~저 기준 {forward_candidate['forward_per']['high']:.2f}~{forward_candidate['forward_per']['low']:.2f}배",
                border=True,
            )
            st.metric(
                "미래 EPS×TTM PER 산술 외곽",
                f"${forward_candidate['range_low']:.2f}~${forward_candidate['range_high']:.2f}",
                "P35~P50 · 정식 적정가 아님",
                border=True,
            )
        matrix_rows = [
            {
                "EPS 경우": row["eps_case"],
                "EPS": row["eps"],
                "역사 PER": row["per_percentile"],
                "배수": row["per"],
                "연구 가격 후보": row["price_candidate"],
            }
            for row in forward_candidate["matrix"]
        ]
        st.dataframe(
            pd.DataFrame(matrix_rows),
            hide_index=True,
            width="stretch",
            column_config={
                "EPS": st.column_config.NumberColumn(format="$%.2f"),
                "배수": st.column_config.NumberColumn(format="%.2f×"),
                "연구 가격 후보": st.column_config.NumberColumn(format="$%.2f"),
            },
        )
        st.caption(
            f"가격 ${forward_candidate['price']:.2f} ({forward_candidate['price_as_of']}) · "
            f"컨센서스 수집 {forward_candidate['consensus_collected_at']} · 대상 {forward_candidate['fiscal_end']} · "
            f"다음 연도 EPS ${forward_candidate['next_consensus_eps']:.2f} ({forward_candidate['next_fiscal_end']})"
        )
        st.caption(forward_candidate["confidence_explanation"])
        with st.expander("미래 EPS 후보 출처·제약", expanded=False):
            st.write(f"- 출처: {forward_candidate['source']}")
            st.write(f"- 원천 SHA-256: `{forward_candidate['source_hash']}`")
            st.write(f"- 계산: {forward_candidate['calculation_basis']}")
            for limitation in forward_candidate["limitations"]:
                st.write(f"- {limitation}")
    elif v23_self_history and v23_self_history.get("forward_consensus_error"):
        st.warning(
            "미래 EPS 로컬 스냅샷 검증 실패: "
            f"{v23_self_history['forward_consensus_error']}"
        )
    if adjusted_history:
        st.info(
            "국면 조정 설명: " + " ".join(adjusted_history.get("rule_trace", []))
            + ". 외부 거시 스냅샷이 없으면 QQQ 로컬 프록시만 사용하며 P35~P50을 유지합니다."
        )
    with st.expander("시장 국면 입력·프록시·규칙 상세", expanded=False):
        st.write(f"- 모델: {market_regime['model_version']}")
        st.write(f"- 분석 시각: {market_regime['analysis_as_of']}")
        st.write(f"- 원천 해시: `{market_regime['source_hash']}`")
        if market_regime["accepted_observations"]:
            for observation in market_regime["accepted_observations"]:
                st.write(
                    f"- {observation['metric_id']}: {observation['value']:.4g} {observation['unit']} · "
                    f"{observation['status']} · 기준 {observation['as_of']} · 공개 {observation['available_at']}"
                )
                if observation.get("calculation_basis"):
                    st.caption(observation["calculation_basis"])
        else:
            st.write("- 사용 가능한 시점 입력 없음")
        for axis_key, label in (
            ("economy_regime", "경기"),
            ("inflation_regime", "물가"),
            ("risk_regime", "리스크"),
        ):
            st.markdown(f"**{label} 규칙 추적**")
            for trace in market_regime[axis_key]["rule_trace"]:
                st.write(f"- {trace}")
        explanations_ko = explain_market_regime_ko(market_regime)
        if explanations_ko:
            st.markdown("**공식 입력의 방향성 설명**")
            for explanation in explanations_ko:
                st.write(f"- {explanation}")
        st.caption(
            "FRED 고지: This product uses the FRED® API but is not endorsed or certified by "
            "the Federal Reserve Bank of St. Louis. Cboe VIX 원문은 개인 로컬 연구에서만 사용합니다."
        )

    if implied_range:
        st.info(
            "현재 가격이 요구하는 5년 CFO-CAPEX 대용 성장률은 연구 할인·터미널 격자에서 "
            f"{_pct(implied_range.get('low'))}~{_pct(implied_range.get('high'))}입니다. "
            "높은 수치 자체를 비현실적·고평가로 자동 판정하지 않습니다."
        )
    else:
        st.info("현재가 역산에 필요한 검증 입력이 부족하여 시장 요구 성장도 미산출입니다.")

    if v23_self_history and v23_self_history.get("sample_gate_passed"):
        st.info(
            "자기 역사 PER 축은 reconstructed PIT 연구 상태로 활성화됐습니다. 피어 상대가치와 정식 내재가치 "
            "입력은 여전히 미확보이며, 이 구간은 운영 투자판단에 사용하지 않습니다."
        )
    else:
        st.info(evidence_map.get("reason", "가치 지도 근거가 미확보되었습니다."))
    map_rows = {
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
            if isinstance(value, float) and row.get("id") in {
                "revenue_change", "operating_margin", "fcf_margin", "fcf_conversion",
            }:
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

    with st.expander("평가 축 산출 자격과 결측 정책", expanded=False):
        for axis_id, label in (
            ("self_history", "자기 역사"),
            ("peer_relative", "피어 상대가치"),
            ("intrinsic_reverse", "내재가치·역산 기대"),
            ("quality_premium", "품질 프리미엄 증거"),
        ):
            axis = evidence_axes.get(axis_id, {})
            if axis_id == "self_history" and v23_self_history:
                axis = {
                    "status": v23_self_history["status"],
                    "calculation_basis": v23_self_history["calculation_basis"],
                    "eligibility_policy": {
                        "missing_behavior": "20개 미만 또는 EPS 결측이면 후보 범위를 null로 유지",
                        "lower_bound_basis": "현재 v23 TTM EPS × 정제 역사 PER P35",
                        "upper_bound_basis": "현재 v23 TTM EPS × 정제 역사 PER P50",
                        "calculation_conditions": [
                            "공시일 프록시가 계산 기준일 이하인 행만 사용",
                            "유한한 양수 PER만 사용하고 1.5×IQR Tukey 극단값 제외",
                            "사용 표본 20개 이상",
                        ],
                        "backtest_criteria": [
                            "현재는 reconstructed PIT 연구 결과이며 운영 검증 전",
                        ],
                    },
                }
            policy = axis.get("eligibility_policy", {})
            st.markdown(f"**{label}: {axis.get('status', 'unavailable')}**")
            st.caption(axis.get("calculation_basis", "산출 근거 미확보"))
            st.write(f"- 결측 시: {policy.get('missing_behavior', 'null/unavailable 유지')}")
            st.write(f"- 하한 근거: {policy.get('lower_bound_basis', '미정')}")
            st.write(f"- 상한 근거: {policy.get('upper_bound_basis', '미정')}")
            for condition in policy.get("calculation_conditions", []):
                st.write(f"- 산출 조건: {condition}")
            for criterion in policy.get("backtest_criteria", []):
                st.write(f"- 검증 기준: {criterion}")

    if references.get("status") == "research_only":
        st.warning("연구 참고 전용 · 정식 시가총액/PER/적정가/매수구간이 아닙니다.")
        point_shares = references.get("point_in_time_share_evidence")
        point_reference = references.get("point_in_time_equity_reference")
        if point_reference and point_reference.get("status") == "time_mismatched_research_only":
            st.warning(
                f"가격일 {point_reference['price_as_of']}과 주식 수 기준일 "
                f"{point_reference['shares_as_of']}이 {point_reference['date_gap_days']}일 차이입니다. "
                "따라서 확정 시가총액·정식 FCF yield로 사용하지 않습니다."
            )

        with st.expander("평가 입력과 기준일 상세", expanded=False):
            with st.container(horizontal=True):
                st.metric("주식수 대용치", f"{references['diluted_share_proxy_millions']:,.0f}M", border=True)
                st.metric(
                    "대용 시가가치",
                    _money_millions(references["proxy_equity_value_usd_millions"]),
                    border=True,
                )
                st.metric("대용 TTM FCF yield", _pct(references["proxy_ttm_fcf_yield"]), border=True)
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
            if point_shares:
                st.markdown("**공식 기준일 발행주식 수 증거**")
                with st.container(horizontal=True):
                    st.metric("발행 보통주", f"{point_shares['value_millions']:,.3f}M", border=True)
                    st.metric("주식 수 기준일", point_shares["as_of"], border=True)
                    st.metric("증거 상태", point_shares["source_integrity"], border=True)
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
                with st.container(horizontal=True):
                    st.metric(
                        "시점 불일치 주식가치 참고",
                        _money_millions(point_reference.get("equity_value_usd_millions")),
                        border=True,
                    )
                    st.metric(
                        "시점 불일치 TTM FCF yield",
                        _pct(point_reference.get("ttm_fcf_yield")),
                        border=True,
                    )
            elif point_reference and point_reference.get("formal_market_cap_eligible"):
                st.success("가격일과 주식 수 기준일이 일치하는 검증 참고치입니다.")

        if conditional.get("status") == "research_only":
            with st.expander("2차 상세 · DCF 스트레스 참고 범위", expanded=False):
                st.warning(
                    "외부 전망치·정상 적정가·목표가격·매수구간이 아닙니다. "
                    "어느 행도 공식 base case로 선택하지 않습니다."
                )
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
                    f"격자 외곽 참고 ${outer.get('low', 0):,.2f}~${outer.get('high', 0):,.2f} · "
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
                        "현재가 내재 성장률은 연구 할인율·터미널 성장률에 종속되며 미래 전망이 아닙니다. "
                        f"가격일 {conditional.get('price_as_of', '미확보')} · 주식 수 기준일 "
                        f"{conditional.get('shares_as_of', '미확보')} · 차이 "
                        f"{conditional.get('date_gap_days', '미확보')}일"
                    )
                drivers = conditional.get("driver_sensitivity", [])
                if drivers:
                    st.write("가치 결과 영향도 순위")
                    for index, driver in enumerate(drivers, start=1):
                        st.write(
                            f"{index}. {driver['driver']} · 연구 격자 내 주당 영향 "
                            f"${abs(driver['per_share_impact']):,.2f}"
                        )
                st.markdown("**가정과 한계**")
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
    st.markdown("#### 현재 시장 국면 · 읽기 전용")
    with st.container(horizontal=True):
        for axis_key, label in (
            ("economy_regime", "경기"),
            ("inflation_regime", "물가"),
            ("risk_regime", "리스크"),
        ):
            axis = market_regime[axis_key]
            st.metric(
                label,
                regime_labels.get(axis["regime"], axis["regime"]),
                f"신뢰 {axis['confidence']} · 커버리지 {axis['coverage']:.0%}",
                border=True,
            )
    st.caption(
        f"국면 기준일 {market_regime.get('as_of') or '미확보'} · 상태 {market_regime['status']} · "
        f"전체 커버리지 {market_regime['overall_coverage']:.0%} · operational_eligible=false"
    )
    normal_history = v23_self_history.get("normal_interest_candidate", {}) if v23_self_history else {}
    adjusted_history = v23_self_history.get("regime_adjusted_candidate", {}) if v23_self_history else {}
    with st.container(horizontal=True):
        st.metric(
            "자기 역사 정상 관심 후보",
            (
                f"${normal_history['low']:,.2f}~${normal_history['high']:,.2f}"
                if normal_history.get("low") is not None else "미산출"
            ),
            border=True,
        )
        st.metric(
            "국면 조정 후보 · 연구 전용",
            (
                f"${adjusted_history['low']:,.2f}~${adjusted_history['high']:,.2f}"
                if adjusted_history.get("low") is not None else "미산출"
            ),
            "~".join(adjusted_history.get("basis_percentiles", [])) or "근거 미확보",
            border=True,
        )
        st.metric("운영 투자판단", "비활성", border=True)
    if adjusted_history:
        st.info("국면 조정 설명: " + " ".join(adjusted_history.get("rule_trace", [])))
    st.info("기타 가치 참고 지표 계약은 적용할 수 없습니다. 자기 역사·국면 후보와 운영 판단은 분리됩니다.")
    with st.expander("시장 국면 입력·프록시·규칙 상세", expanded=False):
        st.write(f"- 모델: {market_regime['model_version']}")
        st.write(f"- 원천 해시: `{market_regime['source_hash']}`")
        for observation in market_regime["accepted_observations"]:
            st.write(
                f"- {observation['metric_id']}: {observation['value']:.4g} {observation['unit']} · "
                f"{observation['status']} · 기준 {observation['as_of']}"
            )
        for explanation in explain_market_regime_ko(market_regime):
            st.write(f"- {explanation}")

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
