from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


def _number(value: float | int | None, name: str, *, positive: bool = False) -> float:
    if value is None or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if positive and result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def operating_tax_evidence(
    *,
    operating_income: float,
    pretax_income: float,
    income_tax: float,
    identified_one_time_pretax_gain: float | None = None,
    identified_one_time_after_tax_gain: float | None = None,
    verified_operating_tax_rate: float | None = None,
) -> dict[str, Any]:
    """Separate reported effective tax from a verified operating tax rate.

    Removing a disclosed investment gain from consolidated pre-tax income and tax
    expense can produce a useful research diagnostic.  It does not prove the tax
    rate on operating income because other income, jurisdiction mix, credits and
    discrete tax items remain in the consolidated provision.
    """

    operating_income = _number(operating_income, "operating_income", positive=True)
    pretax_income = _number(pretax_income, "pretax_income", positive=True)
    income_tax = _number(income_tax, "income_tax")
    actual_rate = income_tax / pretax_income
    if not 0 <= actual_rate < 1:
        raise ValueError("reported effective tax rate must be between zero and one")

    adjusted_rate = None
    tax_on_identified_gain = None
    adjusted_pretax = None
    adjusted_tax = None
    if identified_one_time_pretax_gain is not None or identified_one_time_after_tax_gain is not None:
        gain = _number(identified_one_time_pretax_gain, "identified_one_time_pretax_gain")
        after_tax = _number(identified_one_time_after_tax_gain, "identified_one_time_after_tax_gain")
        if gain < 0 or after_tax < 0 or after_tax > gain:
            raise ValueError("identified gain must be non-negative and after-tax gain cannot exceed pre-tax gain")
        tax_on_identified_gain = gain - after_tax
        adjusted_pretax = pretax_income - gain
        adjusted_tax = income_tax - tax_on_identified_gain
        if adjusted_pretax > 0 and 0 <= adjusted_tax < adjusted_pretax:
            adjusted_rate = adjusted_tax / adjusted_pretax

    verified_rate = None
    if verified_operating_tax_rate is not None:
        verified_rate = _number(verified_operating_tax_rate, "verified_operating_tax_rate")
        if not 0 <= verified_rate < 1:
            raise ValueError("verified operating tax rate must be between zero and one")

    return {
        "status": "verified" if verified_rate is not None else "incomplete",
        "operating_income": operating_income,
        "reported_effective_tax_rate": actual_rate,
        "reported_effective_tax_rate_nopat_proxy": operating_income * (1 - actual_rate),
        "identified_one_time_pretax_gain": identified_one_time_pretax_gain,
        "identified_one_time_after_tax_gain": identified_one_time_after_tax_gain,
        "tax_on_identified_gain": tax_on_identified_gain,
        "event_adjusted_consolidated_pretax_income": adjusted_pretax,
        "event_adjusted_consolidated_income_tax": adjusted_tax,
        "event_adjusted_total_effective_tax_rate": adjusted_rate,
        "event_adjusted_nopat_proxy": None if adjusted_rate is None else operating_income * (1 - adjusted_rate),
        "verified_operating_tax_rate": verified_rate,
        "verified_nopat": None if verified_rate is None else operating_income * (1 - verified_rate),
        "formal_nopat_eligible": verified_rate is not None,
        "reason": (
            "a source-verified operating tax rate is available"
            if verified_rate is not None
            else "consolidated tax expense is affected by non-operating gains and does not establish the tax rate on EBIT"
        ),
    }


def working_capital_evidence(
    *,
    current_operating_assets: Mapping[str, float | None],
    current_operating_liabilities: Mapping[str, float | None],
    prior_operating_assets: Mapping[str, float | None],
    prior_operating_liabilities: Mapping[str, float | None],
    unclassified_fields: Iterable[str] = (),
) -> dict[str, Any]:
    """Measure operating NWC only when every supplied component is classified.

    A raw total-current-assets/current-liabilities shortcut is deliberately not
    accepted because it would mix cash, securities, taxes and debt-like claims
    with operating working capital.
    """

    missing: list[str] = []

    def total(mapping: Mapping[str, float | None], side: str, period: str) -> float | None:
        subtotal = 0.0
        for name, value in mapping.items():
            if value is None:
                missing.append(f"{period}.{side}.{name}")
                continue
            subtotal += _number(value, f"{period}.{side}.{name}")
        return None if any(item.startswith(f"{period}.{side}.") for item in missing) else subtotal

    current_assets = total(current_operating_assets, "assets", "current")
    current_liabilities = total(current_operating_liabilities, "liabilities", "current")
    prior_assets = total(prior_operating_assets, "assets", "prior")
    prior_liabilities = total(prior_operating_liabilities, "liabilities", "prior")
    unclassified = sorted({str(item) for item in unclassified_fields if str(item)})
    observed_change = None
    if None not in (current_assets, current_liabilities, prior_assets, prior_liabilities):
        current_nwc = float(current_assets) - float(current_liabilities)
        prior_nwc = float(prior_assets) - float(prior_liabilities)
        observed_change = current_nwc - prior_nwc
    formal = observed_change if not missing and not unclassified else None
    return {
        "status": "verified" if formal is not None else "incomplete",
        "formula": "operating current assets minus operating current liabilities; change=current-prior",
        "missing_components": sorted(missing),
        "unclassified_components": unclassified,
        "observed_classified_component_change": observed_change,
        "verified_change_operating_working_capital": formal,
        "fcff_cash_flow_effect": None if formal is None else -formal,
        "reason": (
            "all operating working-capital components are present and classified"
            if formal is not None
            else "missing or mixed operating/non-operating current-balance-sheet components block formal delta NWC"
        ),
    }


