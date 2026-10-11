from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .financials import FinancialPeriod


MODEL_VERSION = "valuation-v2.0.0"
EQUITY_RISK_PREMIUM = 0.05

SECTOR_PE = {
    "Technology": 22.0,
    "Communication Services": 20.0,
    "Consumer Cyclical": 19.0,
    "Consumer Defensive": 19.0,
    "Healthcare": 20.0,
    "Industrials": 18.0,
    "Energy": 13.0,
    "Utilities": 16.0,
    "Basic Materials": 15.0,
}
SECTOR_EV_EBITDA = {
    "Technology": 16.0,
    "Communication Services": 14.0,
    "Consumer Cyclical": 13.0,
    "Consumer Defensive": 13.0,
    "Healthcare": 14.0,
    "Industrials": 12.0,
    "Energy": 8.0,
    "Utilities": 10.0,
    "Basic Materials": 9.0,
}
FINANCIAL_SECTORS = {"Financial Services", "Financials"}
REIT_INDUSTRY_WORDS = {"reit", "real estate investment trust"}


@dataclass(frozen=True)
class ModelResult:
    name: str
    available: bool
    conservative: float | None
    base: float | None
    optimistic: float | None
    weight: float
    inputs: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class ValuationResult:
    ticker: str
    company_name: str
    model_version: str
    evaluated_at_utc: str
    price: float
    price_as_of_utc: str
    financials_as_of_utc: str | None
    conservative_value: float | None
    base_value: float | None
    optimistic_value: float | None
    upside_to_base: float | None
    safety_margin: float | None
    favorable_buy_low: float | None
    favorable_buy_high: float | None
    judgement: str
    confidence_score: int
    uncertainty: str
    valuation_family: str
    methods_used: str
    model_details_json: str
    growth_drivers: str
    risk_factors: str
    eps_revision_trend: str
    prior_base_value: float | None = None
    base_value_change: float | None = None
    change_reason: str = "first recorded evaluation"
    status: str = "hold"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(clean) if clean else None


def _cagr(first: float | None, last: float | None, years: int) -> float | None:
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _family(ticker: str, sector: str, industry: str) -> str:
    lower_industry = industry.lower()
    if any(word in lower_industry for word in REIT_INDUSTRY_WORDS):
        return "reit"
    if sector in FINANCIAL_SECTORS:
        return "financial"
    if ticker == "AMAT" or any(word in lower_industry for word in ("semiconductor equipment", "steel", "mining")):
        return "cyclical"
    if ticker == "AVGO":
        return "acquisitive"
    return "general"


def _tax_rate(periods: list[FinancialPeriod]) -> float:
    rates = []
    for period in periods:
        if period.pretax_income and period.pretax_income > 0 and period.income_tax is not None:
            rates.append(period.income_tax / period.pretax_income)
    rate = _median(rates)
    return _clamp(rate if rate is not None else 0.21, 0.10, 0.30)


def _normalized_metrics(periods: list[FinancialPeriod], family: str) -> dict[str, float | None]:
    periods = sorted(periods, key=lambda row: (row.period_end, row.filed_at))
    window = periods[-5:] if family == "cyclical" else periods[-3:]
    tax = _tax_rate(periods[-5:])
    eps = _median(row.diluted_eps for row in window if row.diluted_eps is not None and row.diluted_eps > 0)
    fcf = _median(row.fcf for row in window if row.fcf is not None and row.fcf > 0)
    shares = _median(row.diluted_shares for row in window if row.diluted_shares and row.diluted_shares > 0)
    operating = _median(row.operating_income for row in window if row.operating_income is not None)
    da = _median(row.depreciation_amortization for row in window if row.depreciation_amortization is not None)
    core_eps = operating * (1 - tax) / shares if operating is not None and operating > 0 and shares else None
    revenue_growth = None
    if len(periods) >= 3:
        usable = [row for row in periods[-5:] if row.revenue and row.revenue > 0]
        if len(usable) >= 2:
            revenue_growth = _cagr(usable[0].revenue, usable[-1].revenue, len(usable) - 1)
    fcf_growth = None
    usable_fcf = [row for row in periods[-5:] if row.fcf and row.fcf > 0]
    if len(usable_fcf) >= 2:
        fcf_growth = _cagr(usable_fcf[0].fcf, usable_fcf[-1].fcf, len(usable_fcf) - 1)
    margins = [row.operating_income / row.revenue for row in window if row.operating_income is not None and row.revenue]
    return {
        "eps": eps,
        "core_eps": core_eps,
        "fcf": fcf,
        "shares": shares,
        "ebitda": operating + da if operating is not None and da is not None else None,
        "revenue_growth": revenue_growth,
        "fcf_growth": fcf_growth,
        "operating_margin": _median(margins),
        "tax_rate": tax,
        "cash": periods[-1].cash,
        "debt": periods[-1].total_debt,
        "equity": periods[-1].equity,
        "net_income": periods[-1].net_income,
    }


