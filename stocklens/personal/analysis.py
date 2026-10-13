from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

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
    common = {
        "operational_eligible": False,
        "share_basis": "latest-quarter diluted weighted-average shares; not point-in-time shares outstanding",
        "share_basis_period": quarter.get("label"),
        "share_basis_as_of": quarter.get("period_end"),
        "share_source_url": latest_document.get("url"),
        "share_source_published_at": latest_document.get("published_at"),
        "diluted_share_proxy_millions": float(diluted_share_proxy),
        "formal_market_cap": None,
        "formal_per": None,
        "fair_value": None,
        "point_in_time_share_evidence": share_evidence,
        "point_in_time_equity_reference": None,
    }
    if latest_price is None or latest_price <= 0:
        return {
            **common,
            "status": "unavailable",
            "reason": "A verified local closing price is required",
        }
    proxy_equity_value = latest_price * float(diluted_share_proxy)
    proxy_fcf_yield = float(ttm_fcf) / proxy_equity_value if ttm_fcf is not None else None
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
        "fy_gaap_eps_reference_multiple": latest_price / float(fy_eps) if fy_eps else None,
        "fy_gaap_eps_period": fiscal_year.get("label"),
        "formulas": {
            "proxy_equity_value": "verified EOD close × latest-quarter diluted weighted-average shares",
            "proxy_fcf_yield": "TTM (CFO - CAPEX) / proxy equity value",
            "fy_eps_reference_multiple": "verified EOD close / FY reported GAAP diluted EPS",
        },
        "reverse_dcf": {
            "status": "underdetermined",
            "reason": (
                "Verified forecast cash flows, WACC, terminal assumptions, and point-in-time "
                "diluted shares are unavailable; no implied growth rate is solved"
            ),
        },
        "interpretation": (
            (f"대용 FCF yield {proxy_fcf_yield:.1%}와 " if proxy_fcf_yield is not None else "대용 FCF yield 미확보와 ")
            + (f"FY GAAP EPS 참고 배수 {latest_price / float(fy_eps):.1f}배는 " if fy_eps else "")
            + "높은 성장과 수익성 지속 기대를 시사합니다. 다만 확정 시가총액·미래 전망·할인율이 "
            "없어 고평가·저평가 또는 적정가를 뜻하지 않습니다."
        ),
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
        "future_estimates": future_estimates,
        "security_structure": {
            "point_in_time_share_evidence": PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("share_count_evidence"),
            "share_classes": PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("share_classes"),
            "adr_evidence": PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("adr_evidence"),
            "corporate_actions": PERSONAL_PILOT_UNIVERSE.get(financial["ticker"], {}).get("corporate_action_evidence", []),
        },
        "practical_summary": practical_summary,
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