def fcff_components(
    *,
    operating_income: float,
    depreciation_amortization: float,
    capex: float,
    verified_operating_tax_rate: float | None,
    verified_change_operating_working_capital: float | None,
    research_tax_rate: float | None = None,
) -> dict[str, Any]:
    """Apply FCFF = NOPAT + D&A - CAPEX - delta operating NWC.

    Research proxies are returned separately and never become formal FCFF.
    """

    operating_income = _number(operating_income, "operating_income", positive=True)
    depreciation = _number(depreciation_amortization, "depreciation_amortization")
    capex = _number(capex, "capex")
    if depreciation < 0 or capex < 0:
        raise ValueError("depreciation and capex must be non-negative")

    missing: list[str] = []
    formal_nopat = None
    if verified_operating_tax_rate is None:
        missing.append("verified_operating_tax_rate")
    else:
        rate = _number(verified_operating_tax_rate, "verified_operating_tax_rate")
        if not 0 <= rate < 1:
            raise ValueError("verified operating tax rate must be between zero and one")
        formal_nopat = operating_income * (1 - rate)
    if verified_change_operating_working_capital is None:
        missing.append("verified_change_operating_working_capital")
    else:
        verified_change_operating_working_capital = _number(
            verified_change_operating_working_capital,
            "verified_change_operating_working_capital",
        )

    formal_fcff = None
    if not missing:
        formal_fcff = (
            float(formal_nopat)
            + depreciation
            - capex
            - float(verified_change_operating_working_capital)
        )

    research_nopat = None
    research_pre_working_capital_fcff = None
    if research_tax_rate is not None:
        research_tax_rate = _number(research_tax_rate, "research_tax_rate")
        if not 0 <= research_tax_rate < 1:
            raise ValueError("research tax rate must be between zero and one")
        research_nopat = operating_income * (1 - research_tax_rate)
        research_pre_working_capital_fcff = research_nopat + depreciation - capex

    return {
        "status": "verified" if formal_fcff is not None else "incomplete",
        "formula": "FCFF = NOPAT + depreciation_and_amortization - CAPEX - increase_in_operating_working_capital",
        "operating_income": operating_income,
        "verified_nopat": formal_nopat,
        "depreciation_amortization": depreciation,
        "capex": capex,
        "verified_change_operating_working_capital": verified_change_operating_working_capital,
        "formal_fcff": formal_fcff,
        "missing_components": missing,
        "research_tax_rate": research_tax_rate,
        "research_nopat_proxy": research_nopat,
        "research_pre_working_capital_fcff_proxy": research_pre_working_capital_fcff,
        "research_proxy_operational_use": False,
    }


