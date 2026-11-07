from __future__ import annotations

import pytest

from stocklens.dcf import equity_fcf_schedule, reverse_dcf_sensitivity, solve_implied_fcf_growth


def test_equity_fcf_schedule_reproduces_googl_research_base() -> None:
    result = equity_fcf_schedule(
        base_fcf=53.273,
        growth_rate=0.096954,
        discount_rate=0.1174,
        terminal_growth=0.025,
        diluted_shares=12.274,
    )
    assert result["per_share_value"] == pytest.approx(64.43968035)
    assert result["explicit_present_value"] == pytest.approx(252.0951598)
    assert result["terminal_present_value"] == pytest.approx(538.83747686)
    assert result["terminal_value_share"] == pytest.approx(0.68126848)
    assert result["enterprise_value"] is None
    assert result["enterprise_to_equity_bridge_applied"] is False


def test_reverse_dcf_reconstructs_market_price_without_changing_base_model() -> None:
    result = solve_implied_fcf_growth(
        target_price=351.66,
        base_fcf=53.273,
        diluted_shares=12.274,
        discount_rate=0.1174,
        terminal_growth=0.025,
    )
    assert result["status"] == "solved"
    assert result["implied_fcf_growth"] == pytest.approx(0.5901914355)
    assert result["reconstructed_price"] == pytest.approx(351.66)
    assert result["target_equity_value"] == pytest.approx(4316.27484)


def test_reverse_dcf_grid_keeps_discount_and_terminal_assumptions_visible() -> None:
    rows = reverse_dcf_sensitivity(
        target_price=351.66,
        base_fcf=53.273,
        diluted_shares=12.274,
        discount_rates=(0.09, 0.1174),
        terminal_growth_rates=(0.015, 0.025),
    )
    assert len(rows) == 4
    assert all(row["status"] == "solved" for row in rows)
    assert len({row["implied_fcf_growth"] for row in rows}) == 4


def test_dcf_rejects_terminal_growth_at_or_above_discount_rate() -> None:
    with pytest.raises(ValueError, match="discount_rate must exceed"):
        equity_fcf_schedule(
            base_fcf=1,
            growth_rate=0.05,
            discount_rate=0.03,
            terminal_growth=0.03,
            diluted_shares=1,
        )

