from __future__ import annotations

import pytest

from stocklens.fcff import (
    enterprise_fcff_schedule,
    enterprise_to_equity_bridge,
    fcff_components,
    operating_fcff_projection,
    operating_tax_evidence,
    wacc_evidence,
    working_capital_evidence,
)


def test_googl_tax_evidence_does_not_promote_adjusted_total_rate_to_operating_rate() -> None:
    evidence = operating_tax_evidence(
        operating_income=147.628,
        pretax_income=299.269,
        income_tax=55.064,
        identified_one_time_pretax_gain=135.946,
        identified_one_time_after_tax_gain=105.8,
    )
    assert evidence["reported_effective_tax_rate"] == pytest.approx(55.064 / 299.269)
    assert evidence["event_adjusted_total_effective_tax_rate"] == pytest.approx(24.918 / 163.323)
    assert evidence["verified_operating_tax_rate"] is None
    assert evidence["verified_nopat"] is None
    assert evidence["formal_nopat_eligible"] is False


def test_fcff_is_withheld_when_tax_or_operating_working_capital_is_unverified() -> None:
    result = fcff_components(
        operating_income=147.628,
        depreciation_amortization=25.237,
        capex=132.402,
        verified_operating_tax_rate=None,
        verified_change_operating_working_capital=None,
        research_tax_rate=24.918 / 163.323,
    )
    assert result["status"] == "incomplete"
    assert result["formal_fcff"] is None
    assert result["research_pre_working_capital_fcff_proxy"] == pytest.approx(
        147.628 * (1 - 24.918 / 163.323) + 25.237 - 132.402
    )
    assert result["research_proxy_operational_use"] is False


def test_fcff_formula_uses_nopat_depreciation_capex_and_increase_in_nwc() -> None:
    result = fcff_components(
        operating_income=100,
        depreciation_amortization=12,
        capex=20,
        verified_operating_tax_rate=0.20,
        verified_change_operating_working_capital=5,
    )
    assert result["status"] == "verified"
    assert result["verified_nopat"] == 80
    assert result["formal_fcff"] == 67


def test_enterprise_fcff_is_discounted_at_wacc_and_stops_before_equity_bridge() -> None:
    result = enterprise_fcff_schedule(
        base_fcff=100,
        growth_rate=0.05,
        wacc=0.10,
        terminal_growth=0.02,
        years=5,
    )
    expected_explicit = sum(100 * (1.05**year) / (1.10**year) for year in range(1, 6))
    expected_terminal = 100 * (1.05**5) * 1.02 / (0.10 - 0.02) / (1.10**5)
    assert result["explicit_present_value"] == pytest.approx(expected_explicit)
    assert result["enterprise_value"] == pytest.approx(expected_explicit + expected_terminal)
    assert result["discount_rate_type"] == "wacc"
    assert result["equity_value"] is None
    assert result["per_share_value"] is None


def test_enterprise_fcff_rejects_terminal_growth_at_or_above_wacc() -> None:
    with pytest.raises(ValueError, match="WACC above terminal growth"):
        enterprise_fcff_schedule(
            base_fcff=100,
            growth_rate=0.05,
            wacc=0.03,
            terminal_growth=0.03,
        )


def test_working_capital_gate_rejects_missing_inventory_and_mixed_current_accounts() -> None:
    result = working_capital_evidence(
        current_operating_assets={"accounts_receivable": 69.175, "inventory": 9.991},
        current_operating_liabilities={
            "accounts_payable": 20.258,
            "accrued_compensation": 15.086,
            "accrued_revenue_share": 10.599,
            "deferred_revenue": 7.154,
        },
        prior_operating_assets={"accounts_receivable": 55.048, "inventory": None},
        prior_operating_liabilities={
            "accounts_payable": 8.347,
            "accrued_compensation": 12.168,
            "accrued_revenue_share": 9.787,
            "deferred_revenue": 4.969,
        },
        unclassified_fields=("other_current_assets", "accrued_expenses_and_other_current_liabilities"),
    )
    assert result["status"] == "incomplete"
    assert "prior.assets.inventory" in result["missing_components"]
    assert result["verified_change_operating_working_capital"] is None


