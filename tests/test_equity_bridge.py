from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_googl_equity_bridge import build_audit
from stocklens.equity_bridge import (
    assess_equity_bridge,
    assess_share_denominator,
    per_share_balance_sensitivity,
    validate_accounting_consistency,
    validate_bridge_item,
)


ROOT = Path(__file__).resolve().parents[1]


def _item(**overrides: object) -> dict:
    item = {
        "id": "cash",
        "value_usd": 10.0,
        "state": "official_observed",
        "bridge_treatment": "add",
        "formal_eligible": True,
        "claim_class": "cash",
        "source_ids": ["issuer"],
    }
    item.update(overrides)
    return item


def _shares(**overrides: object) -> dict:
    shares = {
        "value": 10.0,
        "kind": "point_in_time_fully_diluted",
        "treasury_stock_method_complete": True,
        "preferred_policy_complete": True,
        "includes_preferred_if_converted": False,
    }
    shares.update(overrides)
    return shares


def test_missing_bridge_value_cannot_be_zero_filled_or_subtracted() -> None:
    with pytest.raises(ValueError, match="marked missing must have a null value"):
        validate_bridge_item(
            _item(value_usd=0.0, state="missing", bridge_treatment="unresolved", formal_eligible=False)
        )
    with pytest.raises(ValueError, match="cannot be added or subtracted"):
        validate_bridge_item(
            _item(value_usd=None, state="missing", bridge_treatment="subtract", formal_eligible=False)
        )


def test_reported_balance_can_remain_bridge_ineligible() -> None:
    item = validate_bridge_item(_item(bridge_treatment="unresolved", formal_eligible=False))
    assert item["state"] == "official_observed"
    assert item["formal_eligible"] is False


def test_operating_lease_subtraction_rejects_unadjusted_gaap_ebit() -> None:
    errors = validate_accounting_consistency(
        items=[_item(claim_class="operating_lease", bridge_treatment="subtract")],
        fcff_lease_basis="gaap_ebit_unadjusted",
        preferred_treatment="unresolved",
        share_denominator=_shares(),
    )
    assert any("operating lease" in error for error in errors)


def test_preferred_claim_and_if_converted_dilution_cannot_both_apply() -> None:
    errors = validate_accounting_consistency(
        items=[_item(claim_class="convertible_preferred", bridge_treatment="subtract")],
        fcff_lease_basis="lease_recast",
        preferred_treatment="claim",
        share_denominator=_shares(includes_preferred_if_converted=True),
    )
    assert any("both subtracted" in error for error in errors)
    assert any("excludes conversion shares" in error for error in errors)


def test_period_weighted_average_shares_are_not_point_in_time_denominator() -> None:
    result = assess_share_denominator(
        _shares(kind="period_weighted_average_not_point_in_time")
    )
    assert result["eligible_for_per_share_value"] is False
    assert "point-in-time" in result["reason"]


def test_research_ev_does_not_produce_partial_common_equity_or_per_share_value() -> None:
    result = assess_equity_bridge(
        enterprise_value_range={"low": 100.0, "high": 200.0},
        items=[_item(bridge_treatment="unresolved", formal_eligible=False)],
        share_denominator=_shares(value=None, kind="missing", treasury_stock_method_complete=False),
        fcff_lease_basis="gaap_ebit_unadjusted",
        preferred_treatment="unresolved",
    )
    assert result["enterprise_value_grade"] == "B"
    assert result["common_equity_grade"] == "C"
    assert result["per_share_grade"] == "C"
    assert result["common_equity_value_range"] is None
    assert result["per_share_value_range"] is None
    assert result["operational_use"] is False


def test_mechanical_scale_is_explicitly_not_fair_value() -> None:
    result = per_share_balance_sensitivity(10_000_000_000, 12_230_000_000)
    assert result["mechanical_usd_per_issued_share"] == pytest.approx(0.8176614881)
    assert result["is_fair_value"] is False
    assert result["is_diluted_per_share_value"] is False


def test_committed_v24_evidence_keeps_per_share_value_unavailable() -> None:
    evidence_path = ROOT / "docs" / "evidence" / "googl-equity-bridge-v2.4.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["operational_use"] is False
    assert evidence["share_counts"]["formal_point_in_time_fully_diluted"]["value"] is None

    audit = build_audit(
        evidence_path,
        ROOT / "docs" / "evidence" / "googl-scenarios-v2.3.json",
        ROOT / "data" / "universe.csv",
    )
    assert audit["value_status"]["A_verified_value_range"] is None
    assert audit["value_status"]["B_research_reference"]["level"] == "enterprise_value_only"
    assert audit["value_status"]["C_per_share_unavailable"]["status"] == "active"
    assert audit["formal_results"]["formal_per_share_value"] is None
    assert audit["formal_results"]["operational_buy_signal"] is None
    assert audit["source_policy"]["automated_sec_input"] is False
