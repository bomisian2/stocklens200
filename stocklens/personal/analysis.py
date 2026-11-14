from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from stocklens.dcf import equity_fcf_schedule, solve_implied_fcf_growth

from .market_data import compute_relative_strength, compute_technical_snapshot
from .universe import PERSONAL_PILOT_UNIVERSE


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
    "GOOGL": {
        "business": "검색·광고, YouTube, Cloud와 AI 플랫폼을 운영한다.",
        "risks": ["AI 인프라 CAPEX와 회수 시차", "광고 경기·규제", "투자증권 평가손익에 따른 EPS 왜곡"],
    },
    "NVDA": {
        "business": "AI 가속기, 데이터센터 컴퓨팅과 CUDA 소프트웨어 플랫폼을 제공한다.",
        "risks": ["AI 투자 사이클", "고객 집중과 자체 칩", "수출규제·공급망"],
    },
    "MSFT": {
        "business": "Azure, Microsoft 365, Windows, 게임과 AI 서비스를 운영한다.",
        "risks": ["AI 데이터센터 CAPEX", "클라우드 경쟁", "규제와 사이버보안"],
    },
    "MU": {
        "business": "DRAM·NAND 메모리와 데이터센터용 메모리 제품을 공급한다.",
        "risks": ["메모리 가격 사이클", "대규모 설비투자", "중국·공급망 규제"],
    },
    "AMD": {
        "business": "데이터센터·PC CPU, GPU와 적응형 컴퓨팅 제품을 제공한다.",
        "risks": ["AI GPU 경쟁", "파운드리 공급 의존", "인수 무형자산과 주식보상"],
    },
    "TSM": {
        "business": "첨단 공정 중심의 반도체 파운드리 서비스를 제공한다.",
        "risks": ["대만 지정학", "고객 집중", "TWD·ADR·TIFRS 변환 위험"],
    },
    "LRCX": {
        "business": "식각·증착 중심 반도체 제조 장비와 설치 기반 서비스를 제공한다.",
        "risks": ["메모리·파운드리 CAPEX 사이클", "중국 수출규제", "고객 집중"],
    },
}


