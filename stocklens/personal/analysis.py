from __future__ import annotations

from typing import Any

import pandas as pd

from .market_data import compute_relative_strength, compute_technical_snapshot


COMPANY_PROFILES: dict[str, dict[str, Any]] = {
    "AMAT": {
        "business": "반도체·디스플레이 제조용 재료공학 장비와 관련 서비스를 제공한다.",
        "risks": ["반도체 설비투자 사이클", "중국 매출·수출규제", "대규모 생산능력 투자의 회수 시차"],
    },
    "AVGO": {
        "business": "AI 네트워킹·맞춤형 반도체와 VMware 기반 인프라 소프트웨어를 운영한다.",
        "risks": ["AI 고객 집중", "VMware 통합", "높은 금융부채와 인수 관련 무형자산"],
    },
    "QCOM": {
        "business": "무선 반도체와 특허 라이선스를 중심으로 자동차·IoT·PC로 사업을 확장한다.",
        "risks": ["스마트폰 수요와 고객 집중", "라이선스 규제·소송", "투자손익·세금효과에 따른 GAAP EPS 왜곡"],
    },
}


def _period(financial: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((row for row in financial.get("periods", []) if row.get("kind") == kind), None)


def compute_fundamental_indicators(financial: dict[str, Any]) -> dict[str, Any]:
    ticker = financial["ticker"]
    fiscal_year = _period(financial, "fiscal_year")
    ttm = _period(financial, "ttm")
    if not fiscal_year or not ttm:
        return {"status": "unavailable", "reason": "FY and TTM periods are both required"}
    fy = fiscal_year["metrics"]
    current = ttm["metrics"]

    revenue_growth = current["revenue"] / fy["revenue"] - 1 if fy.get("revenue") else None
    operating_margin = current["operating_income"] / current["revenue"] if current.get("revenue") else None
    fcf_margin = current["fcf"] / current["revenue"] if current.get("revenue") else None

    balance = financial.get("balance_sheet", {})
    cash = balance.get("cash_and_equivalents")
    investments = sum(
        value
        for value in (
            balance.get("short_term_investments"),
            balance.get("long_term_investments"),
            balance.get("marketable_securities"),
        )
        if value is not None
    )
    debt_values = (balance.get("short_term_debt"), balance.get("long_term_debt"))
    debt = sum(value for value in debt_values if value is not None)
    known_net_cash = None if cash is None or any(value is None for value in debt_values) else cash + investments - debt
    investment_scope_complete = ticker in {"AMAT", "QCOM"}

    return {
        "status": "verified" if financial.get("status") == "verified" else "partial",
        "financial_as_of": ttm["period_end"],
        "profile": COMPANY_PROFILES[ticker],
        "metrics": {
            "ttm_revenue_change_vs_fy2025": {
                "value": revenue_growth,
                "formula": "TTM revenue / FY2025 revenue - 1",
                "status": "verified",
                "note": "Rolling twelve-month change, not year-over-year growth",
            },
            "ttm_operating_margin": {
                "value": operating_margin,
                "formula": "TTM operating income / TTM revenue",
                "status": "verified",
            },
            "ttm_fcf_margin": {
                "value": fcf_margin,
                "formula": "(TTM CFO - TTM CAPEX) / TTM revenue",
                "status": "verified",
            },
            "known_net_cash": {
                "value_usd_millions": known_net_cash,
                "formula": "cash + verified investments - short-term debt - long-term debt",
                "status": "verified" if investment_scope_complete else "partial",
                "note": None if investment_scope_complete else "Marketable/non-operating investments are not fully verified",
            },
        },
        "valuation_inputs": {
            "market_cap": {"status": "unavailable", "reason": "Point-in-time common shares outstanding are not verified"},
            "fcf_yield": {"status": "unavailable", "reason": "Verified market capitalization is unavailable"},
            "per": {"status": "unavailable", "reason": "Consistent TTM diluted EPS is intentionally withheld"},
            "fair_value": {"status": "hold", "reason": "Forecast and valuation eligibility inputs are incomplete"},
        },
        "guidance": financial.get("guidance", {}),
        "earnings_quality": financial.get("earnings_quality", {"status": "reported_gaap"}),
    }


def _research_valuation_references(
    financial: dict[str, Any], latest_price: float | None,
) -> dict[str, Any]:
    """Build explicitly non-operational references from verified reported values.

    A weighted-average diluted share count is not point-in-time shares outstanding,
    so the resulting capitalization and yield are kept as research proxies.  This
    function never creates a fair value, target price, or recommendation.
    """

    if latest_price is None or latest_price <= 0:
        return {
            "status": "unavailable",
            "reason": "A verified local closing price is required",
            "operational_eligible": False,
        }
    fiscal_year = _period(financial, "fiscal_year")
    quarter = _period(financial, "quarter")
    ttm = _period(financial, "ttm")
    if not fiscal_year or not quarter or not ttm:
        return {
            "status": "unavailable",
            "reason": "FY, latest quarter, and TTM periods are required",
            "operational_eligible": False,
        }
    fy_metrics = fiscal_year["metrics"]
    quarter_metrics = quarter["metrics"]
    ttm_metrics = ttm["metrics"]
    diluted_share_proxy = quarter_metrics.get("diluted_shares")
    fy_eps = fy_metrics.get("diluted_eps")
    ttm_fcf = ttm_metrics.get("fcf")
    if not diluted_share_proxy:
        return {
            "status": "unavailable",
            "reason": "A reported diluted weighted-average share count is unavailable",
            "operational_eligible": False,
        }
    proxy_equity_value = latest_price * float(diluted_share_proxy)
    return {
        "status": "research_only",
        "operational_eligible": False,
        "share_basis": "latest-quarter diluted weighted-average shares; not point-in-time shares outstanding",
        "share_basis_period": quarter.get("label"),
        "diluted_share_proxy_millions": float(diluted_share_proxy),
        "proxy_equity_value_usd_millions": proxy_equity_value,
        "proxy_ttm_fcf_yield": float(ttm_fcf) / proxy_equity_value if ttm_fcf is not None else None,
        "fy_gaap_eps_reference_multiple": latest_price / float(fy_eps) if fy_eps else None,
        "fy_gaap_eps_period": fiscal_year.get("label"),
        "formal_market_cap": None,
        "formal_per": None,
        "fair_value": None,
        "reverse_dcf": {
            "status": "underdetermined",
            "reason": (
                "Verified forecast cash flows, WACC, terminal assumptions, and point-in-time "
                "diluted shares are unavailable; no implied growth rate is solved"
            ),
        },
        "interpretation": (
            "The low proxy FCF yield and high historical-period EPS reference multiple require "
            "durable growth and margins, but they do not establish overvaluation or fair value."
        ),
    }


def _technical_interpretation(technical: dict[str, Any], relative: dict[str, Any]) -> dict[str, Any]:
    if technical.get("status") == "unavailable":
        return {"label": "이력 부족", "reason": technical.get("reason"), "signal": False}
    close = technical.get("latest_close")
    ema20 = technical.get("ema20")
    ema50 = technical.get("ema50")
    rsi = technical.get("rsi14")
    if None in (close, ema20, ema50, rsi):
        return {"label": "이력 부족", "reason": "EMA20·EMA50·RSI14 입력이 부족합니다.", "signal": False}
    if close > ema20 > ema50 and 50 <= rsi < 70:
        label = "상승 우위"
    elif close < ema20 < ema50 and rsi < 50:
        label = "하락 우위"
    else:
        label = "중립"
    relative_63d = relative.get("relative_return_63d")
    reason = f"종가/EMA20/EMA50 배열과 RSI14({rsi:.1f})의 설명적 분류"
    if relative_63d is not None:
        reason += f", 63거래일 QQQ 대비 {relative_63d:+.1%}p"
    return {"label": label, "reason": reason, "signal": False}


def compute_personal_analysis(
    financial: dict[str, Any],
    market: pd.DataFrame | None = None,
    benchmark: pd.DataFrame | None = None,
    market_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fundamental = compute_fundamental_indicators(financial)
    market_frame = market if market is not None else pd.DataFrame()
    technical = compute_technical_snapshot(market_frame)
    relative = compute_relative_strength(market_frame, benchmark if benchmark is not None else pd.DataFrame())
    manifest = market_manifest or {}
    latest_price = None
    if not market_frame.empty and "close" in market_frame:
        candidate = pd.to_numeric(market_frame["close"], errors="coerce").dropna()
        latest_price = float(candidate.iloc[-1]) if not candidate.empty else None
    valuation_references = _research_valuation_references(financial, latest_price)
    technical_interpretation = _technical_interpretation(technical, relative)
    performance_label = "판단 부족"
    metrics = fundamental.get("metrics", {})
    revenue_change = metrics.get("ttm_revenue_change_vs_fy2025", {}).get("value")
    operating_margin = metrics.get("ttm_operating_margin", {}).get("value")
    if revenue_change is not None and operating_margin is not None:
        performance_label = "긍정" if revenue_change > 0 and operating_margin > 0.20 else "중립"
    return {
        "ticker": financial["ticker"],
        "status": "research_only",
        "fundamental": fundamental,
        "market": {
            "status": manifest.get("freshness_status", "unavailable"),
            "price_as_of": manifest.get("market_date"),
            "collected_at_utc": manifest.get("collected_at_utc"),
            "provider": manifest.get("provider"),
            "price_policy": manifest.get("price_policy"),
            "license_scope": manifest.get("classification"),
        },
        "technical": technical,
        "relative_strength": relative,
        "valuation_references": valuation_references,
        "interpretation": {
            "business": {"label": "중립", "reason": "사업 설명과 주요 위험은 확인했지만 경쟁력 점수는 검증하지 않았습니다."},
            "performance": {
                "label": performance_label,
                "reason": "검증된 TTM/FY 매출 변화와 TTM 영업이익률 기준",
            },
            "value": {
                "label": "판단 부족",
                "reason": "공식 시가총액·일관된 TTM EPS·검증된 전망과 할인율이 부족합니다.",
            },
            "technical": technical_interpretation,
            "risk": COMPANY_PROFILES.get(financial["ticker"], {}).get("risks", []),
            "overall": (
                "실적 흐름은 확인 가능하지만 가치평가는 연구 참고에 그치며, "
                f"기술적 위치는 {technical_interpretation['label']}입니다. 운영 매수 신호는 없습니다."
            ),
        },
        "signal_research": {
            "status": "data_insufficient",
            "history_sessions": int(len(market_frame)),
            "independent_validated_strategies": 0,
            "active_operational_signals": 0,
            "reason": "100거래일 원시 비조정 이력은 EMA200과 독립 전략 검증에 부족합니다.",
        },
        "decision": {
            "status": "hold",
            "is_buy_signal": False,
            "reason": "Technical indicators are descriptive; fair value and independently validated signals are unavailable",
        },
    }