def _growth_rate(metrics: dict[str, float | None], family: str) -> float:
    candidates = [metrics.get("revenue_growth"), metrics.get("fcf_growth")]
    growth = _median(candidates)
    if growth is None:
        growth = 0.0
    high = 0.06 if family == "cyclical" else 0.15
    return _clamp(growth, -0.05, high)


def _earnings_model(
    ticker: str,
    sector: str,
    metrics: dict[str, float | None],
    family: str,
    discount_rate: float,
) -> ModelResult:
    eps = metrics.get("eps")
    core_eps = metrics.get("core_eps")
    adjusted = False
    if ticker == "GOOGL" and core_eps and eps and abs(core_eps - eps) / max(abs(eps), 0.01) > 0.10:
        eps = _median([eps, core_eps])
        adjusted = True
    if eps is None or eps <= 0 or sector not in SECTOR_PE:
        return ModelResult("normalized_eps", False, None, None, None, 0.0, {}, "positive normalized EPS or sector benchmark unavailable")
    growth = _growth_rate(metrics, family)
    quality = 1.5 if (metrics.get("operating_margin") or 0) >= 0.20 else -1.0 if (metrics.get("operating_margin") or 0) < 0.08 else 0.0
    rate_adjustment = (0.09 - discount_rate) * 35
    target_pe = _clamp(SECTOR_PE[sector] + growth * 20 + quality + rate_adjustment, 8.0, 32.0)
    base = eps * target_pe
    return ModelResult(
        "normalized_eps",
        True,
        base * 0.78,
        base,
        base * 1.22,
        0.0,
        {"normalized_eps": round(eps, 6), "target_pe": round(target_pe, 3), "growth": round(growth, 6), "core_eps_adjustment": adjusted},
        "five-year median for cyclical issuers, otherwise recent three-year median; multiple is sector, growth, quality and rate adjusted",
    )


def _dcf_model(metrics: dict[str, float | None], family: str, discount_rate: float) -> ModelResult:
    fcf, shares = metrics.get("fcf"), metrics.get("shares")
    if fcf is None or fcf <= 0 or shares is None or shares <= 0 or discount_rate <= 0.03:
        return ModelResult("fcf_dcf", False, None, None, None, 0.0, {}, "positive normalized FCF, shares, and a valid discount rate are required")
    growth = _growth_rate(metrics, family)

    def value(growth_shift: float, rate_shift: float, terminal_shift: float) -> float:
        g = _clamp(growth + growth_shift, -0.05, 0.16)
        rate = _clamp(discount_rate + rate_shift, 0.065, 0.18)
        terminal = _clamp(min(0.025 + terminal_shift, max(0.0, g / 2)), 0.0, 0.03)
        projected = fcf
        present = 0.0
        for year in range(1, 6):
            projected *= 1 + g
            present += projected / ((1 + rate) ** year)
        terminal_value = projected * (1 + terminal) / (rate - terminal)
        return (present + terminal_value / ((1 + rate) ** 5)) / shares

    base = value(0.0, 0.0, 0.0)
    return ModelResult(
        "fcf_dcf",
        True,
        value(-0.03, 0.015, -0.005),
        base,
        value(0.03, -0.01, 0.005),
        0.0,
        {"normalized_fcf": round(fcf, 2), "diluted_shares": round(shares, 2), "growth": round(growth, 6), "discount_rate": round(discount_rate, 6)},
        "five-year explicit FCF forecast plus terminal value; current market price is not an input",
    )


def _ev_ebitda_model(sector: str, metrics: dict[str, float | None], family: str) -> ModelResult:
    ebitda, shares = metrics.get("ebitda"), metrics.get("shares")
    cash, debt = metrics.get("cash"), metrics.get("debt")
    if ebitda is None or ebitda <= 0 or shares is None or shares <= 0 or sector not in SECTOR_EV_EBITDA:
        return ModelResult("ev_ebitda", False, None, None, None, 0.0, {}, "EBITDA, diluted shares, or sector benchmark unavailable")
    multiple = SECTOR_EV_EBITDA[sector]
    if family == "cyclical":
        multiple *= 0.85
    net_debt = (debt or 0.0) - (cash or 0.0)

    def per_share(multiplier: float) -> float:
        return max(0.0, ebitda * multiplier - net_debt) / shares

    base = per_share(multiple)
    return ModelResult(
        "ev_ebitda",
        True,
        per_share(multiple * 0.80),
        base,
        per_share(multiple * 1.20),
        0.0,
        {"normalized_ebitda": round(ebitda, 2), "multiple": round(multiple, 3), "net_debt": round(net_debt, 2)},
        "normalized operating income plus D&A, converted from enterprise to equity value",
    )


