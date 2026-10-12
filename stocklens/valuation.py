from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .dcf import equity_fcf_schedule, reverse_dcf_sensitivity
from .financials import FinancialPeriod
from .valuation_evidence import ValuationEvidence


MODEL_VERSION = "valuation-v2.1.2"
EQUITY_RISK_PREMIUM = 0.05
FINANCIAL_SECTORS = {"Financial Services", "Financials"}
REIT_INDUSTRY_WORDS = {"reit", "real estate investment trust"}
MIN_PE_OBSERVATIONS = 20
MIN_PE_YEARS = 5.0
MIN_COMPARABLE_OBSERVATIONS = 5


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
    confidence_scope: str = "not_applicable"
    confidence_dimensions_json: str = "{}"
    operational_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(clean) if clean else None


def _quantile(values: Iterable[float], probability: float) -> float | None:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return clean[low]
    return clean[low] + (clean[high] - clean[low]) * (position - low)


def _cagr(first: float | None, last: float | None, years: int) -> float | None:
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("valuation timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _family(ticker: str, sector: str, industry: str) -> str:
    lower = industry.lower()
    if any(word in lower for word in REIT_INDUSTRY_WORDS):
        return "reit"
    if sector in FINANCIAL_SECTORS:
        return "financial"
    if ticker == "INTC":
        return "turnaround"
    if ticker == "AMAT" or any(word in lower for word in ("semiconductor equipment", "steel", "mining")):
        return "cyclical"
    if ticker == "AVGO":
        return "acquisitive"
    return "general"


def _tax_rate(periods: list[FinancialPeriod]) -> float:
    rates = [
        row.income_tax / row.pretax_income for row in periods
        if row.pretax_income and row.pretax_income > 0 and row.income_tax is not None
    ]
    return _clamp(_median(rates) if rates else 0.21, 0.10, 0.30)


def _growth_series(periods: list[FinancialPeriod], field: str) -> list[float]:
    values = [getattr(period, field) for period in periods[-6:]]
    return [
        current / previous - 1 for previous, current in zip(values, values[1:])
        if previous is not None and current is not None and previous > 0 and current > 0
    ]


def _normalized_metrics(
    periods: list[FinancialPeriod],
    family: str,
    latest_ttm: FinancialPeriod | None = None,
) -> dict[str, Any]:
    periods = sorted(periods, key=lambda row: (row.period_end, row.filed_at))
    window = periods[-5:] if family in {"cyclical", "turnaround"} else periods[-3:]
    fcf = (
        latest_ttm.fcf
        if latest_ttm is not None and latest_ttm.fcf is not None and latest_ttm.fcf > 0
        else _median(row.fcf for row in window if row.fcf is not None and row.fcf > 0)
    )
    shares = (
        latest_ttm.diluted_shares
        if latest_ttm is not None and latest_ttm.diluted_shares and latest_ttm.diluted_shares > 0
        else _median(row.diluted_shares for row in window if row.diluted_shares and row.diluted_shares > 0)
    )
    operating = (
        latest_ttm.operating_income
        if latest_ttm is not None and latest_ttm.operating_income is not None
        else _median(row.operating_income for row in window if row.operating_income is not None)
    )
    da = (
        latest_ttm.depreciation_amortization
        if latest_ttm is not None and latest_ttm.depreciation_amortization is not None
        else _median(row.depreciation_amortization for row in window if row.depreciation_amortization is not None)
    )
    revenue_rows = [row for row in periods[-5:] if row.revenue and row.revenue > 0]
    fcf_rows = [row for row in periods[-5:] if row.fcf and row.fcf > 0]
    revenue_growth = _cagr(revenue_rows[0].revenue, revenue_rows[-1].revenue, len(revenue_rows) - 1) if len(revenue_rows) >= 2 else None
    fcf_growth = _cagr(fcf_rows[0].fcf, fcf_rows[-1].fcf, len(fcf_rows) - 1) if len(fcf_rows) >= 2 else None
    growth_series = _growth_series(periods, "revenue") + _growth_series(periods, "fcf")
    margins = [row.operating_income / row.revenue for row in window if row.operating_income is not None and row.revenue]
    latest = latest_ttm or periods[-1]
    return {
        "fcf": fcf,
        "shares": shares,
        "revenue": latest.revenue,
        "cfo": latest.cfo,
        "capex": latest.capex,
        "ebitda": operating + da if operating is not None and da is not None else None,
        "revenue_growth": revenue_growth,
        "fcf_growth": fcf_growth,
        "growth_std": statistics.pstdev(growth_series) if len(growth_series) >= 3 else None,
        "operating_margin": _median(margins),
        "tax_rate": _tax_rate(periods[-5:]),
        "cash": latest.cash,
        "marketable_securities": latest.marketable_securities,
        "debt": latest.total_debt,
        "equity": latest.equity,
        "net_income": latest.net_income,
        "fcf_basis": "derived_ttm" if latest_ttm is not None else "historical_annual_median",
    }


def _growth_rate(metrics: dict[str, float | None], family: str, evidence: ValuationEvidence | None, evaluated_at: datetime) -> tuple[float | None, str]:
    forecast = evidence.forecast if evidence else None
    if forecast and _timestamp(forecast.available_at_utc) <= evaluated_at:
        value = _median([forecast.eps_growth, forecast.fcf_growth])
        if value is not None:
            return _clamp(value, -0.10, 0.08 if family in {"cyclical", "turnaround"} else 0.20), "point_in_time_forecast"
    historical = _median([metrics.get("revenue_growth"), metrics.get("fcf_growth")])
    if historical is None:
        return None, "unavailable"
    return _clamp(historical, -0.10, 0.06 if family in {"cyclical", "turnaround"} else 0.15), "historical_only"


def _historical_pe_distribution(evidence: ValuationEvidence | None, evaluated_at: datetime, risk_free_rate: float) -> tuple[dict[str, Any] | None, str]:
    if evidence is None:
        return None, "point-in-time P/E evidence unavailable"
    eligible: list[tuple[Any, float]] = []
    excluded = 0
    for row in evidence.historical_pe:
        observed, eps_available = _timestamp(row.observed_at_utc), _timestamp(row.eps_available_at_utc)
        if observed > evaluated_at or eps_available > observed or row.distorted or row.public_ttm_eps <= 0 or row.price <= 0:
            excluded += 1
            continue
        eligible.append((row, row.price / row.public_ttm_eps))
    eligible.sort(key=lambda pair: pair[0].observed_at_utc)
    if len(eligible) < MIN_PE_OBSERVATIONS:
        return None, f"only {len(eligible)} clean P/E observations; minimum is {MIN_PE_OBSERVATIONS}"
    span = (_timestamp(eligible[-1][0].observed_at_utc) - _timestamp(eligible[0][0].observed_at_utc)).days / 365.25
    if span < MIN_PE_YEARS:
        return None, f"P/E history spans {span:.2f} years; minimum is {MIN_PE_YEARS:.1f}"
    raw = [value for _, value in eligible]
    raw_q1, raw_q3 = float(_quantile(raw, 0.25)), float(_quantile(raw, 0.75))
    iqr = raw_q3 - raw_q1
    clean = [pair for pair in eligible if max(0.1, raw_q1 - 1.5 * iqr) <= pair[1] <= raw_q3 + 1.5 * iqr]
    values = [value for _, value in clean]
    rate_matched = [value for row, value in clean if row.risk_free_rate is not None and abs(row.risk_free_rate - risk_free_rate) <= 0.0075]
    conditioned = rate_matched if len(rate_matched) >= 8 else values
    sector_ratios = [value / row.sector_pe for row, value in clean if row.sector_pe and row.sector_pe > 0]
    anchors = [float(_median(conditioned))]
    if evidence.current_sector_pe and sector_ratios:
        anchors.append(evidence.current_sector_pe * float(_median(sector_ratios)))
    target = float(_median(anchors))
    calibration = evidence.calibration
    forecast = evidence.forecast
    adjustment = 0.0
    calibration_applied = False
    if calibration and calibration.accepted and calibration.selected_before_holdout and calibration.holdout_observations >= 30 and forecast and _timestamp(forecast.available_at_utc) <= evaluated_at:
        if forecast.eps_growth is not None:
            adjustment += calibration.coefficients.get("eps_growth", 0.0) * forecast.eps_growth
        if forecast.roic is not None:
            adjustment += calibration.coefficients.get("roic", 0.0) * forecast.roic
        target *= 1 + _clamp(adjustment, -0.25, 0.25)
        calibration_applied = True
    target = _clamp(target, float(_quantile(values, 0.10)), float(_quantile(values, 0.90)))
    return {
        "count": len(values), "excluded_count": excluded + len(eligible) - len(clean), "span_years": round(span, 4),
        "q1": float(_quantile(values, 0.25)), "median": float(_median(values)), "q3": float(_quantile(values, 0.75)),
        "rate_conditioned_count": len(rate_matched), "sector_relative_count": len(sector_ratios),
        "target_pe": target, "calibration_applied": calibration_applied, "calibration_adjustment": adjustment,
    }, "verified point-in-time P/E distribution"


def _validated_eps(evidence: ValuationEvidence | None, evaluated_at: datetime) -> tuple[float | None, dict[str, Any], str]:
    quality = evidence.earnings_quality if evidence else None
    if quality is None or _timestamp(quality.available_at_utc) > evaluated_at:
        return None, {}, "verified GAAP/normalized EPS assessment unavailable"
    inputs = {"gaap_eps": quality.gaap_eps, "normalized_eps": quality.normalized_eps, "earnings_quality_state": quality.state, "loss_assessment": quality.loss_assessment, "earnings_source_url": quality.source_url}
    if quality.state in {"negative", "structural_loss", "unverified"} or quality.gaap_eps <= 0:
        return None, inputs, "negative, structural, or unverified EPS blocks the P/E model"
    if quality.state == "distorted":
        visible = [item for item in quality.adjustments if _timestamp(item.available_at_utc) <= evaluated_at]
        inputs["adjustment_categories"] = sorted({item.category for item in visible})
        if quality.normalized_eps is None or quality.normalized_eps <= 0 or not visible:
            return None, inputs, "distorted EPS lacks traceable normalization evidence"
        return quality.normalized_eps, inputs, "traceable normalized EPS used"
    return quality.gaap_eps, inputs, "GAAP EPS used"


def _earnings_model(evidence: ValuationEvidence | None, evaluated_at: datetime, risk_free_rate: float) -> ModelResult:
    eps, eps_inputs, eps_reason = _validated_eps(evidence, evaluated_at)
    distribution, distribution_reason = _historical_pe_distribution(evidence, evaluated_at, risk_free_rate)
    if eps is None or distribution is None:
        return ModelResult("normalized_eps", False, None, None, None, 0.0, {**eps_inputs, "pe_gate": distribution_reason}, f"{eps_reason}; {distribution_reason}")
    return ModelResult(
        "normalized_eps", True, eps * distribution["q1"], eps * distribution["target_pe"], eps * distribution["q3"], 0.0,
        {**eps_inputs, **distribution}, "quality-gated EPS and 5+ year point-in-time P/E distribution; no fixed sector multiple",
    )


def _dcf_model(
    metrics: dict[str, float | None],
    family: str,
    discount_rate: float,
    evidence: ValuationEvidence | None,
    evaluated_at: datetime,
    price: float,
) -> ModelResult:
    fcf, shares = metrics.get("fcf"), metrics.get("shares")
    growth, growth_basis = _growth_rate(metrics, family, evidence, evaluated_at)
    if fcf is None or fcf <= 0 or shares is None or shares <= 0 or discount_rate <= 0.03 or growth is None:
        return ModelResult("fcf_dcf", False, None, None, None, 0.0, {}, "positive normalized FCF, shares, growth evidence, and a valid discount rate are required")
    forecast = evidence.forecast if evidence else None
    uncertainty = [metrics.get("growth_std"), evidence.historical_forecast_error if evidence else None]
    if forecast and _timestamp(forecast.available_at_utc) <= evaluated_at:
        uncertainty.append(forecast.dispersion)
    growth_uncertainty = _clamp(max([value for value in uncertainty if value is not None and value >= 0] or [0.05]), 0.01, 0.12)

    def schedule(growth_rate: float, rate: float) -> dict[str, Any]:
        growth_rate, rate = _clamp(growth_rate, -0.10, 0.20), _clamp(rate, 0.06, 0.20)
        terminal = _clamp(min(0.025, max(0.0, growth_rate / 2)), 0.0, min(0.03, rate - 0.02))
        return equity_fcf_schedule(
            base_fcf=fcf,
            growth_rate=growth_rate,
            discount_rate=rate,
            terminal_growth=terminal,
            diluted_shares=shares,
        )

    conservative_audit = schedule(growth - growth_uncertainty, discount_rate + growth_uncertainty / 2)
    base_audit = schedule(growth, discount_rate)
    optimistic_audit = schedule(growth + growth_uncertainty, discount_rate - growth_uncertainty / 2)
    terminal_rates = (0.015, 0.025, 0.03)
    discount_rates = tuple(
        sorted({_clamp(discount_rate + shift, 0.06, 0.20) for shift in (-0.02, 0.0, 0.02)})
    )
    reverse = reverse_dcf_sensitivity(
        target_price=price,
        base_fcf=fcf,
        diluted_shares=shares,
        discount_rates=discount_rates,
        terminal_growth_rates=terminal_rates,
    )
    cash = metrics.get("cash") or 0.0
    marketable = metrics.get("marketable_securities") or 0.0
    debt = metrics.get("debt") or 0.0
    revenue = metrics.get("revenue")
    capex = metrics.get("capex")
    base_reverse = next(
        (
            row for row in reverse
            if math.isclose(row["discount_rate"], discount_rate, abs_tol=1e-12)
            and math.isclose(row["terminal_growth"], 0.025, abs_tol=1e-12)
        ),
        None,
    )
    translation = None
    revenue_growth = metrics.get("revenue_growth")
    operating_margin = metrics.get("operating_margin")
    if (
        base_reverse
        and base_reverse.get("status") == "solved"
        and revenue is not None
        and revenue > 0
        and revenue_growth is not None
    ):
        implied_year_5_revenue = revenue * ((1 + revenue_growth) ** 5)
        translation = {
            "revenue_growth_anchor": revenue_growth,
            "implied_year_5_revenue": implied_year_5_revenue,
            "implied_year_5_fcf_margin": base_reverse["implied_year_5_fcf"] / implied_year_5_revenue,
            "current_operating_margin": operating_margin,
            "status": "illustrative_not_identified",
            "reason": "holds historical revenue CAGR only to expose the FCF burden; it is not a forecast",
        }
    balance_bridge = {
        "cash": cash,
        "marketable_securities": marketable,
        "debt": debt,
        "observed_net_financial_assets": cash + marketable - debt,
        "applied_to_equity_value": False,
        "reason": base_audit["bridge_reason"],
    }

    return ModelResult(
        "fcf_dcf", True,
        float(conservative_audit["per_share_value"]),
        float(base_audit["per_share_value"]),
        float(optimistic_audit["per_share_value"]),
        0.0,
        {
            "normalized_fcf": round(fcf, 2),
            "fcf_basis": metrics.get("fcf_basis"),
            "cash_flow_definition": base_audit["cash_flow_definition"],
            "cash_flow_classification": base_audit["cash_flow_classification"],
            "discount_rate_type": base_audit["discount_rate_type"],
            "diluted_shares": round(shares, 2),
            "growth": round(growth, 6),
            "growth_basis": growth_basis,
            "growth_uncertainty": round(growth_uncertainty, 6),
            "discount_rate": round(discount_rate, 6),
            "base_case_audit": base_audit,
            "scenario_terminal_value_shares": {
                "conservative": conservative_audit["terminal_value_share"],
                "base": base_audit["terminal_value_share"],
                "optimistic": optimistic_audit["terminal_value_share"],
            },
            "balance_sheet_bridge": balance_bridge,
            "capex_to_revenue": capex / revenue if capex is not None and revenue else None,
            "reverse_dcf": {
                "market_price": price,
                "sensitivity": reverse,
                "illustrative_revenue_translation": translation,
                "revenue_margin_reinvestment_identification": "underdetermined",
                "identification_reason": (
                    "one market price cannot uniquely identify revenue growth, target operating margin, "
                    "working-capital investment, CAPEX and terminal growth simultaneously"
                ),
            },
        },
        (
            "issuer-defined CFO-minus-CAPEX is discounted once as a levered equity cash-flow proxy; "
            "growth is applied once, no enterprise bridge is applied, and reverse DCF is diagnostic only"
        ),
    )


def _ev_ebitda_model(metrics: dict[str, float | None], evidence: ValuationEvidence | None, evaluated_at: datetime) -> ModelResult:
    ebitda, shares = metrics.get("ebitda"), metrics.get("shares")
    rows = [row for row in (evidence.comparable_ev_ebitda if evidence else ()) if _timestamp(row.available_at_utc) <= _timestamp(row.observed_at_utc) <= evaluated_at]
    multiples = [row.ev_to_ebitda for row in rows]
    if ebitda is None or ebitda <= 0 or shares is None or shares <= 0 or len(multiples) < MIN_COMPARABLE_OBSERVATIONS:
        return ModelResult("ev_ebitda", False, None, None, None, 0.0, {"comparable_count": len(multiples)}, "EBITDA, shares, or point-in-time comparable distribution unavailable")
    cash, debt = metrics.get("cash"), metrics.get("debt")
    net_debt = (debt or 0.0) - (cash or 0.0)

    def per_share(multiple: float) -> float:
        return max(0.0, ebitda * multiple - net_debt) / shares

    q1, median, q3 = float(_quantile(multiples, 0.25)), float(_median(multiples)), float(_quantile(multiples, 0.75))
    return ModelResult("ev_ebitda", True, per_share(q1), per_share(median), per_share(q3), 0.0, {"normalized_ebitda": ebitda, "comparable_count": len(multiples), "multiple_q1": q1, "multiple_median": median, "multiple_q3": q3, "net_debt": net_debt}, "point-in-time comparable distribution; no fixed sector multiple")


def _financial_model(metrics: dict[str, float | None], discount_rate: float) -> ModelResult:
    equity, income, shares = metrics.get("equity"), metrics.get("net_income"), metrics.get("shares")
    if not equity or equity <= 0 or not shares or shares <= 0 or income is None:
        return ModelResult("residual_pb", False, None, None, None, 0.0, {}, "book equity, earnings, or shares unavailable")
    roe = income / equity
    if roe <= 0 or discount_rate <= 0.03:
        return ModelResult("residual_pb", False, None, None, None, 0.0, {"roe": roe}, "positive ROE and valid cost of equity required")
    growth = min(max(roe * 0.25, 0.0), discount_rate - 0.02)
    uncertainty = min(abs(roe) * 0.35, discount_rate / 2)

    def value(scenario_roe: float) -> float:
        multiple = max(0.0, (max(0.0, scenario_roe) - growth) / (discount_rate - growth))
        return equity / shares * multiple

    return ModelResult("residual_pb", True, value(roe - uncertainty), value(roe), value(roe + uncertainty), 1.0, {"book_value_per_share": equity / shares, "roe": roe, "sustainable_growth": growth, "cost_of_equity": discount_rate}, "residual-income scenario width follows observed ROE")


def _assign_weights(models: list[ModelResult]) -> list[ModelResult]:
    scores = {}
    for model in models:
        if model.available and model.base and model.base > 0 and model.conservative is not None and model.optimistic is not None:
            scores[model.name] = 1 / max((model.optimistic - model.conservative) / model.base, 0.05)
    total = sum(scores.values())
    if total <= 0:
        return models
    return [ModelResult(**{**asdict(model), "weight": scores.get(model.name, 0.0) / total}) for model in models]


def _weighted(models: list[ModelResult], field: str) -> float | None:
    values = [(getattr(model, field), model.weight) for model in models if model.available and getattr(model, field) is not None and model.weight > 0]
    return sum(value * weight for value, weight in values) / sum(weight for _, weight in values) if values else None


def _narrative(ticker: str, sector: str, family: str) -> tuple[str, str]:
    special = {
        "GOOGL": ("광고·클라우드 현금흐름과 AI 제품 확장", "AI CAPEX, 광고 경기민감도, 규제와 투자 평가손익"),
        "AMAT": ("첨단공정·패키징 장비 수요", "설비투자 사이클, 수주와 고객·지역 집중"),
        "AVGO": ("AI 반도체와 VMware 반복 현금흐름", "인수부채, 통합, 무형자산 상각과 GAAP EPS 왜곡"),
        "QCOM": ("라이선스와 자동차·IoT 다변화", "스마트폰 업황, 고객 내재화와 라이선스 규제"),
        "INTC": ("파운드리 전환과 제품 로드맵 실행", "구조적 마진 훼손, CAPEX, 감손·구조조정 및 음수 EPS"),
    }
    if ticker in special:
        return special[ticker]
    if family == "financial":
        return "순자산 수익성과 자본 효율", "신용손실, 금리·유동성 및 규제자본 위험"
    if family == "reit":
        return "임대 현금흐름과 가동률", "금리·차환 위험; 검증된 FFO/AFFO 부재 시 보류"
    return f"{sector or '해당 산업'} 성장과 현금흐름 전환", "경기·경쟁·마진 변동과 데이터 불확실성"


def _judgement(upside: float, margin: float, width: float, prior: str | None) -> tuple[str, float]:
    buffer = _clamp(width / 4, 0.01, 0.08)
    if prior == "저평가" and upside >= margin - buffer:
        return "저평가", buffer
    if prior == "고평가" and upside <= -margin + buffer:
        return "고평가", buffer
    threshold = margin + buffer if prior == "적정" else margin
    return ("저평가", buffer) if upside >= threshold else ("고평가", buffer) if upside <= -threshold else ("적정", buffer)


def evaluate_company(
    *, ticker: str, company_name: str, sector: str, industry: str, price: float,
    price_as_of_utc: str, periods: Iterable[FinancialPeriod], risk_free_rate: float,
    evaluated_at: datetime | None = None, independent_financial_crosscheck: bool = False,
    valuation_evidence: ValuationEvidence | None = None, prior_judgement: str | None = None,
) -> ValuationResult:
    evaluated_at = (evaluated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    annual = sorted([row for row in periods if row.period_type == "FY" and _timestamp(row.available_at_utc) <= evaluated_at], key=lambda row: row.period_end)
    ttm = sorted([row for row in periods if row.period_type == "TTM" and _timestamp(row.available_at_utc) <= evaluated_at], key=lambda row: row.period_end)
    latest_ttm = ttm[-1] if ttm else None
    family = _family(ticker, sector, industry)
    drivers, risks = _narrative(ticker, sector, family)
    empty = dict(ticker=ticker, company_name=company_name, model_version=MODEL_VERSION, evaluated_at_utc=evaluated_at.replace(microsecond=0).isoformat(), price=float(price), price_as_of_utc=price_as_of_utc, financials_as_of_utc=max((row.available_at_utc for row in [*annual, *ttm]), default=None), growth_drivers=drivers, risk_factors=risks, eps_revision_trend="unavailable: no redistribution-safe point-in-time consensus source configured", valuation_family=family)
    if valuation_evidence and valuation_evidence.ticker != ticker.upper():
        raise ValueError("valuation evidence ticker does not match evaluated company")
    if family == "reit":
        return ValuationResult(**empty, conservative_value=None, base_value=None, optimistic_value=None, upside_to_base=None, safety_margin=None, favorable_buy_low=None, favorable_buy_high=None, judgement="판단 보류", confidence_score=15, uncertainty="very_high", methods_used="none", model_details_json="[]", status="hold", change_reason="verified FFO/AFFO unavailable")
    if len(annual) < 3 or risk_free_rate <= 0:
        return ValuationResult(**empty, conservative_value=None, base_value=None, optimistic_value=None, upside_to_base=None, safety_margin=None, favorable_buy_low=None, favorable_buy_high=None, judgement="판단 보류", confidence_score=10, uncertainty="very_high", methods_used="none", model_details_json="[]", status="hold", change_reason="fewer than three annual periods or missing official risk-free rate")
    metrics = _normalized_metrics(annual, family, latest_ttm)
    quality_rows = [*annual[-3:], *([latest_ttm] if latest_ttm else [])]
    data_quality_premium = 0.015 if any(row.validation_state != "verified" for row in quality_rows) else 0.0
    leverage = ((metrics.get("debt") or 0) - (metrics.get("cash") or 0)) / max(metrics.get("fcf") or 1, 1)
    leverage_premium = _clamp(max(0.0, leverage - 2.0) * 0.003, 0.0, 0.025)
    discount_rate = _clamp(risk_free_rate + EQUITY_RISK_PREMIUM + data_quality_premium + leverage_premium, 0.07, 0.18)
    models = [_financial_model(metrics, discount_rate)] if family == "financial" else [_earnings_model(valuation_evidence, evaluated_at, risk_free_rate), _dcf_model(metrics, family, discount_rate, valuation_evidence, evaluated_at, price), _ev_ebitda_model(metrics, valuation_evidence, evaluated_at)]
    models = _assign_weights(models)
    base, conservative, optimistic = _weighted(models, "base"), _weighted(models, "conservative"), _weighted(models, "optimistic")
    coverage_fields = ("revenue", "operating_income", "net_income", "diluted_eps", "diluted_shares", "cfo", "capex", "cash", "total_debt", "equity")
    coverage = sum(getattr(row, field) is not None for row in annual[-5:] for field in coverage_fields) / (len(annual[-5:]) * len(coverage_fields))
    available_count = sum(model.available for model in models)
    evidence_bonus = 12 if valuation_evidence and not valuation_evidence.fixture else 0
    confidence = int(_clamp(round(15 + min(len(annual), 5) * 5 + coverage * 25 + available_count * 8 + evidence_bonus + (12 if independent_financial_crosscheck else 0)), 0, 100))
    forecast = valuation_evidence.forecast if valuation_evidence else None
    has_visible_forecast = bool(forecast and _timestamp(forecast.available_at_utc) <= evaluated_at)
    confidence_dimensions = {
        "financial_data_quality": (
            "verified_with_unaudited_interim"
            if latest_ttm is not None and all(row.source_url and row.source_artifact_sha256 for row in quality_rows)
            else "partial"
        ),
        "forecast_quality": "point_in_time_verified" if has_visible_forecast else "insufficient",
        "valuation_model_confidence": "research_only" if not has_visible_forecast else "eligible_for_model_review",
        "operational_judgement_eligibility": False,
    }
    details = [asdict(model) for model in models]
    if base is None or base <= 0 or conservative is None or optimistic is None:
        return ValuationResult(**empty, conservative_value=None, base_value=None, optimistic_value=None, upside_to_base=None, safety_margin=None, favorable_buy_low=None, favorable_buy_high=None, judgement="판단 보류", confidence_score=confidence, uncertainty="very_high", methods_used="none", model_details_json=json.dumps(details, ensure_ascii=False, sort_keys=True), status="hold", change_reason="no valuation model passed its point-in-time input gate")
    model_bases = [model.base for model in models if model.available and model.base is not None]
    model_dispersion = statistics.pstdev(model_bases) / base if len(model_bases) >= 2 else 0.0
    scenario_width = max((base - conservative) / base, (optimistic - base) / base, 0.0)
    forecast_dispersion = forecast.dispersion if forecast and _timestamp(forecast.available_at_utc) <= evaluated_at else None
    historical_error = valuation_evidence.historical_forecast_error if valuation_evidence else None
    risk_components = {"scenario_width": scenario_width, "model_dispersion": model_dispersion, "forecast_dispersion": max(forecast_dispersion or 0.0, 0.0), "historical_forecast_error": max(historical_error or 0.0, 0.0), "leverage_premium_scaled": leverage_premium * 4, "data_uncertainty": (100 - confidence) / 500}
    safety_margin = _clamp(max(risk_components.values()), 0.10, 0.50)
    upside = (base - price) / base
    judgement, transition_buffer = _judgement(upside, safety_margin, max(scenario_width, model_dispersion), prior_judgement)
    buy_high = base * (1 - safety_margin)
    buy_low = min(conservative * (1 - safety_margin), buy_high)
    details.append({"name": "automatic_uncertainty_band", "available": True, "weight": 0.0, "conservative": conservative, "base": base, "optimistic": optimistic, "inputs": {**{key: round(value, 6) for key, value in risk_components.items()}, "safety_margin": round(safety_margin, 6), "transition_buffer": round(transition_buffer, 6), "prior_judgement": prior_judgement}, "reason": "company-specific model spread, forecast dispersion, historical error, leverage, and data quality; no fixed 90-110% band"})
    methods = [model.name for model in models if model.available]
    provisional = methods == ["fcf_dcf"] and not has_visible_forecast
    confidence_dimensions["operational_judgement_eligibility"] = not provisional
    confidence_dimensions["valuation_model_confidence"] = "research_only" if provisional else "operational_candidate"
    return ValuationResult(**empty, conservative_value=round(conservative, 2), base_value=round(base, 2), optimistic_value=round(optimistic, 2), upside_to_base=round(upside, 6), safety_margin=round(safety_margin, 6), favorable_buy_low=round(buy_low, 2), favorable_buy_high=round(buy_high, 2), judgement="판단 보류" if provisional else judgement, confidence_score=confidence, uncertainty="low" if confidence >= 80 else "medium" if confidence >= 65 else "high", methods_used=", ".join(methods), model_details_json=json.dumps(details, ensure_ascii=False, sort_keys=True), status="provisional" if provisional else "evaluated", change_reason="historical-only provisional DCF; no point-in-time forecast evidence" if provisional else "first recorded evaluation", confidence_scope="research_input_completeness_not_investment_confidence" if provisional else "operational_model_evidence", confidence_dimensions_json=json.dumps(confidence_dimensions, ensure_ascii=False, sort_keys=True), operational_eligible=not provisional)
