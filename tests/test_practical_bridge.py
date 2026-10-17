from __future__ import annotations

from pathlib import Path

import pytest

from scripts.audit_googl_practical_value import build_audit
from stocklens.practical_bridge import calculate_research_bridge_envelope, rank_value_drivers


ROOT = Path(__file__).resolve().parents[1]


def _asset(item_id: str, low: float, high: float) -> dict:
    return {
        "id": item_id,
        "low": low,
        "high": high,
        "basis": "official balance with explicit mechanical recognition range",
        "rationale": "test-only research range",
    }


def test_research_bridge_is_non_operational_and_has_no_midpoint() -> None:
    result = calculate_research_bridge_envelope(
        enterprise_value_range={"low": 100.0, "high": 200.0},
        additive_asset_ranges=[_asset("cash", 0.0, 50.0)],
        claim_ranges=[_asset("debt", 20.0, 20.0)],
        share_range={
            "low": 10.0,
            "high": 12.0,
            "basis": "issued-to-dilution-bound",
            "rationale": "test-only bound",
        },
        preferred_policy="claim",
    )
    assert result["status"] == "B_research_reference_envelope"
    assert result["common_equity_range"] == {"low": 80.0, "high": 230.0}
    assert result["per_share_reference_range"]["low"] == pytest.approx(80.0 / 12.0)
    assert result["per_share_reference_range"]["high"] == pytest.approx(23.0)
    assert result["no_selected_midpoint"] is True
    assert result["formal_fair_value"] is None
    assert result["operational_use"] is False


def test_missing_rationale_or_invalid_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="basis and rationale"):
        calculate_research_bridge_envelope(
            enterprise_value_range={"low": 100.0, "high": 200.0},
            additive_asset_ranges=[{"id": "cash", "low": 0.0, "high": 10.0, "basis": "official"}],
            claim_ranges=[],
            share_range={"low": 10.0, "high": 10.0},
            preferred_policy="claim",
        )
    with pytest.raises(ValueError, match="0 <= low <= high"):
        calculate_research_bridge_envelope(
            enterprise_value_range={"low": 200.0, "high": 100.0},
            additive_asset_ranges=[],
            claim_ranges=[],
            share_range={"low": 10.0, "high": 10.0},
            preferred_policy="claim",
        )


def test_value_drivers_are_ranked_without_becoming_targets() -> None:
    ranked = rank_value_drivers(
        [
            {"name": "small", "low_value_usd": 10.0, "high_value_usd": 20.0, "comparison_shares": 10.0},
            {"name": "large", "low_value_usd": 0.0, "high_value_usd": 50.0, "comparison_shares": 10.0},
        ]
    )
    assert [item["name"] for item in ranked] == ["large", "small"]
    assert ranked[0]["impact_rank"] == 1
    assert ranked[0]["is_price_target"] is False


def test_committed_practical_audit_produces_only_b_grade_reference() -> None:
    audit = build_audit(
        ROOT / "docs" / "evidence" / "googl-equity-bridge-v2.4.json",
        ROOT / "docs" / "evidence" / "googl-scenarios-v2.3.json",
        ROOT / "data" / "universe.csv",
    )
    reference = audit["value_levels"]["B_research_per_share_reference"]
    assert audit["value_levels"]["A_formal_value"] is None
    assert reference["range"]["low"] < reference["range"]["high"]
    assert reference["operational_use"] is False
    assert reference["claim_branch"]["preferred_policy"] == "claim"
    assert reference["if_converted_branch"]["preferred_policy"] == "if_converted"
    assert audit["official_source_integrity"]["local_acquisition_http_status"] == 403
    assert audit["official_source_integrity"]["local_source_sha256"] is None
    assert len(audit["largest_three_uncertainties"]) == 3
    assert audit["formal_results"]["formal_fair_value"] is None
    assert audit["formal_results"]["operational_buy_signal"] is None
