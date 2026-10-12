from __future__ import annotations

import math
from typing import Any, Iterable


def _finite_positive(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return number


def equity_fcf_schedule(
    *,
    base_fcf: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth: float,
    diluted_shares: float,
    years: int = 5,
) -> dict[str, Any]:
    """Discount issuer-defined CFO-minus-CAPEX as an equity cash-flow proxy.

    CFO is after interest and tax under US GAAP, so this series is not FCFF. It is
    also not a complete FCFE construction because debt cash flows and a verified
    operating/non-operating asset split are unavailable. The function therefore
    never applies an enterprise-to-equity bridge or silently adds net cash.
    """

    base_fcf = _finite_positive(base_fcf, "base_fcf")
    diluted_shares = _finite_positive(diluted_shares, "diluted_shares")
    discount_rate = float(discount_rate)
    terminal_growth = float(terminal_growth)
    growth_rate = float(growth_rate)
    if years <= 0:
        raise ValueError("years must be positive")
    if not all(math.isfinite(value) for value in (growth_rate, discount_rate, terminal_growth)):
        raise ValueError("growth and discount inputs must be finite")
    if discount_rate <= terminal_growth:
        raise ValueError("discount_rate must exceed terminal_growth")
    if discount_rate <= -1 or growth_rate <= -1:
        raise ValueError("growth and discount rates must exceed -100%")

    projected = base_fcf
    schedule: list[dict[str, float | int]] = []
    explicit_present_value = 0.0
    for year in range(1, years + 1):
        projected *= 1 + growth_rate
        factor = 1 / ((1 + discount_rate) ** year)
        present_value = projected * factor
        explicit_present_value += present_value
        schedule.append(
            {
                "year": year,
                "cash_flow": projected,
                "discount_factor": factor,
                "present_value": present_value,
            }
        )

    terminal_value = projected * (1 + terminal_growth) / (discount_rate - terminal_growth)
    terminal_discount_factor = 1 / ((1 + discount_rate) ** years)
    terminal_present_value = terminal_value * terminal_discount_factor
    equity_value = explicit_present_value + terminal_present_value
    return {
        "cash_flow_definition": "issuer_defined_fcf_cfo_minus_capex",
        "cash_flow_classification": "levered_equity_cash_flow_proxy_not_canonical_fcfe",
        "discount_rate_type": "cost_of_equity_proxy",
        "enterprise_value": None,
        "enterprise_to_equity_bridge_applied": False,
        "bridge_reason": (
            "CFO-minus-CAPEX is after interest and tax and is not FCFF; adding cash or "
            "subtracting debt without separating operating and non-operating returns would mix bases"
        ),
        "base_fcf": base_fcf,
        "growth_rate": growth_rate,
        "discount_rate": discount_rate,
        "terminal_growth": terminal_growth,
        "diluted_shares": diluted_shares,
        "years": years,
        "schedule": schedule,
        "explicit_present_value": explicit_present_value,
        "terminal_value_at_horizon": terminal_value,
        "terminal_discount_factor": terminal_discount_factor,
        "terminal_present_value": terminal_present_value,
        "terminal_value_share": terminal_present_value / equity_value,
        "equity_value": equity_value,
        "per_share_value": equity_value / diluted_shares,
    }


def solve_implied_fcf_growth(
    *,
    target_price: float,
    base_fcf: float,
    diluted_shares: float,
    discount_rate: float,
    terminal_growth: float,
    years: int = 5,
    lower_bound: float = -0.90,
    upper_bound: float = 3.00,
) -> dict[str, Any]:
    """Solve the constant FCF CAGR implied by a market price without fitting it."""

    target_price = _finite_positive(target_price, "target_price")
    low, high = float(lower_bound), float(upper_bound)

    def price_at(growth: float) -> float:
        return float(
            equity_fcf_schedule(
                base_fcf=base_fcf,
                growth_rate=growth,
                discount_rate=discount_rate,
                terminal_growth=terminal_growth,
                diluted_shares=diluted_shares,
                years=years,
            )["per_share_value"]
        )

    low_price, high_price = price_at(low), price_at(high)
    if not low_price <= target_price <= high_price:
        return {
            "status": "not_bracketed",
            "target_price": target_price,
            "lower_bound": low,
            "upper_bound": high,
            "lower_bound_price": low_price,
            "upper_bound_price": high_price,
        }
    for _ in range(160):
        middle = (low + high) / 2
        if price_at(middle) < target_price:
            low = middle
        else:
            high = middle
    growth = (low + high) / 2
    audit = equity_fcf_schedule(
        base_fcf=base_fcf,
        growth_rate=growth,
        discount_rate=discount_rate,
        terminal_growth=terminal_growth,
        diluted_shares=diluted_shares,
        years=years,
    )
    return {
        "status": "solved",
        "target_price": target_price,
        "target_equity_value": target_price * diluted_shares,
        "implied_fcf_growth": growth,
        "implied_year_5_fcf": audit["schedule"][-1]["cash_flow"],
        "terminal_value_share": audit["terminal_value_share"],
        "reconstructed_price": audit["per_share_value"],
        "solver_bounds": [lower_bound, upper_bound],
    }


def reverse_dcf_sensitivity(
    *,
    target_price: float,
    base_fcf: float,
    diluted_shares: float,
    discount_rates: Iterable[float],
    terminal_growth_rates: Iterable[float],
    years: int = 5,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for discount_rate in discount_rates:
        for terminal_growth in terminal_growth_rates:
            solved = solve_implied_fcf_growth(
                target_price=target_price,
                base_fcf=base_fcf,
                diluted_shares=diluted_shares,
                discount_rate=float(discount_rate),
                terminal_growth=float(terminal_growth),
                years=years,
            )
            rows.append(
                {
                    "discount_rate": float(discount_rate),
                    "terminal_growth": float(terminal_growth),
                    **solved,
                }
            )
    return rows