def _financial_model(metrics: dict[str, float | None], discount_rate: float) -> ModelResult:
    equity, income, shares = metrics.get("equity"), metrics.get("net_income"), metrics.get("shares")
    if not equity or equity <= 0 or not shares or shares <= 0 or income is None:
        return ModelResult("residual_pb", False, None, None, None, 0.0, {}, "book equity, earnings, or shares unavailable")
    roe = income / equity
    growth = _clamp(_growth_rate(metrics, "financial"), 0.0, 0.04)
    if discount_rate <= growth + 0.01:
        return ModelResult("residual_pb", False, None, None, None, 0.0, {}, "cost of equity is not sufficiently above sustainable growth")
    justified_pb = _clamp((roe - growth) / (discount_rate - growth), 0.35, 3.5)
    base = equity / shares * justified_pb
    return ModelResult(
        "residual_pb",
        True,
        base * 0.75,
        base,
        base * 1.25,
        1.0,
        {"book_value_per_share": round(equity / shares, 6), "roe": round(roe, 6), "growth": round(growth, 6), "cost_of_equity": round(discount_rate, 6), "justified_pb": round(justified_pb, 4)},
        "financial-sector residual-income proxy using book value, ROE, sustainable growth and cost of equity",
    )


def _assign_weights(models: list[ModelResult], family: str) -> list[ModelResult]:
    desired = {
        "general": {"normalized_eps": 0.40, "fcf_dcf": 0.40, "ev_ebitda": 0.20},
        "cyclical": {"normalized_eps": 0.45, "fcf_dcf": 0.25, "ev_ebitda": 0.30},
        "acquisitive": {"normalized_eps": 0.15, "fcf_dcf": 0.55, "ev_ebitda": 0.30},
        "financial": {"residual_pb": 1.0},
    }.get(family, {})
    available_total = sum(desired.get(model.name, 0.0) for model in models if model.available)
    if available_total <= 0:
        return models
    return [
        ModelResult(**{**asdict(model), "weight": desired.get(model.name, 0.0) / available_total if model.available else 0.0})
        for model in models
    ]


def _weighted(models: list[ModelResult], field: str) -> float | None:
    values = [(getattr(model, field), model.weight) for model in models if model.available and getattr(model, field) is not None and model.weight > 0]
    if not values:
        return None
    return sum(value * weight for value, weight in values) / sum(weight for _, weight in values)


def _narrative(ticker: str, sector: str, family: str) -> tuple[str, str]:
    special = {
        "GOOGL": (
            "광고·클라우드 영업현금흐름과 AI 제품 확장; 영업이익 기반 core EPS로 투자 평가손익 영향을 점검",
            "AI 인프라 CAPEX 확대, 광고 경기 민감도, 규제와 일회성 투자 평가손익",
        ),
        "AMAT": (
            "첨단 공정·패키징 장비 수요와 장기 반도체 투자 증가",
            "설비투자 사이클, 수주 변동과 고객·지역 집중; 5년 정상화 지표 적용",
        ),
        "AVGO": (
            "AI 네트워킹·가속기 수요와 VMware 반복 현금흐름",
            "인수 부채, 통합 실행, 무형자산 상각과 GAAP/조정 EPS 괴리; FCF 가중 확대",
        ),
        "QCOM": (
            "라이선스 수익과 자동차·IoT 매출 다변화",
            "스마트폰 업황, 주요 고객 내재화와 라이선스 규제 위험",
        ),
    }
    if ticker in special:
        return special[ticker]
    if family == "financial":
        return "순자산 수익성과 자본 효율 개선", "신용손실, 금리·유동성 및 규제자본 위험"
    if family == "reit":
        return "임대 현금흐름과 자산 가동률", "금리·차환 위험; 검증된 FFO/AFFO 부재 시 평가 보류"
    return f"{sector or '해당 산업'} 매출 성장과 현금흐름 전환", "경기·경쟁·마진 변동과 재무 데이터 불확실성"


