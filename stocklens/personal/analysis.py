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
        "decision": {
            "status": "hold",
            "is_buy_signal": False,
            "reason": "Technical indicators are descriptive; fair value and independently validated signals are unavailable",
        },
    }
