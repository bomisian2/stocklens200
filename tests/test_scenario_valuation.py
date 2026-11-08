from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_googl_scenarios import build_audit
from stocklens.scenario_valuation import (
    discount_fcff_sensitivity,
    market_expectations_status,
    project_revenue_to_fcff,
)


ROOT = Path(__file__).resolve().parents[1]


def _assumption(value: float, rationale: str = "test-only research assumption") -> dict:
    return {
        "value": value,
        "state": "model_assumption",
        "source_ids": [],
        "rationale": rationale,
    }


def _year(**overrides: float) -> dict:
    values = {
        "revenue_growth": 0.10,
        "operating_margin": 0.30,
        "operating_tax_rate": 0.20,
        "depreciation_amortization_to_revenue": 0.05,
        "capex_to_revenue": 0.10,
        "change_operating_working_capital_to_incremental_revenue": 0.02,
        "growth_capex_lag_years": 2,
    }
    values.update(overrides)
    return {name: _assumption(value) for name, value in values.items()}


def _projection(years: int = 5) -> dict:
    return project_revenue_to_fcff(
        base_revenue={
            "value": 100.0,
            "state": "observed",
            "source_ids": ["official_release"],
        },
        years=[_year() for _ in range(years)],
        reinvestment_efficiency_ceiling=_assumption(4.0),
    )


def test_projection_keeps_observations_and_assumptions_separate() -> None:
    result = _projection()
    assert result["status"] == "research_scenario_complete"
    assert result["formal_fcff"] is None
    assert result["operational_use"] is False
    assert result["evidence_states_used"] == ["model_assumption", "observed"]
    assert result["years"][0]["fcff"] == pytest.approx(20.7)


def test_missing_or_unsourced_observed_value_cannot_be_silently_used() -> None:
    with pytest.raises(ValueError, match="marked missing must have a null value"):
        project_revenue_to_fcff(
            base_revenue={"value": 0.0, "state": "missing", "source_ids": []},
            years=[_year()],
            reinvestment_efficiency_ceiling=_assumption(4.0),
        )
    with pytest.raises(ValueError, match="requires a source"):
        project_revenue_to_fcff(
            base_revenue={"value": 100.0, "state": "observed", "source_ids": []},
            years=[_year()],
            reinvestment_efficiency_ceiling=_assumption(4.0),
        )


def test_high_growth_low_reinvestment_path_is_rejected() -> None:
    result = project_revenue_to_fcff(
        base_revenue={
            "value": 100.0,
            "state": "observed",
            "source_ids": ["official_release"],
        },
        years=[
            _year(
                revenue_growth=0.20,
                depreciation_amortization_to_revenue=0.10,
                capex_to_revenue=0.02,
                change_operating_working_capital_to_incremental_revenue=0.0,
            )
        ],
        reinvestment_efficiency_ceiling=_assumption(4.0),
    )
    assert result["status"] == "rejected"
    assert result["years"][0]["fcff"] is None
    assert "non-positive net reinvestment" in result["years"][0]["contradiction"]


def test_wacc_terminal_grid_has_no_default_and_marks_unstable_cells() -> None:
    result = discount_fcff_sensitivity(
        projection=_projection(),
        wacc_candidates=[_assumption(0.09), _assumption(0.12)],
        terminal_growth_candidates=[_assumption(0.02), _assumption(0.08)],
    )
    assert result["status"] == "research_sensitivity_complete"
    assert result["no_privileged_default"] is True
    assert result["formal_wacc"] is None
    assert result["formal_per_share_value"] is None
    assert any(row["status"] == "unstable" for row in result["rows"])


def test_market_price_does_not_identify_a_unique_operating_scenario() -> None:
    result = market_expectations_status(
        current_price={"value": 351.66, "state": "observed", "source_ids": ["snapshot"]},
        diluted_shares={"value": 12.274, "state": "observed", "source_ids": ["issuer"]},
        missing_equity_bridge_components=["NCI", "other claims"],
    )
    assert result["status"] == "underdetermined"
    assert result["target_enterprise_value"] is None
    assert result["is_forecast"] is False


def test_committed_googl_v23_evidence_and_audit_remain_non_operational() -> None:
    evidence_path = ROOT / "docs" / "evidence" / "googl-scenarios-v2.3.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["operational_use"] is False
    assert evidence["issuer_guidance"]["revenue_growth"]["state"] == "missing"
    assert evidence["wacc_review"]["formal_wacc"] is None

    audit = build_audit(evidence_path, ROOT / "data" / "universe.csv")
    assert len(audit["scenario_results"]) == 3
    assert all(item["operational_use"] is False for item in audit["scenario_results"])
    assert audit["formal_results"]["formal_per_share_value"] is None
    assert audit["formal_results"]["operational_buy_signal"] is None
    assert audit["formal_results"]["status"] == "hold"
    assert audit["market_expectations"]["status"] == "underdetermined"
