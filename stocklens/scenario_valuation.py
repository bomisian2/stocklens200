from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


EVIDENCE_STATES = {"observed", "issuer_guidance", "model_assumption", "missing"}


def _finite(value: Any, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _fact(raw: Mapping[str, Any], name: str, *, allow_missing: bool = False) -> dict[str, Any]:
    """Validate a value together with its epistemic state and provenance.

    Values are never inferred from their state. In particular, ``missing`` is
    represented by ``None`` and can never silently become zero.
    """

    state = str(raw.get("state", ""))
    if state not in EVIDENCE_STATES:
        raise ValueError(f"{name}.state must be one of {sorted(EVIDENCE_STATES)}")
    value = raw.get("value")
    if state == "missing":
        if value is not None:
            raise ValueError(f"{name} marked missing must have a null value")
        if not allow_missing:
            raise ValueError(f"{name} is required for a complete research scenario")
    elif value is None:
        raise ValueError(f"{name} with state {state} must have a value")

    source_ids = raw.get("source_ids", [])
    if not isinstance(source_ids, list) or any(not isinstance(item, str) for item in source_ids):
        raise ValueError(f"{name}.source_ids must be a list of strings")
    if state in {"observed", "issuer_guidance"} and not source_ids:
        raise ValueError(f"{name} with state {state} requires a source")
    if state == "model_assumption" and not str(raw.get("rationale", "")).strip():
        raise ValueError(f"{name} model assumptions require a rationale")
    return {
        "value": None if value is None else _finite(value, name),
        "state": state,
        "source_ids": source_ids,
        "rationale": raw.get("rationale"),
        "as_of_utc": raw.get("as_of_utc"),
        "unit": raw.get("unit"),
    }


def project_revenue_to_fcff(
    *,
    base_revenue: Mapping[str, Any],
    years: Iterable[Mapping[str, Any]],
    reinvestment_efficiency_ceiling: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an assumption-based revenue-to-FCFF path.

    This is intentionally a research scenario rather than formal FCFF. Future
    rows normally contain model assumptions even when year-one CAPEX is bounded
    by issuer guidance. Every field keeps its own evidence state.
    """

    base = _fact(base_revenue, "base_revenue")
    if base["state"] != "observed" or base["value"] <= 0:
        raise ValueError("base_revenue must be a positive observed fact")
    efficiency = _fact(reinvestment_efficiency_ceiling, "reinvestment_efficiency_ceiling")
    if efficiency["value"] <= 0:
        raise ValueError("reinvestment efficiency ceiling must be positive")

    revenue = float(base["value"])
    rows: list[dict[str, Any]] = []
    all_states = {base["state"], efficiency["state"]}
    for index, raw in enumerate(years, start=1):
        facts = {
            name: _fact(raw[name], f"year_{index}.{name}")
            for name in (
                "revenue_growth",
                "operating_margin",
                "operating_tax_rate",
                "depreciation_amortization_to_revenue",
                "capex_to_revenue",
                "change_operating_working_capital_to_incremental_revenue",
            )
        }
        lag = _fact(raw["growth_capex_lag_years"], f"year_{index}.growth_capex_lag_years")
        facts["growth_capex_lag_years"] = lag
        all_states.update(item["state"] for item in facts.values())

        growth = facts["revenue_growth"]["value"]
        margin = facts["operating_margin"]["value"]
        tax = facts["operating_tax_rate"]["value"]
        da_ratio = facts["depreciation_amortization_to_revenue"]["value"]
        capex_ratio = facts["capex_to_revenue"]["value"]
        nwc_ratio = facts["change_operating_working_capital_to_incremental_revenue"]["value"]
        if growth <= -1 or not 0 <= margin <= 1 or not 0 <= tax < 1:
            raise ValueError("growth, margin, or tax rate is outside its economic range")
        if min(da_ratio, capex_ratio, nwc_ratio) < 0:
            raise ValueError("D&A, CAPEX, and working-capital ratios cannot be negative")
        if lag["value"] < 0:
            raise ValueError("growth CAPEX lag cannot be negative")

        prior_revenue = revenue
        revenue *= 1 + growth
        incremental_revenue = revenue - prior_revenue
        operating_income = revenue * margin
        nopat = operating_income * (1 - tax)
        depreciation = revenue * da_ratio
        capex = revenue * capex_ratio
        change_nwc = max(incremental_revenue, 0.0) * nwc_ratio
        net_reinvestment = capex - depreciation + change_nwc
        realized_efficiency = (
            None if net_reinvestment <= 0 else max(incremental_revenue, 0.0) / net_reinvestment
        )
        contradiction = None
        if growth > 0 and net_reinvestment <= 0:
            contradiction = "positive growth is paired with non-positive net reinvestment"
        elif (
            growth > 0
            and realized_efficiency is not None
            and realized_efficiency > efficiency["value"]
        ):
            contradiction = (
                "incremental revenue per unit of net reinvestment exceeds the explicit research ceiling"
            )
        fcff = None if contradiction else nopat + depreciation - capex - change_nwc
        rows.append(
            {
                "year": index,
                "label": str(raw.get("label", f"forecast_year_{index}")),
                "input_facts": facts,
                "revenue": revenue,
                "incremental_revenue": incremental_revenue,
                "operating_income": operating_income,
                "nopat": nopat,
                "depreciation_amortization": depreciation,
                "capex": capex,
                "change_operating_working_capital": change_nwc,
                "net_reinvestment": net_reinvestment,
                "incremental_revenue_per_net_reinvestment": realized_efficiency,
                "fcff": fcff,
                "consistency_status": "rejected" if contradiction else "passed_research_check",
                "contradiction": contradiction,
                "operational_use": False,
            }
        )

    complete = bool(rows) and all(row["fcff"] is not None for row in rows)
    contains_estimates = bool(all_states & {"issuer_guidance", "model_assumption"})
    return {
        "status": "research_scenario_complete" if complete else "rejected",
        "cash_flow_definition": "assumption_based_projected_fcff_not_formal_observed_fcff",
        "base_revenue_fact": base,
        "reinvestment_efficiency_ceiling": efficiency,
        "years": rows,
        "evidence_states_used": sorted(all_states),
        "contains_estimates": contains_estimates,
        "formal_fcff": None,
        "operational_use": False,
    }


def discount_fcff_sensitivity(
    *,
    projection: Mapping[str, Any],
    wacc_candidates: Iterable[Mapping[str, Any]],
    terminal_growth_candidates: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Discount one projected FCFF path across a non-privileged sensitivity grid."""

    if projection.get("status") != "research_scenario_complete":
        return {
            "status": "unavailable",
            "reason": "the operating scenario failed its completeness or consistency gate",
            "rows": [],
            "operational_use": False,
        }
    cash_flows = [_finite(row.get("fcff"), "projected_fcff") for row in projection["years"]]
    if not cash_flows or cash_flows[-1] <= 0:
        return {
            "status": "unavailable",
            "reason": "positive terminal-year projected FCFF is required for the research sensitivity grid",
            "rows": [],
            "operational_use": False,
        }

    wacc_facts = [_fact(item, "wacc_candidate") for item in wacc_candidates]
    growth_facts = [_fact(item, "terminal_growth_candidate") for item in terminal_growth_candidates]
    rows: list[dict[str, Any]] = []
    for wacc_fact in wacc_facts:
        for growth_fact in growth_facts:
            wacc = wacc_fact["value"]
            terminal_growth = growth_fact["value"]
            if wacc <= terminal_growth:
                rows.append(
                    {
                        "wacc_fact": wacc_fact,
                        "terminal_growth_fact": growth_fact,
                        "status": "unstable_rejected",
                        "reason": "WACC must exceed terminal growth",
                        "enterprise_value": None,
                        "operational_use": False,
                    }
                )
                continue
            present_values = [
                cash_flow / ((1 + wacc) ** year)
                for year, cash_flow in enumerate(cash_flows, start=1)
            ]
            terminal_value = cash_flows[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
            terminal_present_value = terminal_value / ((1 + wacc) ** len(cash_flows))
            enterprise_value = sum(present_values) + terminal_present_value
            terminal_share = terminal_present_value / enterprise_value
            spread = wacc - terminal_growth
            unstable_reasons: list[str] = []
            if spread < 0.02:
                unstable_reasons.append("WACC-minus-terminal-growth spread is below 2 percentage points")
            if terminal_share > 0.80:
                unstable_reasons.append("terminal value exceeds 80% of enterprise value")
            rows.append(
                {
                    "wacc_fact": wacc_fact,
                    "terminal_growth_fact": growth_fact,
                    "status": "unstable" if unstable_reasons else "research_only",
                    "explicit_present_value": sum(present_values),
                    "terminal_present_value": terminal_present_value,
                    "terminal_value_share": terminal_share,
                    "enterprise_value": enterprise_value,
                    "instability_reasons": unstable_reasons,
                    "common_equity_value": None,
                    "per_share_value": None,
                    "operational_use": False,
                }
            )

    usable = [row for row in rows if row.get("enterprise_value") is not None]
    stable = [row for row in usable if row["status"] == "research_only"]
    return {
        "status": "research_sensitivity_complete" if usable else "unavailable",
        "no_privileged_default": True,
        "rows": rows,
        "stable_enterprise_value_range": (
            None
            if not stable
            else {
                "low": min(row["enterprise_value"] for row in stable),
                "high": max(row["enterprise_value"] for row in stable),
            }
        ),
        "all_computable_enterprise_value_range": (
            None
            if not usable
            else {
                "low": min(row["enterprise_value"] for row in usable),
                "high": max(row["enterprise_value"] for row in usable),
            }
        ),
        "formal_wacc": None,
        "formal_enterprise_value": None,
        "formal_common_equity_value": None,
        "formal_per_share_value": None,
        "operational_use": False,
    }


def market_expectations_status(
    *,
    current_price: Mapping[str, Any],
    diluted_shares: Mapping[str, Any],
    missing_equity_bridge_components: Iterable[str],
) -> dict[str, Any]:
    """State why market-implied operating conditions are not uniquely solvable."""

    price = _fact(current_price, "current_price")
    shares = _fact(diluted_shares, "diluted_shares")
    missing = sorted({str(item) for item in missing_equity_bridge_components if str(item)})
    if price["value"] <= 0 or shares["value"] <= 0:
        raise ValueError("price and diluted shares must be positive")
    return {
        "status": "underdetermined",
        "observed_target_common_equity_value": price["value"] * shares["value"],
        "price_fact": price,
        "diluted_shares_fact": shares,
        "target_enterprise_value": None,
        "missing_equity_bridge_components": missing,
        "jointly_unidentified_conditions": [
            "multi-year revenue growth",
            "operating-margin path",
            "operating tax rate",
            "CAPEX productivity and lag",
            "operating working-capital path",
            "WACC",
            "terminal growth",
        ],
        "reason": (
            "one observed equity price cannot identify a unique revenue-margin-reinvestment path, "
            "and an incomplete enterprise-to-equity bridge prevents a target enterprise value"
        ),
        "is_forecast": False,
        "operational_use": False,
    }