def _period(financial: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((row for row in financial.get("periods", []) if row.get("kind") == kind), None)


def _latest_document(financial: dict[str, Any]) -> dict[str, Any]:
    documents = financial.get("documents", [])
    return max(documents, key=lambda row: str(row.get("published_at", "")), default={})


def _guidance_analysis(financial: dict[str, Any]) -> dict[str, Any]:
    """Explain issuer guidance without mixing GAAP and non-GAAP values."""

    guidance = financial.get("guidance", {})
    quarter = _period(financial, "quarter")
    latest_revenue = (quarter or {}).get("metrics", {}).get("revenue")
    rows: list[dict[str, Any]] = []
    comparison: dict[str, Any] = {"status": "unavailable"}

    if guidance.get("revenue_midpoint") is not None and guidance.get("non_gaap_eps_midpoint") is not None:
        midpoint = float(guidance["revenue_midpoint"])
        spread = float(guidance.get("revenue_plus_minus") or 0.0)
        low, high = midpoint - spread, midpoint + spread
        rows.extend([
            {"label": "매출 가이던스", "value": f"${low:,.0f}M~${high:,.0f}M", "basis": "issuer_guidance"},
            {
                "label": "Non-GAAP EPS 가이던스",
                "value": f"${guidance['non_gaap_eps_midpoint'] - guidance.get('non_gaap_eps_plus_minus', 0):.2f}~"
                f"${guidance['non_gaap_eps_midpoint'] + guidance.get('non_gaap_eps_plus_minus', 0):.2f}",
                "basis": "issuer_guidance_non_gaap",
            },
        ])
        if latest_revenue:
            comparison = {
                "status": "verified_calculation",
                "label": "직전 분기 대비 매출 가이던스",
                "low": low / latest_revenue - 1,
                "midpoint": midpoint / latest_revenue - 1,
                "high": high / latest_revenue - 1,
                "formula": "guidance revenue / latest reported quarter revenue - 1",
            }
    elif guidance.get("revenue_approx") is not None:
        revenue = float(guidance["revenue_approx"])
        rows.extend([
            {"label": "매출 가이던스", "value": f"약 ${revenue:,.0f}M", "basis": "issuer_guidance"},
            {
                "label": "Non-GAAP 영업이익률",
                "value": f"약 {float(guidance['non_gaap_operating_margin_percent']):.1f}%",
                "basis": "issuer_guidance_non_gaap",
            },
        ])
        if latest_revenue:
            comparison = {
                "status": "verified_calculation",
                "label": "직전 분기 대비 매출 가이던스",
                "midpoint": revenue / latest_revenue - 1,
                "formula": "guidance revenue / latest reported quarter revenue - 1",
            }
    elif guidance.get("revenue_low") is not None and guidance.get("revenue_high") is not None:
        low, high = float(guidance["revenue_low"]), float(guidance["revenue_high"])
        midpoint = (low + high) / 2
        rows.extend([
            {"label": "매출 가이던스", "value": f"${low:,.0f}M~${high:,.0f}M", "basis": "issuer_guidance"},
            {
                "label": "GAAP EPS 가이던스",
                "value": f"${float(guidance['gaap_eps_low']):.2f}~${float(guidance['gaap_eps_high']):.2f}",
                "basis": "issuer_guidance_gaap",
            },
        ])
        if latest_revenue:
            comparison = {
                "status": "verified_calculation",
                "label": "직전 분기 대비 매출 가이던스",
                "low": low / latest_revenue - 1,
                "midpoint": midpoint / latest_revenue - 1,
                "high": high / latest_revenue - 1,
                "formula": "guidance revenue / latest reported quarter revenue - 1",
            }
    elif guidance.get("revenue_midpoint") is not None and guidance.get("revenue_percent_range") is None:
        midpoint = float(guidance["revenue_midpoint"])
        spread = float(guidance.get("revenue_plus_minus") or 0.0)
        low, high = midpoint - spread, midpoint + spread
        rows.append({"label": "매출 가이던스", "value": f"${low:,.0f}M~${high:,.0f}M", "basis": "issuer_guidance"})
        if guidance.get("gaap_eps_midpoint") is not None:
            eps_midpoint = float(guidance["gaap_eps_midpoint"])
            eps_spread = float(guidance.get("gaap_eps_plus_minus") or 0.0)
            rows.append({"label": "GAAP EPS 가이던스", "value": f"${eps_midpoint - eps_spread:.2f}~${eps_midpoint + eps_spread:.2f}", "basis": "issuer_guidance_gaap"})
        if latest_revenue:
            comparison = {"status": "verified_calculation", "label": "직전 분기 대비 매출 가이던스", "low": low / latest_revenue - 1, "midpoint": midpoint / latest_revenue - 1, "high": high / latest_revenue - 1, "formula": "guidance revenue / latest reported quarter revenue - 1"}
    elif guidance.get("capex_low") is not None and guidance.get("capex_high") is not None:
        rows.append({"label": "연간 CAPEX 가이던스", "value": f"${float(guidance['capex_low']):,.0f}M~${float(guidance['capex_high']):,.0f}M", "basis": "issuer_guidance"})
    elif guidance.get("revenue_percent_range") is not None and guidance.get("revenue_midpoint") is not None:
        midpoint = float(guidance["revenue_midpoint"])
        pct = float(guidance["revenue_percent_range"]) / 100
        rows.append({"label": "매출 가이던스", "value": f"${midpoint * (1-pct):,.0f}M~${midpoint * (1+pct):,.0f}M", "basis": "issuer_guidance"})

    return {
        "status": guidance.get("status", "unavailable"),
        "period": guidance.get("period"),
        "rows": rows,
        "comparison": comparison,
        "accounting_warning": "공시의 GAAP·Non-GAAP 표기를 유지하며 서로 직접 비교하거나 혼합하지 않음",
    }


def compute_fundamental_indicators(financial: dict[str, Any]) -> dict[str, Any]:
    ticker = financial["ticker"]
    fiscal_year = _period(financial, "fiscal_year")
    ttm = _period(financial, "ttm")
    if not fiscal_year or not ttm:
        return {"status": "unavailable", "reason": "FY and TTM periods are both required"}
    fy = fiscal_year["metrics"]
    current = ttm["metrics"]

    revenue_growth = current["revenue"] / fy["revenue"] - 1 if fy.get("revenue") else None
    operating_income_change = (
        current["operating_income"] / fy["operating_income"] - 1
        if fy.get("operating_income") and current.get("operating_income") is not None
        else None
    )
    fcf_change = current["fcf"] / fy["fcf"] - 1 if fy.get("fcf") and current.get("fcf") is not None else None
    fy_operating_margin = (
        fy["operating_income"] / fy["revenue"]
        if fy.get("revenue") and fy.get("operating_income") is not None
        else None
    )
    fy_fcf_margin = fy["fcf"] / fy["revenue"] if fy.get("revenue") and fy.get("fcf") is not None else None
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
    investment_scope_complete = bool(balance.get("investment_scope_complete", ticker in {"AMAT", "QCOM"}))

    return {
        "status": "verified" if financial.get("status") == "verified" else "partial",
        "financial_as_of": ttm["period_end"],
        "profile": COMPANY_PROFILES[ticker],
        "metrics": {
            "ttm_revenue_change_vs_fy2025": {
                "value": revenue_growth,
                "formula": f"TTM revenue / {fiscal_year.get('label', 'latest fiscal year')} revenue - 1",
                "status": "verified",
                "note": "Rolling twelve-month change, not year-over-year growth",
            },
            "ttm_operating_margin": {
                "value": operating_margin,
                "formula": "TTM operating income / TTM revenue",
                "status": "verified",
            },
            "ttm_operating_income_change_vs_fy2025": {
                "value": operating_income_change,
                "formula": f"TTM operating income / {fiscal_year.get('label', 'latest fiscal year')} operating income - 1",
                "status": "verified",
                "note": "Rolling twelve-month change, not year-over-year growth",
            },
            "ttm_operating_margin_change_bps_vs_fy2025": {
                "value": (operating_margin - fy_operating_margin) * 10_000
                if operating_margin is not None and fy_operating_margin is not None
                else None,
                "formula": "(TTM operating margin - latest fiscal-year operating margin) × 10,000",
                "status": "verified",
            },
            "ttm_fcf_margin": {
                "value": fcf_margin,
                "formula": "(TTM CFO - TTM CAPEX) / TTM revenue",
                "status": "verified",
            },
            "ttm_fcf_change_vs_fy2025": {
                "value": fcf_change,
                "formula": f"TTM FCF / {fiscal_year.get('label', 'latest fiscal year')} FCF - 1",
                "status": "verified",
                "note": "Rolling twelve-month change, not year-over-year growth",
            },
            "ttm_fcf_margin_change_bps_vs_fy2025": {
                "value": (fcf_margin - fy_fcf_margin) * 10_000
                if fcf_margin is not None and fy_fcf_margin is not None
                else None,
                "formula": "(TTM FCF margin - latest fiscal-year FCF margin) × 10,000",
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
        "guidance_analysis": _guidance_analysis(financial),
        "calculation_basis": {
            "amount_unit": "USD millions except per-share data",
            "ttm_formula": ttm.get("note") or "issuer-period bridge or same fiscal-year interval as explicitly labeled",
            "fcf_definition": "reported GAAP CFO - reported CAPEX; derived cash-flow reference, not a GAAP line item",
            "latest_official_document": _latest_document(financial),
        },
        "earnings_quality": financial.get("earnings_quality", {"status": "reported_gaap"}),
    }


def _research_valuation_references(
    financial: dict[str, Any], latest_price: float | None, price_as_of: str | None = None,
) -> dict[str, Any]:
    """Build explicitly non-operational references from verified reported values.

    A weighted-average diluted share count is not point-in-time shares outstanding,
    so the resulting capitalization and yield are kept as research proxies.  This
    function never creates a fair value, target price, or recommendation.
    """

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
    latest_document = _latest_document(financial)
    share_evidence = PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("share_count_evidence")
    reverse_dcf_readiness = _reverse_dcf_input_readiness(
        financial=financial,
        latest_price=latest_price,
        price_as_of=price_as_of,
        ttm_metrics=ttm_metrics,
        share_evidence=share_evidence,
    )
    conditional_research_valuation = _conditional_research_valuation(
        financial=financial,
        latest_price=latest_price,
        price_as_of=price_as_of,
        ttm_metrics=ttm_metrics,
        share_evidence=share_evidence,
    )
    common = {
        "operational_eligible": False,
        "share_basis": "latest-quarter diluted weighted-average shares; not point-in-time shares outstanding",
        "share_basis_period": quarter.get("label"),
        "share_basis_as_of": quarter.get("period_end"),
        "share_source_url": (
            share_evidence.get("source_url") if share_evidence else latest_document.get("url")
        ),
        "share_source_published_at": (
            share_evidence.get("published_at") if share_evidence else latest_document.get("published_at")
        ),
        "diluted_share_proxy_millions": float(diluted_share_proxy),
        "formal_market_cap": None,
        "formal_per": None,
        "fair_value": None,
        "point_in_time_share_evidence": share_evidence,
        "point_in_time_equity_reference": None,
        "reverse_dcf": reverse_dcf_readiness,
        "conditional_research_valuation": conditional_research_valuation,
    }
    if latest_price is None or latest_price <= 0:
        return {
            **common,
            "status": "unavailable",
            "reason": "A verified local closing price is required",
        }
    proxy_equity_value = latest_price * float(diluted_share_proxy)
    proxy_fcf_yield = float(ttm_fcf) / proxy_equity_value if ttm_fcf is not None else None
    proxy_p_fcf = proxy_equity_value / float(ttm_fcf) if ttm_fcf else None
    sensitivity_rows: list[dict[str, Any]] = []
    if ttm_fcf and proxy_equity_value:
        for growth_rate in (0.0, 0.10, 0.15, 0.20, 0.25):
            year5_fcf = float(ttm_fcf) * (1 + growth_rate) ** 5
            sensitivity_rows.append(
                {
                    "fcf_cagr": growth_rate,
                    "year5_fcf_usd_millions": year5_fcf,
                    "year5_proxy_fcf_yield": year5_fcf / proxy_equity_value,
                    "year5_proxy_p_fcf": proxy_equity_value / year5_fcf,
                }
            )
    point_in_time_reference = None
    if share_evidence and price_as_of:
        try:
            share_as_of = date.fromisoformat(str(share_evidence["as_of"]))
            price_date = date.fromisoformat(str(price_as_of))
            gap_days = abs((price_date - share_as_of).days)
            point_equity_value = latest_price * float(share_evidence["value_millions"])
            point_in_time_reference = {
                "status": "date_aligned" if gap_days == 0 else "time_mismatched_research_only",
                "price_as_of": price_as_of,
                "shares_as_of": share_evidence["as_of"],
                "date_gap_days": gap_days,
                "equity_value_usd_millions": point_equity_value,
                "ttm_fcf_yield": float(ttm_fcf) / point_equity_value if ttm_fcf is not None else None,
                "formal_market_cap_eligible": gap_days == 0,
                "formula": "verified EOD close × official point-in-time common shares outstanding",
                "warning": (
                    None
                    if gap_days == 0
                    else "가격일과 발행주식 수 기준일이 달라 정식 시가총액이 아닌 연구용 시점 불일치 참고치"
                ),
            }
        except (KeyError, TypeError, ValueError):
            point_in_time_reference = {
                "status": "invalid_evidence",
                "formal_market_cap_eligible": False,
                "warning": "발행주식 수 또는 가격 기준일을 검증할 수 없음",
            }
    return {
        **common,
        "status": "research_only",
        "point_in_time_equity_reference": point_in_time_reference,
        "proxy_equity_value_usd_millions": proxy_equity_value,
        "proxy_ttm_fcf_yield": proxy_fcf_yield,
        "proxy_p_fcf": proxy_p_fcf,
        "fy_gaap_eps_reference_multiple": latest_price / float(fy_eps) if fy_eps else None,
        "fy_gaap_eps_period": fiscal_year.get("label"),
        "formulas": {
            "proxy_equity_value": "verified EOD close × latest-quarter diluted weighted-average shares",
            "proxy_fcf_yield": "TTM (CFO - CAPEX) / proxy equity value",
            "fy_eps_reference_multiple": "verified EOD close / FY reported GAAP diluted EPS",
        },
        "fcf_growth_sensitivity": {
            "status": "research_only",
            "horizon_years": 5,
            "rows": sensitivity_rows,
            "assumptions": [
                "Starting FCF is the verified TTM reported CFO minus reported CAPEX",
                "Proxy equity value, price, share count, and capital structure remain unchanged",
                "No dilution, buyback, acquisition, divestiture, or terminal value is modeled",
                "The 0%, 10%, 15%, 20%, and 25% rates are a standard sensitivity grid, not forecasts",
            ],
            "formula": "year-5 FCF = TTM FCF × (1 + scenario CAGR)^5; proxy yield = year-5 FCF / current proxy equity value",
            "operational_eligible": False,
        },
        "interpretation": (
            (f"대용 FCF yield {proxy_fcf_yield:.1%}와 " if proxy_fcf_yield is not None else "대용 FCF yield 미확보와 ")
            + (f"FY GAAP EPS 참고 배수 {latest_price / float(fy_eps):.1f}배는 " if fy_eps else "")
            + "높은 성장과 수익성 지속 기대를 시사합니다. 다만 확정 시가총액·미래 전망·할인율이 "
            "없어 고평가·저평가 또는 적정가를 뜻하지 않습니다."
        ),
    }


def _reverse_dcf_input_readiness(
    *,
    financial: dict[str, Any],
    latest_price: float | None,
    price_as_of: str | None,
    ttm_metrics: dict[str, Any],
    share_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Map verified personal inputs onto PR #2's reverse-DCF contract.

    This adapter deliberately does not select a discount rate or terminal growth
    rate.  PR #2's arithmetic is reusable, but a result is blocked until those
    economic assumptions and a point-in-time fully diluted denominator are
    independently evidenced.
    """

    price_ready = latest_price is not None and latest_price > 0 and bool(price_as_of)
    fcf = ttm_metrics.get("fcf")
    fcf_ready = fcf is not None and float(fcf) > 0
    share_ready = bool(share_evidence and share_evidence.get("value_millions"))
    share_date_gap = None
    if share_ready and price_as_of:
        try:
            share_date_gap = abs(
                (date.fromisoformat(str(price_as_of)) - date.fromisoformat(str(share_evidence["as_of"]))).days
            )
        except (KeyError, TypeError, ValueError):
            share_date_gap = None

    inputs = [
        {
            "id": "target_price",
            "label": "검증 EOD 종가",
            "status": "verified" if price_ready else "missing",
            "value": latest_price if price_ready else None,
            "as_of": price_as_of,
            "reason": None if price_ready else "verified local EOD close is unavailable",
        },
        {
            "id": "starting_cash_flow",
            "label": "TTM CFO-CAPEX 현금흐름 대용치",
            "status": "verified_proxy" if fcf_ready else "missing",
            "value": float(fcf) if fcf_ready else None,
            "unit": "USD millions",
            "reason": (
                "reported CFO minus reported CAPEX; levered proxy, not canonical FCFF or complete FCFE"
                if fcf_ready
                else "verified TTM CFO-minus-CAPEX is unavailable"
            ),
        },
        {
            "id": "share_denominator",
            "label": "기준일 보통주 수",
            "status": "partial" if share_ready else "missing",
            "value": float(share_evidence["value_millions"]) if share_ready else None,
            "unit": "million shares",
            "as_of": share_evidence.get("as_of") if share_evidence else None,
            "date_gap_days": share_date_gap,
            "reason": (
                "official issued common shares are observed, but treasury-stock dilution and later share changes are not resolved"
                if share_ready
                else "official point-in-time common shares are unavailable"
            ),
        },
        {
            "id": "discount_rate",
            "label": "현금흐름 정의와 일치하는 할인율",
            "status": "missing",
            "value": None,
            "reason": "no verified point-in-time cost of equity or WACC evidence is connected",
        },
        {
            "id": "terminal_growth",
            "label": "터미널 성장률",
            "status": "missing",
            "value": None,
            "reason": "no validated long-run growth evidence is connected",
        },
    ]
    formal_fcff = {
        "ttm_operating_income": "available" if ttm_metrics.get("operating_income") is not None else "missing",
        "normalized_operating_tax_rate": "missing",
        "depreciation_and_amortization": "missing",
        "operating_working_capital_change": "missing",
        "capex": "available" if ttm_metrics.get("capex") is not None else "missing",
        "wacc": "missing",
        "non_operating_assets_and_claims_policy": "missing",
        "point_in_time_fully_diluted_shares": "missing",
    }
    missing_ids = [row["id"] for row in inputs if row["status"] in {"missing", "partial"}]
    return {
        "status": "underdetermined",
        "numeric_result": None,
        "operational_eligible": False,
        "reused_contract": "PR #2 stocklens.dcf.solve_implied_fcf_growth / equity_fcf_schedule",
        "reuse_assessment": "arithmetic_reusable_inputs_not_operationally_complete",
        "cash_flow_classification": "levered_equity_cash_flow_proxy_not_canonical_fcfe",
        "inputs": inputs,
        "missing_or_partial_inputs": missing_ids,
        "formal_fcff_input_map": formal_fcff,
        "reason": (
            "No formal implied growth rate is solved: discount rate, terminal growth, and a point-in-time fully "
            "diluted share denominator are not verified. A separate explicitly assumed research grid may still "
            "be shown. Formal FCFF additionally lacks tax, D&A, working-capital, WACC, and EV-to-common-equity "
            "bridge evidence."
        ),
    }


def _conditional_research_valuation(
    *,
    financial: dict[str, Any],
    latest_price: float | None,
    price_as_of: str | None,
    ttm_metrics: dict[str, Any],
    share_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run a deterministic, non-operational equity cash-flow stress grid.

    The grid reuses PR #2's audited arithmetic but does not claim that any of its
    growth, discount, or terminal assumptions are forecasts. Issued common shares
    are an observed but incomplete denominator, so every output remains research
    only and cannot flow into a recommendation.
    """

    base_fcf = ttm_metrics.get("fcf")
    issued_shares = (share_evidence or {}).get("value_millions")
    if latest_price is None or latest_price <= 0:
        return {
            "status": "unavailable",
            "reason": "verified local EOD close is required",
            "operational_eligible": False,
        }
    if base_fcf is None or float(base_fcf) <= 0:
        return {
            "status": "unavailable",
            "reason": "positive verified TTM CFO-minus-CAPEX is required",
            "operational_eligible": False,
        }
    if issued_shares is None or float(issued_shares) <= 0:
        return {
            "status": "unavailable",
            "reason": "official point-in-time common shares outstanding are required",
            "operational_eligible": False,
        }

    base_fcf = float(base_fcf)
    issued_shares = float(issued_shares)
    scenario_specs = [
        {
            "id": "low_growth_high_discount",
            "label": "저성장·고할인 스트레스 조건",
            "growth_rate": 0.00,
            "discount_rate": 0.13,
            "terminal_growth": 0.02,
        },
        {
            "id": "middle_grid_not_base_case",
            "label": "중간 스트레스 격자(공식 기본값 아님)",
            "growth_rate": 0.10,
            "discount_rate": 0.11,
            "terminal_growth": 0.025,
        },
        {
            "id": "high_growth_low_discount",
            "label": "고성장·저할인 스트레스 조건",
            "growth_rate": 0.20,
            "discount_rate": 0.09,
            "terminal_growth": 0.03,
        },
    ]
    scenarios: list[dict[str, Any]] = []
    for spec in scenario_specs:
        result = equity_fcf_schedule(
            base_fcf=base_fcf,
            growth_rate=spec["growth_rate"],
            discount_rate=spec["discount_rate"],
            terminal_growth=spec["terminal_growth"],
            diluted_shares=issued_shares,
            years=5,
        )
        scenarios.append(
            {
                **spec,
                "year5_fcf_usd_millions": result["schedule"][-1]["cash_flow"],
                "explicit_present_value_usd_millions": result["explicit_present_value"],
                "terminal_present_value_usd_millions": result["terminal_present_value"],
                "terminal_value_share": result["terminal_value_share"],
                "research_equity_value_usd_millions": result["equity_value"],
                "research_per_issued_share": result["per_share_value"],
            }
        )

    implied_specs = [
        {"label": "고할인 조건", "discount_rate": 0.13, "terminal_growth": 0.02},
        {"label": "중간 격자", "discount_rate": 0.11, "terminal_growth": 0.025},
        {"label": "저할인 조건", "discount_rate": 0.09, "terminal_growth": 0.03},
    ]
    implied_rows: list[dict[str, Any]] = []
    for spec in implied_specs:
        solved = solve_implied_fcf_growth(
            target_price=float(latest_price),
            base_fcf=base_fcf,
            diluted_shares=issued_shares,
            discount_rate=spec["discount_rate"],
            terminal_growth=spec["terminal_growth"],
            years=5,
        )
        implied_rows.append({**spec, **solved})

    def per_share(growth: float, discount: float, terminal: float) -> float:
        return float(
            equity_fcf_schedule(
                base_fcf=base_fcf,
                growth_rate=growth,
                discount_rate=discount,
                terminal_growth=terminal,
                diluted_shares=issued_shares,
                years=5,
            )["per_share_value"]
        )

    driver_rows = [
        {
            "driver": "5년 현금흐름 성장률",
            "low_assumption": 0.00,
            "high_assumption": 0.20,
            "per_share_low": per_share(0.00, 0.11, 0.025),
            "per_share_high": per_share(0.20, 0.11, 0.025),
        },
        {
            "driver": "할인율",
            "low_assumption": 0.09,
            "high_assumption": 0.13,
            "per_share_low": per_share(0.10, 0.13, 0.025),
            "per_share_high": per_share(0.10, 0.09, 0.025),
        },
        {
            "driver": "터미널 성장률",
            "low_assumption": 0.02,
            "high_assumption": 0.03,
            "per_share_low": per_share(0.10, 0.11, 0.02),
            "per_share_high": per_share(0.10, 0.11, 0.03),
        },
    ]
    for row in driver_rows:
        row["per_share_impact"] = row["per_share_high"] - row["per_share_low"]
    driver_rows.sort(key=lambda row: abs(row["per_share_impact"]), reverse=True)

    solved_growths = [
        row["implied_fcf_growth"] for row in implied_rows if row.get("status") == "solved"
    ]
    values = [row["research_per_issued_share"] for row in scenarios]
    share_date = str((share_evidence or {}).get("as_of") or "")
    date_gap_days = None
    try:
        if price_as_of and share_date:
            date_gap_days = abs((date.fromisoformat(price_as_of) - date.fromisoformat(share_date)).days)
    except ValueError:
        date_gap_days = None

    return {
        "status": "research_only",
        "grade": "dcf_stress_reference",
        "operational_eligible": False,
        "is_fair_value": False,
        "is_target_price": False,
        "is_buy_range": False,
        "is_normal_interest_range": False,
        "is_validated_crisis_range": False,
        "range_label": "DCF 스트레스 참고 범위",
        "ticker": financial.get("ticker"),
        "model_version": "levered_proxy_stress_grid_v2",
        "reused_engine": "PR #2 stocklens.dcf equity_fcf_schedule / solve_implied_fcf_growth",
        "cash_flow_definition": "reported TTM CFO minus reported CAPEX",
        "cash_flow_classification": "levered_equity_cash_flow_proxy_not_canonical_fcfe",
        "base_fcf_usd_millions": base_fcf,
        "price": float(latest_price),
        "price_as_of": price_as_of,
        "issued_common_shares_millions": issued_shares,
        "shares_as_of": share_date or None,
        "date_gap_days": date_gap_days,
        "share_denominator_status": "observed_issued_common_not_fully_diluted",
        "scenario_values": scenarios,
        "research_per_share_outer_range": {"low": min(values), "high": max(values)},
        "selected_base_value": None,
        "implied_growth_sensitivity": implied_rows,
        "implied_growth_range": (
            {"low": min(solved_growths), "high": max(solved_growths)} if solved_growths else None
        ),
        "driver_sensitivity": driver_rows,
        "assumption_policy": {
            "growth_rates": [0.00, 0.10, 0.20],
            "discount_rates": [0.09, 0.11, 0.13],
            "terminal_growth_rates": [0.02, 0.025, 0.03],
            "selection_rule": (
                "fixed deterministic stress grid; no row is a forecast, consensus, normal fair value, or official base case"
            ),
            "source_status": "explicit_research_assumptions_not_external_forecasts",
        },
        "limitations": [
            "발행 보통주 수는 완전희석 주식 수가 아니며 가격일과 기준일이 다름",
            "CFO-CAPEX는 정식 FCFF 또는 완전한 FCFE가 아님",
            "할인율·성장률·터미널 성장률은 외부 전망이 아닌 연구 스트레스 격자",
            "순현금·부채·비영업자산을 더하거나 빼는 EV-to-equity 브리지를 적용하지 않음",
            "결과는 목표가격·매수구간·수익률 예측이 아님",
            "역사적 도달 빈도를 검증하지 않아 위기 스트레스 범위로도 확정하지 않음",
        ],
    }


def _evidence_axis(
    *,
    source: Any,
    source_hash: Any,
    as_of: Any,
    available_at: Any,
    unit: Any,
    calculation_basis: str,
    status: str,
    model_version: str,
    calculation_conditions: list[str],
    missing_behavior: str,
    lower_bound_basis: str,
    upper_bound_basis: str,
    backtest_criteria: list[str],
    evidence: Any = None,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    """Return the common non-operational contract for one valuation axis."""

    return {
        "source": source,
        "source_hash": source_hash,
        "as_of": as_of,
        "available_at": available_at,
        "unit": unit,
        "calculation_basis": calculation_basis,
        "status": status,
        "operational_eligible": False,
        "model_version": model_version,
        "eligibility_policy": {
            "calculation_conditions": calculation_conditions,
            "missing_behavior": missing_behavior,
            "lower_bound_basis": lower_bound_basis,
            "upper_bound_basis": upper_bound_basis,
            "backtest_criteria": backtest_criteria,
        },
        "evidence": evidence,
        "missing": missing or [],
    }


def _valuation_evidence_contract(
    *,
    financial: dict[str, Any],
    market_manifest: dict[str, Any],
    fundamental: dict[str, Any],
    valuation: dict[str, Any],
) -> dict[str, Any]:
    """Build four independent valuation-evidence axes without filling gaps.

    The contract deliberately keeps unavailable history and peer axes empty. It
    never turns missing evidence into a lower multiple, higher discount rate, or
    zero-valued input.
    """

    ticker = financial.get("ticker")
    latest_document = _latest_document(financial)
    ttm = _period(financial, "ttm") or {}
    metrics = fundamental.get("metrics", {})
    guidance = fundamental.get("guidance_analysis", {})
    share_evidence = PERSONAL_PILOT_UNIVERSE.get(str(ticker), {}).get("share_count_evidence")
    conditional = valuation.get("conditional_research_valuation", {})

    self_history = _evidence_axis(
        source=None,
        source_hash=None,
        as_of=None,
        available_at=None,
        unit="valuation multiple and percentile",
        calculation_basis=(
            "point-in-time price divided by EPS or cash flow available at that timestamp; "
            "corporate actions and acceptance time required"
        ),
        status="unavailable",
        model_version="self_history_contract_v2",
        calculation_conditions=[
            "rights-cleared point-in-time price observations spanning at least five fiscal years",
            "each observation joined only to EPS or cash flow available by its acceptance timestamp",
            "split, dividend, restatement, and one-time-item policies fixed before percentile calculation",
            "at least 20 non-duplicated fiscal-event or quarter-end observations after exclusions",
        ],
        missing_behavior=(
            "return null/unavailable; do not lower a multiple, raise a discount rate, repeat values, or interpolate gaps"
        ),
        lower_bound_basis=(
            "out-of-sample calibrated lower percentile of the eligible point-in-time distribution; not its minimum"
        ),
        upper_bound_basis=(
            "out-of-sample calibrated upper percentile of the eligible point-in-time distribution; not its maximum"
        ),
        backtest_criteria=[
            "walk-forward vintages with filing availability and corporate actions frozen at each observation date",
            "selection and final holdout periods separated",
            "coverage, calibration error, and later returns reported without treating later return as fair-value truth",
        ],
        missing=[
            "rights-cleared point-in-time price ledger",
            "acceptance-timestamped EPS/FCF ledger",
            "validated 5-year and 10-year distributions",
        ],
    )
    peer_relative = _evidence_axis(
        source=None,
        source_hash=None,
        as_of=None,
        available_at=None,
        unit="relative valuation multiple",
        calculation_basis=(
            "peer median adjusted only by validated growth, margin, capital-efficiency, cash-flow conversion, "
            "leverage, dilution, and cyclicality evidence"
        ),
        status="unavailable",
        model_version="peer_relative_contract_v2",
        calculation_conditions=[
            "peer set fixed by documented business and accounting criteria before seeing valuation outcomes",
            "at least five eligible peers with same-date price and point-in-time financial snapshots",
            "accounting basis, fiscal period, currency, share class, and cyclicality normalized or explicitly excluded",
            "premium or discount coefficients validated outside their estimation sample",
        ],
        missing_behavior=(
            "return null/unavailable; do not substitute a broad-sector multiple or apply an unsupported discount"
        ),
        lower_bound_basis=(
            "validated peer-distribution interval after predeclared comparability adjustments; never the cheapest peer"
        ),
        upper_bound_basis=(
            "validated peer-distribution interval after predeclared comparability adjustments; never the richest peer"
        ),
        backtest_criteria=[
            "peer membership and coefficients selected without final-period outcomes",
            "walk-forward comparison against later realized fundamentals and returns",
            "industry and market-regime stability reported separately from headline accuracy",
        ],
        missing=[
            "same-date peer price and financial snapshots",
            "validated peer membership",
            "out-of-sample premium/discount coefficients",
        ],
    )

    intrinsic_status = "research_only" if conditional.get("status") == "research_only" else "unavailable"
    intrinsic_reverse = _evidence_axis(
        source={
            "financial": latest_document.get("url"),
            "market": market_manifest.get("provider"),
            "shares": (share_evidence or {}).get("source_url"),
        },
        source_hash={
            "financial": latest_document.get("sha256"),
            "market": market_manifest.get("sha256"),
            "shares": None,
        },
        as_of={
            "financial": ttm.get("period_end"),
            "price": market_manifest.get("market_date"),
            "shares": (share_evidence or {}).get("as_of"),
        },
        available_at={
            "financial": latest_document.get("published_at"),
            "price_collected_at": market_manifest.get("collected_at_utc"),
            "shares": (share_evidence or {}).get("published_at"),
        },
        unit={"price": "USD/share", "cash_flow": "USD millions", "shares": "million shares"},
        calculation_basis=(
            "current-price reverse growth and a fixed DCF stress grid using reported TTM CFO minus CAPEX; "
            "not canonical FCFF or complete FCFE"
        ),
        status=intrinsic_status,
        model_version=str(conditional.get("model_version") or "intrinsic_reverse_contract_v2"),
        calculation_conditions=[
            "verified cash-flow definition consistent with its discount rate and equity or enterprise-value bridge",
            "same-vintage price, share denominator, capital structure, and non-operating assets or explicit uncertainty",
            "growth, reinvestment, margin, discount-rate, and terminal assumptions sourced or labeled research-only",
            "normal range requires calibrated forecast uncertainty; a fixed stress grid cannot qualify",
        ],
        missing_behavior=(
            "retain reverse-expectation evidence when solvable, but keep normal value null; no uncertainty penalty is embedded"
        ),
        lower_bound_basis=(
            "forecast-error-calibrated downside case with a complete cash-flow bridge; current stress-grid low is excluded"
        ),
        upper_bound_basis=(
            "forecast-error-calibrated upside case with a complete cash-flow bridge; current stress-grid high is excluded"
        ),
        backtest_criteria=[
            "rolling-origin forecasts use only inputs available at each valuation date",
            "forecast cash flows and margins compared with subsequent reported results before price outcomes",
            "interval coverage, terminal-value dependence, and sensitivity stability pass a separate holdout",
        ],
        evidence={
            "current_market_expectation": conditional.get("implied_growth_sensitivity"),
            "dcf_stress_reference": conditional.get("scenario_values"),
            "terminal_value_share": [
                row.get("terminal_value_share") for row in conditional.get("scenario_values", [])
            ],
            "date_gap_days": conditional.get("date_gap_days"),
            "selected_base_value": None,
            "normal_interest_range": None,
            "validated_crisis_range": None,
        },
        missing=list(conditional.get("limitations", [])),
    )

    net_cash = metrics.get("known_net_cash", {})
    ttm_metrics = ttm.get("metrics", {})
    ttm_cfo = ttm_metrics.get("cfo")
    ttm_fcf = ttm_metrics.get("fcf")
    fcf_conversion = (
        float(ttm_fcf) / float(ttm_cfo)
        if ttm_fcf is not None and ttm_cfo not in {None, 0}
        else None
    )
    quality_rows = [
        {
            "id": "revenue_change",
            "label": "TTM 매출 변화",
            "value": metrics.get("ttm_revenue_change_vs_fy2025", {}).get("value"),
            "status": metrics.get("ttm_revenue_change_vs_fy2025", {}).get("status", "unavailable"),
            "note": "FY 대비 rolling TTM 변화이며 장기 성장률이나 YoY가 아님",
        },
        {
            "id": "operating_margin",
            "label": "TTM 영업마진",
            "value": metrics.get("ttm_operating_margin", {}).get("value"),
            "status": metrics.get("ttm_operating_margin", {}).get("status", "unavailable"),
        },
        {
            "id": "fcf_margin",
            "label": "TTM FCF 마진",
            "value": metrics.get("ttm_fcf_margin", {}).get("value"),
            "status": metrics.get("ttm_fcf_margin", {}).get("status", "unavailable"),
        },
        {
            "id": "fcf_conversion",
            "label": "TTM FCF/CFO 전환",
            "value": fcf_conversion,
            "status": "verified" if fcf_conversion is not None else "unavailable",
            "note": "공시 CFO와 CFO-CAPEX 대용 FCF의 비율이며 정식 FCFF 전환율이 아님",
        },
        {
            "id": "net_cash_or_debt",
            "label": "확인 범위 순현금·순부채",
            "value_usd_millions": net_cash.get("value_usd_millions"),
            "status": net_cash.get("status", "unavailable"),
            "note": net_cash.get("note"),
        },
        {
            "id": "issuer_guidance",
            "label": "회사 공식 가이던스",
            "value": guidance.get("rows") or None,
            "status": guidance.get("status", "unavailable"),
        },
        {
            "id": "share_evidence",
            "label": "발행주식·희석 근거",
            "value": share_evidence,
            "status": "partial" if share_evidence else "unavailable",
            "note": "기준일 발행주식 수이며 완전희석 주식 수가 아님" if share_evidence else None,
        },
        {"id": "roic", "label": "다년 ROIC", "value": None, "status": "unavailable"},
        {"id": "dilution_trend", "label": "다년 희석·자사주 추세", "value": None, "status": "unavailable"},
        {"id": "multi_year_revision", "label": "다년 전망 revision", "value": None, "status": "unavailable"},
        {"id": "recurring_revenue", "label": "반복매출·전환비용", "value": None, "status": "unavailable"},
    ]
    quality_premium = _evidence_axis(
        source={"financial": latest_document.get("url"), "shares": (share_evidence or {}).get("source_url")},
        source_hash={"financial": latest_document.get("sha256"), "shares": None},
        as_of={"financial": ttm.get("period_end"), "shares": (share_evidence or {}).get("as_of")},
        available_at={
            "financial": latest_document.get("published_at"),
            "shares": (share_evidence or {}).get("published_at"),
        },
        unit="mixed evidence; no aggregate premium score",
        calculation_basis="display verified quality drivers independently; do not aggregate into an AI premium",
        status="partial" if fundamental.get("status") in {"verified", "partial"} else "unavailable",
        model_version="quality_premium_evidence_v2",
        calculation_conditions=[
            "at least six of eight quality dimensions observed with source and availability timestamps",
            "ROIC, FCF conversion, balance-sheet resilience, and dilution evidence are mandatory",
            "revenue growth, margins, recurring revenue or switching costs, and revisions cover comparable periods",
            "any premium mapping is estimated and validated outside the current company sample",
        ],
        missing_behavior=(
            "show each observed driver and leave the premium null; missing quality evidence never becomes a discount"
        ),
        lower_bound_basis=(
            "no lower premium bound until out-of-sample evidence maps quality drivers to valuation dispersion"
        ),
        upper_bound_basis=(
            "no upper premium bound until out-of-sample evidence maps quality drivers to valuation dispersion"
        ),
        backtest_criteria=[
            "quality observations frozen at each filing date and tested on later fundamentals and valuation dispersion",
            "coefficients estimated on a training universe and assessed on company and time holdouts",
            "calibration and stability reported without creating an opaque aggregate AI score",
        ],
        evidence=quality_rows,
        missing=[row["label"] for row in quality_rows if row.get("status") == "unavailable"],
    )

    return {
        "model_version": "valuation_evidence_contract_v2",
        "operational_eligible": False,
        "axes": {
            "self_history": self_history,
            "peer_relative": peer_relative,
            "intrinsic_reverse": intrinsic_reverse,
            "quality_premium": quality_premium,
        },
        "map": {
            "normal_interest_range": None,
            "high_expectation_range": None,
            "validated_crisis_stress_range": None,
            "dcf_stress_candidate": conditional.get("research_per_share_outer_range"),
            "status": "research_only" if intrinsic_status == "research_only" else "unavailable",
            "reason": (
                "자기 역사와 피어 상대가치가 미확보되어 정상 관심 범위를 산출하지 않았습니다. "
                "DCF 격자는 역사적 도달 빈도 검증 전 스트레스 후보일 뿐입니다."
            ),
        },
        "normal_interest_policy": {
            "required_axes": {
                "self_history": "verified",
                "peer_relative": "verified",
                "intrinsic_reverse": "verified",
                "quality_premium": "verified",
            },
            "combination_rule": (
                "use only an out-of-sample calibrated interval method fixed before the final holdout; "
                "simple mean, lowest value, and unsupported confidence weighting are prohibited"
            ),
            "missing_behavior": (
                "normal_interest_range remains null; current market expectation and observed quality evidence remain visible"
            ),
            "range_separation": (
                "normal interest, high-expectation, and validated crisis-stress ranges are distinct outputs and cannot substitute"
            ),
            "backtest_gate": [
                "point-in-time inputs with no future revisions",
                "walk-forward model selection and untouched final holdout",
                "interval coverage and regime stability reported",
                "no operational eligibility until data rights and reproducibility gates pass",
            ],
        },
    }
def _investment_questions(
    financial: dict[str, Any],
    fundamental: dict[str, Any],
    valuation: dict[str, Any],
    technical: dict[str, Any],
    relative: dict[str, Any],
    technical_label: str,
) -> dict[str, dict[str, Any]]:
    """Answer practical investor questions without promoting research inputs."""

    metrics = fundamental.get("metrics", {})
    revenue_change = metrics.get("ttm_revenue_change_vs_fy2025", {}).get("value")
    operating_change = metrics.get("ttm_operating_income_change_vs_fy2025", {}).get("value")
    fcf_change = metrics.get("ttm_fcf_change_vs_fy2025", {}).get("value")
    margin_change_bps = metrics.get("ttm_operating_margin_change_bps_vs_fy2025", {}).get("value")
    guidance_midpoint = fundamental.get("guidance_analysis", {}).get("comparison", {}).get("midpoint")

    performance_parts: list[str] = []
    if revenue_change is not None:
        performance_parts.append(f"TTM 매출 {revenue_change:+.1%}")
    if operating_change is not None:
        performance_parts.append(f"영업이익 {operating_change:+.1%}")
    if fcf_change is not None:
        performance_parts.append(f"FCF {fcf_change:+.1%}")
    if margin_change_bps is not None:
        performance_parts.append(f"영업마진 {margin_change_bps:+.0f}bp")
    if guidance_midpoint is not None:
        performance_parts.append(f"다음 분기 매출 가이던스 중간값/직전 분기 {guidance_midpoint:+.1%}")

    sensitivity = valuation.get("fcf_growth_sensitivity", {})
    row_15 = next((row for row in sensitivity.get("rows", []) if row.get("fcf_cagr") == 0.15), None)
    conditional = valuation.get("conditional_research_valuation", {})
    implied_range = conditional.get("implied_growth_range")
    if conditional.get("status") == "research_only" and implied_range:
        expectation_answer = (
            f"현재 가격을 재현하려면 연구 할인·터미널 격자에서 5년 대용 FCF CAGR "
            f"{implied_range['low']:.1%}~{implied_range['high']:.1%}가 필요합니다. "
            "이는 시장 컨센서스나 미래 전망이 아니라 조건부 역산이며 적정가 판단이 아닙니다."
        )
    elif valuation.get("status") == "research_only" and valuation.get("proxy_p_fcf") is not None:
        expectation_answer = (
            f"현재 대용 P/FCF는 {valuation['proxy_p_fcf']:.1f}배입니다. 가격·주식 수가 변하지 않고 FCF가 "
            f"5년간 연 15% 성장하는 연구 시나리오에서도 5년 후 대용 FCF yield는 "
            f"{row_15['year5_proxy_fcf_yield']:.1%}입니다. 높은 성장 의존도는 확인되지만 적정가 판단은 아닙니다."
            if row_15
            else "대용 가치 참고치는 있으나 미래 현금흐름 민감도를 계산할 수 없습니다."
        )
    else:
        expectation_answer = "검증된 가격과 주식 수 연결이 부족해 현재 가격의 성장 기대를 정량화하지 않았습니다."

    if technical.get("status") == "unavailable" or any(
        technical.get(key) is None for key in ("latest_close", "ema20", "ema50", "rsi14")
    ):
        technical_answer = "검증된 가격 이력이 없어 추세를 판단하지 않았습니다."
    else:
        relative_63d = relative.get("relative_return_63d")
        technical_answer = (
            f"기술 상태는 {technical_label}입니다. 종가 {technical.get('latest_close'):.2f}, "
            f"EMA20 {technical.get('ema20'):.2f}, EMA50 {technical.get('ema50'):.2f}, "
            f"RSI14 {technical.get('rsi14'):.1f}"
            + (f", 63거래일 QQQ 대비 {relative_63d:+.1%}p입니다." if relative_63d is not None else "입니다.")
        )

    watch = _practical_summary(
        financial, fundamental, valuation, technical, technical_label
    )["watch_conditions"]
    return {
        "performance": {
            "question": "실적은 개선되고 있는가?",
            "status": "verified_calculation" if performance_parts else "unavailable",
            "answer": " · ".join(performance_parts) if performance_parts else "비교 가능한 FY·TTM 실적이 부족합니다.",
            "warning": "TTM 대 FY 변화이며 전년동기 성장률이 아닙니다.",
        },
        "growth_expectation": {
            "question": "현재 가격에는 높은 성장이 요구되는가?",
            "status": "research_only" if valuation.get("status") == "research_only" else "unavailable",
            "answer": expectation_answer,
        },
        "technical": {
            "question": "주가 추세가 개선되는가, 약해지는가?",
            "status": "descriptive_only" if technical.get("status") != "unavailable" else "unavailable",
            "answer": technical_answer,
        },
        "watch": {
            "question": "다음에 무엇을 확인해야 하는가?",
            "status": "research_only",
            "items": watch,
        },
        "unknowns": {
            "question": "현재 판단할 수 없는 부분은 무엇인가?",
            "status": "hold",
            "items": [
                "동일 기준일의 보통주 발행주식 수와 완전희석 주식 수",
                "일관된 TTM 희석 EPS와 검증된 미래 EPS·매출 컨센서스",
                "검증된 할인율·장기 성장률에 기반한 정식 적정가",
                "충분한 조정가격 이력과 독립 검증된 운영 매수 신호",
            ],
        },
    }


def _practical_summary(
    financial: dict[str, Any],
    fundamental: dict[str, Any],
    valuation: dict[str, Any],
    technical: dict[str, Any],
    technical_label: str,
) -> dict[str, Any]:
    ticker = financial["ticker"]
    metrics = fundamental.get("metrics", {})
    revenue_change = metrics.get("ttm_revenue_change_vs_fy2025", {}).get("value")
    operating_margin = metrics.get("ttm_operating_margin", {}).get("value")
    fcf_margin = metrics.get("ttm_fcf_margin", {}).get("value")
    guidance = fundamental.get("guidance_analysis", {})
    comparison = guidance.get("comparison", {})
    strengths: list[str] = []
    if revenue_change is not None:
        strengths.append(f"TTM 매출은 FY2025 대비 {revenue_change:+.1%} 변화했습니다(전년동기 성장률 아님).")
    if operating_margin is not None:
        strengths.append(f"TTM 영업이익률은 {operating_margin:.1%}입니다.")
    if fcf_margin is not None:
        strengths.append(f"TTM CFO-CAPEX 마진은 {fcf_margin:.1%}입니다.")
    if comparison.get("midpoint") is not None:
        strengths.append(
            f"공식 다음 분기 매출 가이던스 중간값은 직전 보고 분기보다 {comparison['midpoint']:+.1%} 높습니다."
        )

    expectations = (
        valuation.get("interpretation")
        if valuation.get("status") == "research_only"
        else "검증된 가격이 없어 현재 가격에 반영된 기대를 정량 평가할 수 없습니다."
    )
    unknowns = [
        "동일 기준일의 보통주 발행주식 수와 완전희석 주식 수",
        "일관된 TTM 희석 EPS",
        "검증된 미래 EPS·매출 컨센서스와 자본비용",
    ]
    if technical.get("status") == "unavailable":
        unknowns.append("검증된 로컬 가격 이력과 기술적 위치")

    watch_by_ticker = {
        "AMAT": ["Q4 FY2026 매출·Non-GAAP EPS가 회사 가이던스 범위에 들어오는지", "AI·반도체 설비투자 사이클과 중국 규제 영향", "CFO-CAPEX 마진이 유지되는지"],
        "AVGO": ["Q4 FY2026 매출과 Non-GAAP 영업이익률 가이던스 달성 여부", "AI 고객 집중과 VMware 통합 현금흐름", "부채 축소와 이자 부담"],
        "QCOM": ["Q4 FY2026 매출·GAAP EPS 가이던스 달성 여부", "스마트폰 외 자동차·IoT·PC 성장", "투자손익·세금효과가 GAAP EPS에 미치는 영향"],
        "GOOGL": ["2026년 CAPEX 가이던스와 AI 투자 회수 속도", "광고·Cloud 영업현금흐름", "투자증권 평가손익을 제외한 이익의 질"],
        "NVDA": ["Q3 FY2027 공식 매출 가이던스 달성 여부", "AI 고객 집중과 자체 칩 전환", "FY 원문 확보 후 TTM 현금흐름 완성"],
        "MSFT": ["Azure·AI 매출 성장과 데이터센터 CAPEX 회수", "클라우드 영업마진", "규제·사이버보안 비용"],
        "MU": ["메모리 가격과 HBM 수요", "설비투자와 공급 증가", "공식 원문 연결 후 TTM 현금흐름"],
        "AMD": ["데이터센터·AI 가속기 매출 성장", "파운드리 공급과 제품 전환", "공식 수치로 정규화한 다음 분기 가이던스"],
        "TSM": ["첨단공정 수요와 해외 팹 비용", "TWD·ADR 변환 기준", "TIFRS 원문 접근과 통화 일관성"],
        "LRCX": ["Q1 FY2027 GAAP 가이던스 달성 여부", "메모리·파운드리 설비투자 사이클", "중국 수출규제와 설치 기반 서비스"],
    }
    risk = COMPANY_PROFILES[ticker]["risks"][0]
    performance_label = "긍정" if revenue_change is not None and revenue_change > 0 and (operating_margin or 0) > 0.20 else "중립"
    return {
        "one_line": f"사업 중립 · 실적 {performance_label} · 가치 판단 부족 · 기술 {technical_label} · 핵심 위험: {risk}",
        "strengths": strengths,
        "embedded_expectations": expectations,
        "unknowns": unknowns,
        "watch_conditions": watch_by_ticker[ticker],
        "price_comparison_rule": "가격은 동일 거래일·정규장 종가·조정 정책이 일치할 때만 직접 비교",
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


def _future_estimate_references(
    estimates: dict[str, Any] | None,
    latest_price: float | None,
    price_date: str | None,
) -> dict[str, Any]:
    if not estimates:
        return {
            "status": "unavailable",
            "reason": (
                "No ticker-level response was collected: a local API key is not configured, and free-plan "
                "entitlement, cache rights, and response fields were not validated"
            ),
            "documented_candidate": "Alpha Vantage EARNINGS_ESTIMATES",
            "candidate_scope": "annual/quarterly EPS and revenue estimates, analyst count, and revision history",
            "access_and_reuse_validated": False,
            "source_url": "https://www.alphavantage.co/documentation/#earnings-estimates",
            "operational_eligible": False,
        }
    periods = estimates.get("periods") if isinstance(estimates.get("periods"), list) else []
    annual = sorted(
        (row for row in periods if row.get("frequency") == "annual"),
        key=lambda row: str(row.get("fiscal_date_ending", "")),
    )
    eligible_annual = [
        row
        for row in annual
        if row.get("eps", {}).get("average") is not None
        and (row.get("eps", {}).get("analyst_count") or 0) > 0
    ]
    next_annual = next(
        (row for row in eligible_annual if price_date and str(row.get("fiscal_date_ending", "")) >= price_date),
        None,
    )
    forward_per = None
    if latest_price is not None and next_annual:
        eps_average = float(next_annual["eps"]["average"])
        if eps_average > 0:
            forward_per = latest_price / eps_average
    return {
        "status": "research_only",
        "provider": estimates.get("provider"),
        "dataset": estimates.get("dataset"),
        "collected_at_utc": estimates.get("collected_at_utc"),
        "provider_snapshot_time": estimates.get("provider_snapshot_time"),
        "provider_snapshot_time_status": estimates.get("provider_snapshot_time_status"),
        "periods": periods,
        "period_count": len(periods),
        "next_annual": next_annual,
        "forward_per_reference": forward_per,
        "formula": "verified local EOD close / analyst-consensus average EPS for the selected annual fiscal period",
        "issuer_guidance_mixed": False,
        "operational_eligible": False,
        "reason": "Consensus snapshot timing and historical revision reproducibility are not sufficient for an operating valuation",
        "source": estimates.get("source", {}),
    }


def compute_personal_analysis(
    financial: dict[str, Any],
    market: pd.DataFrame | None = None,
    benchmark: pd.DataFrame | None = None,
    market_manifest: dict[str, Any] | None = None,
    estimates: dict[str, Any] | None = None,
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
    valuation_references = _research_valuation_references(financial, latest_price, manifest.get("market_date"))
    valuation_evidence = _valuation_evidence_contract(
        financial=financial,
        market_manifest=manifest,
        fundamental=fundamental,
        valuation=valuation_references,
    )
    future_estimates = _future_estimate_references(estimates, latest_price, manifest.get("market_date"))
    technical_interpretation = _technical_interpretation(technical, relative)
    performance_label = "판단 부족"
    metrics = fundamental.get("metrics", {})
    revenue_change = metrics.get("ttm_revenue_change_vs_fy2025", {}).get("value")
    operating_margin = metrics.get("ttm_operating_margin", {}).get("value")
    if revenue_change is not None and operating_margin is not None:
        performance_label = "긍정" if revenue_change > 0 and operating_margin > 0.20 else "중립"
    practical_summary = _practical_summary(
        financial, fundamental, valuation_references, technical, technical_interpretation["label"]
    )
    investment_questions = _investment_questions(
        financial,
        fundamental,
        valuation_references,
        technical,
        relative,
        technical_interpretation["label"],
    )
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
        "valuation_evidence": valuation_evidence,
        "future_estimates": future_estimates,
        "security_structure": {
            "point_in_time_share_evidence": PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("share_count_evidence"),
            "share_classes": PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("share_classes"),
            "adr_evidence": PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("adr_evidence"),
            "corporate_actions": PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("corporate_action_evidence", []),
        },
        "practical_summary": practical_summary,
        "investment_questions": investment_questions,
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
            "reason": (
                f"{len(market_frame)}거래일 원시 비조정 이력은 EMA200과 독립 전략 검증에 부족합니다."
                if len(market_frame)
                else "검증된 가격 이력이 없어 기술 전략을 계산하지 않았습니다."
            ),
        },
        "decision": {
            "status": "hold",
            "is_buy_signal": False,
            "reason": "Technical indicators are descriptive; fair value and independently validated signals are unavailable",
        },
    }