def evaluate_company(
    *,
    ticker: str,
    company_name: str,
    sector: str,
    industry: str,
    price: float,
    price_as_of_utc: str,
    periods: Iterable[FinancialPeriod],
    risk_free_rate: float,
    evaluated_at: datetime | None = None,
    independent_financial_crosscheck: bool = False,
) -> ValuationResult:
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    annual = sorted(
        [row for row in periods if row.period_type == "FY" and datetime.fromisoformat(row.available_at_utc) <= evaluated_at],
        key=lambda row: row.period_end,
    )
    family = _family(ticker, sector, industry)
    drivers, risks = _narrative(ticker, sector, family)
    empty = dict(
        ticker=ticker,
        company_name=company_name,
        model_version=MODEL_VERSION,
        evaluated_at_utc=evaluated_at.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        price=float(price),
        price_as_of_utc=price_as_of_utc,
        financials_as_of_utc=max((row.available_at_utc for row in annual), default=None),
        growth_drivers=drivers,
        risk_factors=risks,
        eps_revision_trend="unavailable: no redistribution-safe consensus source configured",
        valuation_family=family,
    )
    if family == "reit":
        return ValuationResult(**empty, conservative_value=None, base_value=None, optimistic_value=None, upside_to_base=None, safety_margin=None, favorable_buy_low=None, favorable_buy_high=None, judgement="판단 보류", confidence_score=15, uncertainty="very_high", methods_used="none", model_details_json="[]", status="hold", change_reason="verified FFO/AFFO unavailable")
    if len(annual) < 3 or risk_free_rate <= 0:
        return ValuationResult(**empty, conservative_value=None, base_value=None, optimistic_value=None, upside_to_base=None, safety_margin=None, favorable_buy_low=None, favorable_buy_high=None, judgement="판단 보류", confidence_score=10, uncertainty="very_high", methods_used="none", model_details_json="[]", status="hold", change_reason="fewer than three annual periods or missing official risk-free rate")

    metrics = _normalized_metrics(annual, family)
    data_quality_premium = 0.015 if any(row.validation_state != "verified" for row in annual[-3:]) else 0.0
    leverage = ((metrics.get("debt") or 0) - (metrics.get("cash") or 0)) / max(metrics.get("fcf") or 1, 1)
    leverage_premium = _clamp(max(0.0, leverage - 2.0) * 0.003, 0.0, 0.025)
    discount_rate = _clamp(risk_free_rate + EQUITY_RISK_PREMIUM + data_quality_premium + leverage_premium, 0.07, 0.18)
    if family == "financial":
        models = [_financial_model(metrics, discount_rate)]
    else:
        models = [
            _earnings_model(ticker, sector, metrics, family, discount_rate),
            _dcf_model(metrics, family, discount_rate),
            _ev_ebitda_model(sector, metrics, family),
        ]
    models = _assign_weights(models, family)
    base = _weighted(models, "base")
    conservative = _weighted(models, "conservative")
    optimistic = _weighted(models, "optimistic")
    available_count = sum(model.available for model in models)
    coverage_fields = ("revenue", "operating_income", "net_income", "diluted_eps", "diluted_shares", "cfo", "capex", "cash", "total_debt", "equity")
    coverage = sum(getattr(row, field) is not None for row in annual[-5:] for field in coverage_fields) / (len(annual[-5:]) * len(coverage_fields))
    confidence = round(20 + min(len(annual), 5) * 5 + coverage * 25 + available_count * 8 + (12 if independent_financial_crosscheck else 0))
    confidence = int(_clamp(confidence, 0, 100))
    if base is None or base <= 0:
        return ValuationResult(**empty, conservative_value=None, base_value=None, optimistic_value=None, upside_to_base=None, safety_margin=None, favorable_buy_low=None, favorable_buy_high=None, judgement="판단 보류", confidence_score=confidence, uncertainty="very_high", methods_used="none", model_details_json=json.dumps([asdict(model) for model in models], ensure_ascii=False, sort_keys=True), status="hold", change_reason="no valuation model passed its input gate")
    safety_margin = _clamp(0.15 + (100 - confidence) / 250 + (0.05 if family == "cyclical" else 0), 0.15, 0.40)
    upside = (base - price) / base
    judgement = "저평가" if upside >= safety_margin else "고평가" if upside <= -0.15 else "적정"
    uncertainty = "low" if confidence >= 80 else "medium" if confidence >= 65 else "high"
    methods = ", ".join(model.name for model in models if model.available)
    return ValuationResult(
        **empty,
        conservative_value=round(conservative, 2) if conservative is not None else None,
        base_value=round(base, 2),
        optimistic_value=round(optimistic, 2) if optimistic is not None else None,
        upside_to_base=round(upside, 6),
        safety_margin=round(safety_margin, 6),
        favorable_buy_low=round(base * (1 - safety_margin), 2),
        favorable_buy_high=round(base * (1 - safety_margin * 0.60), 2),
        judgement=judgement,
        confidence_score=confidence,
        uncertainty=uncertainty,
        methods_used=methods,
        model_details_json=json.dumps([asdict(model) for model in models], ensure_ascii=False, sort_keys=True),
        status="evaluated",
    )