def enterprise_fcff_schedule(
    *,
    base_fcff: float,
    growth_rate: float,
    wacc: float,
    terminal_growth: float,
    years: int = 5,
) -> dict[str, Any]:
    """Discount verified FCFF at WACC to enterprise value.

    This function deliberately stops at enterprise value. Common-equity claims
    are handled by :func:`enterprise_to_equity_bridge` so asset/claim omissions
    cannot hide inside the DCF.
    """

    base_fcff = _number(base_fcff, "base_fcff", positive=True)
    growth_rate = _number(growth_rate, "growth_rate")
    wacc = _number(wacc, "wacc")
    terminal_growth = _number(terminal_growth, "terminal_growth")
    if years < 1:
        raise ValueError("years must be positive")
    if growth_rate <= -1 or wacc <= terminal_growth or wacc <= -1:
        raise ValueError("enterprise DCF requires growth above -100% and WACC above terminal growth")

    schedule: list[dict[str, float | int]] = []
    projected = base_fcff
    explicit_present_value = 0.0
    for year in range(1, years + 1):
        projected *= 1 + growth_rate
        discount_factor = 1 / ((1 + wacc) ** year)
        present_value = projected * discount_factor
        explicit_present_value += present_value
        schedule.append({
            "year": year,
            "fcff": projected,
            "discount_factor": discount_factor,
            "present_value": present_value,
        })
    terminal_value = projected * (1 + terminal_growth) / (wacc - terminal_growth)
    terminal_discount_factor = 1 / ((1 + wacc) ** years)
    terminal_present_value = terminal_value * terminal_discount_factor
    enterprise_value = explicit_present_value + terminal_present_value
    return {
        "cash_flow_definition": "formal_fcff_nopat_plus_da_minus_capex_minus_delta_operating_nwc",
        "discount_rate_type": "wacc",
        "base_fcff": base_fcff,
        "growth_rate": growth_rate,
        "wacc": wacc,
        "terminal_growth": terminal_growth,
        "years": years,
        "schedule": schedule,
        "explicit_present_value": explicit_present_value,
        "terminal_value_at_horizon": terminal_value,
        "terminal_discount_factor": terminal_discount_factor,
        "terminal_present_value": terminal_present_value,
        "terminal_value_share": terminal_present_value / enterprise_value,
        "enterprise_value": enterprise_value,
        "equity_value": None,
        "per_share_value": None,
    }


def wacc_evidence(
    *,
    risk_free_rate: float,
    beta: float | None,
    equity_risk_premium: float | None,
    pretax_cost_of_debt: float | None,
    marginal_tax_rate: float | None,
    equity_market_value: float | None,
    debt_market_value: float | None,
    verified_components: Iterable[str] = (),
    legacy_data_quality_premium: float | None = None,
) -> dict[str, Any]:
    """Calculate WACC only from source-verified economic inputs."""

    risk_free_rate = _number(risk_free_rate, "risk_free_rate")
    supplied = {
        "risk_free_rate": risk_free_rate,
        "beta": beta,
        "equity_risk_premium": equity_risk_premium,
        "pretax_cost_of_debt": pretax_cost_of_debt,
        "marginal_tax_rate": marginal_tax_rate,
        "equity_market_value": equity_market_value,
        "debt_market_value": debt_market_value,
    }
    verified = {str(item) for item in verified_components}
    missing = [name for name, value in supplied.items() if value is None]
    unverified = [name for name, value in supplied.items() if value is not None and name not in verified]
    wacc = None
    cost_of_equity = None
    after_tax_cost_of_debt = None
    weights = None
    if not missing and not unverified:
        values = {name: _number(value, name) for name, value in supplied.items()}
        if values["equity_market_value"] <= 0 or values["debt_market_value"] < 0:
            raise ValueError("capital values must be positive/non-negative")
        if not 0 <= values["marginal_tax_rate"] < 1:
            raise ValueError("marginal tax rate must be between zero and one")
        cost_of_equity = values["risk_free_rate"] + values["beta"] * values["equity_risk_premium"]
        after_tax_cost_of_debt = values["pretax_cost_of_debt"] * (1 - values["marginal_tax_rate"])
        capital = values["equity_market_value"] + values["debt_market_value"]
        equity_weight = values["equity_market_value"] / capital
        debt_weight = values["debt_market_value"] / capital
        weights = {"equity": equity_weight, "debt": debt_weight}
        wacc = equity_weight * cost_of_equity + debt_weight * after_tax_cost_of_debt

    legacy_proxy = risk_free_rate + 0.05 + (legacy_data_quality_premium or 0.0)
    return {
        "status": "verified" if wacc is not None else "incomplete",
        "formula": "WACC = E/(D+E)*(Rf+beta*ERP) + D/(D+E)*pre_tax_cost_debt*(1-marginal_tax_rate)",
        "components": supplied,
        "verified_components": sorted(verified),
        "missing_components": sorted(missing),
        "unverified_components": sorted(unverified),
        "cost_of_equity": cost_of_equity,
        "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "capital_weights": weights,
        "wacc": wacc,
        "legacy_cost_of_equity_proxy": legacy_proxy,
        "legacy_proxy_operational_use": False,
        "data_quality_premium_is_economic_risk": False,
        "reason": (
            "all WACC inputs are source verified"
            if wacc is not None
            else "risk-free rate alone does not verify beta, ERP, debt yield, marginal tax rate, or market-value capital weights"
        ),
    }