def test_wacc_requires_verified_beta_erp_debt_yield_tax_and_market_weights() -> None:
    incomplete = wacc_evidence(
        risk_free_rate=0.0524,
        beta=None,
        equity_risk_premium=0.05,
        pretax_cost_of_debt=None,
        marginal_tax_rate=None,
        equity_market_value=4316.27484,
        debt_market_value=None,
        verified_components=("risk_free_rate", "equity_market_value"),
        legacy_data_quality_premium=0.015,
    )
    assert incomplete["status"] == "incomplete"
    assert incomplete["wacc"] is None
    assert incomplete["legacy_cost_of_equity_proxy"] == pytest.approx(0.1174)
    assert incomplete["legacy_proxy_operational_use"] is False

    verified = wacc_evidence(
        risk_free_rate=0.04,
        beta=1.1,
        equity_risk_premium=0.05,
        pretax_cost_of_debt=0.06,
        marginal_tax_rate=0.21,
        equity_market_value=900,
        debt_market_value=100,
        verified_components=(
            "risk_free_rate", "beta", "equity_risk_premium", "pretax_cost_of_debt",
            "marginal_tax_rate", "equity_market_value", "debt_market_value",
        ),
    )
    assert verified["status"] == "verified"
    assert verified["wacc"] == pytest.approx(0.9 * 0.095 + 0.1 * 0.06 * 0.79)


def test_enterprise_bridge_never_silently_fills_unknown_claims_with_zero() -> None:
    incomplete = enterprise_to_equity_bridge(
        enterprise_value=None,
        cash=55.911,
        marketable_securities=186.563,
        debt=98.165,
        preferred_equity=18.023,
        noncontrolling_interest=None,
        debt_like_lease_liabilities=14.591,
        other_verified_nonoperating_assets=None,
        other_verified_potential_claims=None,
        diluted_shares=12.274,
    )
    assert incomplete["status"] == "incomplete"
    assert incomplete["common_equity_value"] is None
    assert "noncontrolling_interest" in incomplete["missing_components"]

    verified = enterprise_to_equity_bridge(
        enterprise_value=1000,
        cash=50,
        marketable_securities=100,
        debt=200,
        preferred_equity=10,
        noncontrolling_interest=5,
        debt_like_lease_liabilities=15,
        other_verified_nonoperating_assets=20,
        other_verified_potential_claims=10,
        diluted_shares=10,
    )
    assert verified["common_equity_value"] == 930
    assert verified["per_share_value"] == 93


def test_operating_projection_blocks_missing_inputs_and_growth_without_reinvestment_evidence() -> None:
    incomplete = operating_fcff_projection(
        base_revenue=100,
        scenarios=[{"revenue_growth": 0.10, "operating_margin": 0.30}],
    )
    assert incomplete["status"] == "underdetermined"
    assert incomplete["years"][0]["fcff"] is None

    contradictory = operating_fcff_projection(
        base_revenue=100,
        scenarios=[{
            "revenue_growth": 0.10,
            "operating_margin": 0.30,
            "operating_tax_rate": 0.20,
            "depreciation_amortization": 12,
            "capex": 10,
            "change_operating_working_capital": 0,
            "evidence_verified": True,
        }],
    )
    assert contradictory["years"][0]["status"] == "contradictory"
    assert contradictory["years"][0]["fcff"] is None

    valid = operating_fcff_projection(
        base_revenue=100,
        scenarios=[{
            "revenue_growth": 0.10,
            "operating_margin": 0.30,
            "operating_tax_rate": 0.20,
            "depreciation_amortization": 10,
            "capex": 20,
            "change_operating_working_capital": 2,
            "evidence_verified": True,
        }],
    )
    assert valid["status"] == "research_scenario_complete"
    assert valid["years"][0]["fcff"] == pytest.approx(14.4)
