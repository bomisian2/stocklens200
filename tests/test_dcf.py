from __future__ import annotations

import pytest

from stocklens.dcf import equity_fcf_schedule, solve_implied_fcf_growth


def test_equity_proxy_value_is_reproducible_and_monotonic() -> None:
    inputs = {
        "base_fcf": 5_623.0,
        "growth_rate": 0.10,
        "discount_rate": 0.11,
        "terminal_growth": 0.025,
        "diluted_shares": 793.597443,
        "years": 5,
    }
    first = equity_fcf_schedule(**inputs)
    second = equity_fcf_schedule(**inputs)
    higher_growth = equity_fcf_schedule(**{**inputs, "growth_rate": 0.20})
    higher_discount = equity_fcf_schedule(**{**inputs, "discount_rate": 0.13})

    assert first == second
    assert higher_growth["per_share_value"] > first["per_share_value"]
    assert higher_discount["per_share_value"] < first["per_share_value"]
    assert first["enterprise_value"] is None
    assert first["enterprise_to_equity_bridge_applied"] is False


def test_equity_proxy_rejects_invalid_terminal_relation_and_cash_flow() -> None:
    with pytest.raises(ValueError, match="discount_rate must exceed terminal_growth"):
        equity_fcf_schedule(
            base_fcf=1.0,
            growth_rate=0.1,
            discount_rate=0.03,
            terminal_growth=0.03,
            diluted_shares=1.0,
        )
    with pytest.raises(ValueError, match="base_fcf must be a finite positive number"):
        equity_fcf_schedule(
            base_fcf=-1.0,
            growth_rate=0.1,
            discount_rate=0.1,
            terminal_growth=0.02,
            diluted_shares=1.0,
        )


def test_implied_growth_reconstructs_price_without_becoming_a_forecast() -> None:
    solved = solve_implied_fcf_growth(
        target_price=497.53,
        base_fcf=5_623.0,
        diluted_shares=793.597443,
        discount_rate=0.11,
        terminal_growth=0.025,
    )

    assert solved["status"] == "solved"
    assert solved["reconstructed_price"] == pytest.approx(497.53)
    assert solved["implied_fcf_growth"] > 0