def enterprise_to_equity_bridge(
    *,
    enterprise_value: float | None,
    cash: float | None,
    marketable_securities: float | None,
    debt: float | None,
    preferred_equity: float | None,
    noncontrolling_interest: float | None,
    debt_like_lease_liabilities: float | None,
    other_verified_nonoperating_assets: float | None,
    other_verified_potential_claims: float | None,
    diluted_shares: float | None,
) -> dict[str, Any]:
    """Bridge FCFF enterprise value to common equity without silent zero fills."""

    inputs = {
        "enterprise_value": enterprise_value,
        "cash": cash,
        "marketable_securities": marketable_securities,
        "debt": debt,
        "preferred_equity": preferred_equity,
        "noncontrolling_interest": noncontrolling_interest,
        "debt_like_lease_liabilities": debt_like_lease_liabilities,
        "other_verified_nonoperating_assets": other_verified_nonoperating_assets,
        "other_verified_potential_claims": other_verified_potential_claims,
        "diluted_shares": diluted_shares,
    }
    missing = sorted(name for name, value in inputs.items() if value is None)
    common_equity = None
    per_share = None
    if not missing:
        values = {name: _number(value, name) for name, value in inputs.items()}
        if values["diluted_shares"] <= 0:
            raise ValueError("diluted shares must be positive")
        common_equity = (
            values["enterprise_value"]
            + values["cash"]
            + values["marketable_securities"]
            + values["other_verified_nonoperating_assets"]
            - values["debt"]
            - values["preferred_equity"]
            - values["noncontrolling_interest"]
            - values["debt_like_lease_liabilities"]
            - values["other_verified_potential_claims"]
        )
        per_share = common_equity / values["diluted_shares"]
    return {
        "status": "verified" if common_equity is not None else "incomplete",
        "formula": "common_equity = EV + cash + marketable_securities + verified_other_nonoperating_assets - debt - preferred - NCI - debt_like_leases - verified_other_claims",
        "inputs": inputs,
        "missing_components": missing,
        "common_equity_value": common_equity,
        "per_share_value": per_share,
        "operational_use": common_equity is not None,
    }


def operating_fcff_projection(
    *,
    base_revenue: float,
    scenarios: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project revenue-to-FCFF only for fully sourced scenario rows."""

    revenue = _number(base_revenue, "base_revenue", positive=True)
    output: list[dict[str, Any]] = []
    required = (
        "revenue_growth",
        "operating_margin",
        "operating_tax_rate",
        "depreciation_amortization",
        "capex",
        "change_operating_working_capital",
    )
    for year, raw in enumerate(scenarios, start=1):
        missing = [name for name in required if raw.get(name) is None]
        verified = bool(raw.get("evidence_verified"))
        if missing or not verified:
            output.append({
                "year": year,
                "status": "incomplete",
                "missing_components": missing,
                "evidence_verified": verified,
                "fcff": None,
            })
            continue
        growth = _number(raw["revenue_growth"], "revenue_growth")
        margin = _number(raw["operating_margin"], "operating_margin")
        tax = _number(raw["operating_tax_rate"], "operating_tax_rate")
        depreciation = _number(raw["depreciation_amortization"], "depreciation_amortization")
        capex = _number(raw["capex"], "capex")
        delta_nwc = _number(raw["change_operating_working_capital"], "change_operating_working_capital")
        if growth <= -1 or not 0 <= margin <= 1 or not 0 <= tax < 1:
            raise ValueError("scenario growth, margin, or tax rate is outside its economic range")
        revenue *= 1 + growth
        ebit = revenue * margin
        nopat = ebit * (1 - tax)
        net_reinvestment = capex - depreciation + delta_nwc
        contradiction = growth > 0 and net_reinvestment <= 0 and not bool(raw.get("verified_capacity_evidence"))
        fcff = nopat - net_reinvestment
        output.append({
            "year": year,
            "status": "contradictory" if contradiction else "verified_research_scenario",
            "revenue": revenue,
            "operating_income": ebit,
            "nopat": nopat,
            "depreciation_amortization": depreciation,
            "capex": capex,
            "change_operating_working_capital": delta_nwc,
            "net_reinvestment": net_reinvestment,
            "reinvestment_rate_on_nopat": None if nopat == 0 else net_reinvestment / nopat,
            "fcff": None if contradiction else fcff,
            "operational_use": False,
            "contradiction": (
                "positive growth with non-positive net reinvestment lacks verified capacity/asset-light evidence"
                if contradiction
                else None
            ),
        })
    complete = bool(output) and all(row["status"] == "verified_research_scenario" for row in output)
    return {
        "status": "research_scenario_complete" if complete else "underdetermined",
        "formula": "revenue -> operating margin -> NOPAT -> net reinvestment -> FCFF",
        "years": output,
        "operational_use": False,
    }
