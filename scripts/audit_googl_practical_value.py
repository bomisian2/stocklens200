from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_googl_scenarios import (  # noqa: E402
    _build_projection,
    _sensitivity,
    build_audit as build_scenario_audit,
)
from stocklens.practical_bridge import (  # noqa: E402
    calculate_research_bridge_envelope,
    rank_value_drivers,
)


DIAGNOSTIC_WACC = 0.135
DIAGNOSTIC_TERMINAL_GROWTH = 0.015
WACC_SLICE_TERMINAL_GROWTH = 0.02


def _item_value(evidence: dict[str, Any], item_id: str) -> float:
    item = next(item for item in evidence["bridge_items"] if item["id"] == item_id)
    return float(item["value_usd"])


def _grid_value(sensitivity: dict[str, Any], wacc: float, growth: float) -> float:
    rows = [
        row
        for row in sensitivity["rows"]
        if float(row["wacc_fact"]["value"]) == wacc
        and float(row["terminal_growth_fact"]["value"]) == growth
    ]
    if len(rows) != 1 or rows[0].get("enterprise_value") is None:
        raise ValueError(f"one computable sensitivity cell is required for WACC={wacc}, g={growth}")
    return float(rows[0]["enterprise_value"])


def _scenario_spec(evidence: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [spec for spec in evidence["scenarios"] if spec["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"one {name} scenario is required")
    return matches[0]


def _isolated_operating_sensitivity(scenario_evidence: dict[str, Any]) -> dict[str, float]:
    reference = deepcopy(_scenario_spec(scenario_evidence, "reference_research"))
    downside = _scenario_spec(scenario_evidence, "downside_research")
    upside = _scenario_spec(scenario_evidence, "upside_research")
    values: dict[str, float] = {}
    for label, source in (("low", downside), ("high", upside)):
        spec = deepcopy(reference)
        spec["revenue_growth"] = list(source["revenue_growth"])
        spec["operating_margin"] = list(source["operating_margin"])
        spec["assumption_basis"] = (
            "Isolated diagnostic: existing v2.3 revenue-growth and operating-margin path "
            "with all other reference-research inputs held constant."
        )
        sensitivity = _sensitivity(scenario_evidence, _build_projection(scenario_evidence, spec))
        values[label] = _grid_value(
            sensitivity, DIAGNOSTIC_WACC, DIAGNOSTIC_TERMINAL_GROWTH
        )
    return values


def _isolated_post_2026_capex_sensitivity(
    scenario_evidence: dict[str, Any],
) -> dict[str, float]:
    reference = deepcopy(_scenario_spec(scenario_evidence, "reference_research"))
    high_capex = _scenario_spec(scenario_evidence, "downside_research")["capex_to_revenue"]
    low_capex = _scenario_spec(scenario_evidence, "upside_research")["capex_to_revenue"]
    values: dict[str, float] = {}
    for label, source in (("low", high_capex), ("high", low_capex)):
        spec = deepcopy(reference)
        spec["capex_to_revenue"] = [reference["capex_to_revenue"][0], *source[1:]]
        spec["assumption_basis"] = (
            "Isolated diagnostic: existing v2.3 post-2026 CAPEX path with year-one official "
            "guidance linkage and all other reference-research inputs held constant."
        )
        sensitivity = _sensitivity(scenario_evidence, _build_projection(scenario_evidence, spec))
        values[label] = _grid_value(
            sensitivity, DIAGNOSTIC_WACC, DIAGNOSTIC_TERMINAL_GROWTH
        )
    return values


def build_audit(
    bridge_evidence_path: Path,
    scenario_evidence_path: Path,
    universe_path: Path,
) -> dict[str, Any]:
    bridge_evidence = json.loads(bridge_evidence_path.read_text(encoding="utf-8"))
    scenario_evidence = json.loads(scenario_evidence_path.read_text(encoding="utf-8"))
    scenario_audit = build_scenario_audit(scenario_evidence_path, universe_path)
    if bridge_evidence.get("ticker") != "GOOGL" or scenario_evidence.get("ticker") != "GOOGL":
        raise ValueError("GOOGL evidence is required")
    if bridge_evidence.get("operational_use") is not False:
        raise ValueError("bridge evidence must remain non-operational")

    reference_ev = scenario_audit["research_enterprise_value_ranges"]["reference_research"]
    assets = [
        {
            "id": item_id,
            "low": 0.0,
            "high": _item_value(bridge_evidence, item_id),
            "basis": "official carrying balance with zero-to-full mechanical recognition",
            "rationale": (
                "No probability or preferred haircut is selected because operating-cash, tax, "
                "liquidity, restriction and strategic-use evidence is incomplete."
            ),
        }
        for item_id in (
            "cash_and_equivalents",
            "current_marketable_securities",
            "noncurrent_marketable_equity",
            "nonmarketable_securities",
        )
    ]
    debt = _item_value(bridge_evidence, "interest_bearing_debt_book")
    nci = _item_value(bridge_evidence, "noncontrolling_interests")
    preferred = _item_value(bridge_evidence, "mandatory_convertible_preferred")
    issued = float(bridge_evidence["share_counts"]["issued_outstanding_common"]["value"])
    awards = float(bridge_evidence["share_counts"]["unvested_rsus_and_psus"]["value"])
    preferred_conversion_upper = 19_000_000 * 2.8420

    common_claims = [
        {
            "id": "interest_bearing_debt_book",
            "low": debt,
            "high": debt,
            "basis": "official current plus long-term carrying amount",
            "rationale": "Book proxy is used identically at both endpoints; formal market-value debt remains missing.",
        },
        {
            "id": "noncontrolling_interests",
            "low": nci,
            "high": nci,
            "basis": "official carrying amount used as a research proxy",
            "rationale": "Economic value is unresolved, so no model-created NCI premium or discount is used.",
        },
    ]
    claim_bridge = calculate_research_bridge_envelope(
        enterprise_value_range=reference_ev,
        additive_asset_ranges=assets,
        claim_ranges=[
            *common_claims,
            {
                "id": "mandatory_convertible_preferred_claim",
                "low": preferred,
                "high": preferred,
                "basis": "official liquidation preference",
                "rationale": "Claim branch subtracts the preferred claim and excludes conversion dilution.",
            },
        ],
        share_range={
            "low": issued,
            "high": issued + awards,
            "basis": "issued common shares to issued-plus-all-unvested-awards mechanical bound",
            "rationale": "Award count is an upper dilution bound, not treasury-stock-method incremental shares.",
        },
        preferred_policy="claim",
    )
    converted_bridge = calculate_research_bridge_envelope(
        enterprise_value_range=reference_ev,
        additive_asset_ranges=assets,
        claim_ranges=common_claims,
        share_range={
            "low": issued,
            "high": issued + awards + preferred_conversion_upper,
            "basis": "issued shares to awards-plus-maximum-preferred-conversion mechanical bound",
            "rationale": (
                "The upper bound uses the maximum disclosed conversion ratio and all awards; capped-call "
                "offset and treasury-stock effects are not treated as known."
            ),
        },
        preferred_policy="if_converted",
    )
    combined_reference = {
        "low": min(
            claim_bridge["per_share_reference_range"]["low"],
            converted_bridge["per_share_reference_range"]["low"],
        ),
        "high": max(
            claim_bridge["per_share_reference_range"]["high"],
            converted_bridge["per_share_reference_range"]["high"],
        ),
    }

    reference_sensitivity = next(
        item
        for item in scenario_audit["scenario_results"]
        if item["name"] == "reference_research"
    )["enterprise_value_sensitivity"]
    wacc_values = {
        "low": _grid_value(reference_sensitivity, 0.15, WACC_SLICE_TERMINAL_GROWTH),
        "high": _grid_value(reference_sensitivity, 0.09, WACC_SLICE_TERMINAL_GROWTH),
    }
    operating_values = _isolated_operating_sensitivity(scenario_evidence)
    post_capex_values = _isolated_post_2026_capex_sensitivity(scenario_evidence)
    comparison_shares = (issued + issued + awards + preferred_conversion_upper) / 2
    investment_span = sum(item["high"] - item["low"] for item in assets)
    claim_span = debt + preferred
    bridge_equity_midpoint = (
        claim_bridge["common_equity_range"]["low"]
        + claim_bridge["common_equity_range"]["high"]
    ) / 2
    dilution_low_per_share = bridge_equity_midpoint / (issued + awards + preferred_conversion_upper)
    dilution_high_per_share = bridge_equity_midpoint / issued
    dilution_equivalent_span = (
        dilution_high_per_share - dilution_low_per_share
    ) * comparison_shares

    ranked = rank_value_drivers(
        [
            {
                "name": "five_year_revenue_growth_and_operating_margin",
                "low_value_usd": operating_values["low"],
                "high_value_usd": operating_values["high"],
                "comparison_shares": comparison_shares,
                "evidence": "existing v2.3 explicit research paths; not issuer guidance",
                "slice": f"WACC={DIAGNOSTIC_WACC:.3f}, terminal_growth={DIAGNOSTIC_TERMINAL_GROWTH:.3f}",
            },
            {
                "name": "wacc",
                "low_value_usd": wacc_values["low"],
                "high_value_usd": wacc_values["high"],
                "comparison_shares": comparison_shares,
                "evidence": "9%-15% v2.3 research grid; formal beta and WACC remain missing",
                "slice": f"reference operating path, terminal_growth={WACC_SLICE_TERMINAL_GROWTH:.3f}",
            },
            {
                "name": "investment_asset_economic_recognition",
                "low_value_usd": 0.0,
                "high_value_usd": investment_span,
                "comparison_shares": comparison_shares,
                "evidence": "official carrying balances with zero-to-full mechanical recognition",
                "slice": "no selected haircut or probability",
            },
            {
                "name": "post_2026_capex_and_reinvestment",
                "low_value_usd": post_capex_values["low"],
                "high_value_usd": post_capex_values["high"],
                "comparison_shares": comparison_shares,
                "evidence": "official 2026 guide plus existing v2.3 post-2026 research paths",
                "slice": f"WACC={DIAGNOSTIC_WACC:.3f}, terminal_growth={DIAGNOSTIC_TERMINAL_GROWTH:.3f}",
            },
            {
                "name": "financial_debt_and_preferred_claim",
                "low_value_usd": 0.0,
                "high_value_usd": claim_span,
                "comparison_shares": comparison_shares,
                "evidence": "official carrying debt and preferred liquidation preference",
                "slice": "mechanical inclusion-versus-omission impact, not uncertainty in existence",
            },
            {
                "name": "share_dilution_bound",
                "low_value_usd": 0.0,
                "high_value_usd": dilution_equivalent_span,
                "comparison_shares": comparison_shares,
                "evidence": "issued shares to awards-plus-maximum-conversion mechanical bound",
                "slice": "capped-call and treasury-stock effects unresolved",
            },
        ]
    )

    filing = bridge_evidence["sources"]["alphabet_2026_q2_10q"]
    return {
        "schema_version": 1,
        "model_version": "googl-practical-reference-audit-v2.4.1",
        "ticker": "GOOGL",
        "official_source_integrity": {
            "accession": filing["accession"],
            "period_end": filing["period_end"],
            "filed": filing["filing_date"],
            "accepted_at_utc": filing["accepted_at_utc"],
            "reported_document_size_bytes": filing["reported_document_size_bytes"],
            "local_source_sha256": filing["local_acquisition_attempt"]["source_file_sha256"],
            "local_acquisition_http_status": filing["local_acquisition_attempt"]["http_status"],
            "retry_count": filing["local_acquisition_attempt"]["retry_count"],
            "raw_file_operationally_verified": False,
        },
        "value_levels": {
            "A_formal_value": None,
            "B_research_per_share_reference": {
                "range": combined_reference,
                "claim_branch": claim_bridge,
                "if_converted_branch": converted_bridge,
                "display_eligible_with_full_assumption_disclosure": True,
                "operational_use": False,
            },
            "C_unavailable": False,
        },
        "driver_ranking": ranked,
        "largest_three_uncertainties": [item["name"] for item in ranked[:3]],
        "bridge_policy": {
            "operating_leases": "not separately subtracted while FCFF uses unadjusted GAAP EBIT",
            "finance_leases": "disclosed but excluded pending FCFF reconciliation",
            "preferred": "claim and if-converted branches shown separately",
            "investments": "zero-to-full carrying-balance recognition range; no selected haircut",
            "missing_values": "never zero-filled as observed facts",
        },
        "minimum_user_display": [
            "value level A/B/C and operational eligibility",
            "official financial date and market-price date",
            "research per-share range with no selected midpoint",
            "top three value drivers with mechanical per-share spans",
            "asset-recognition, preferred and dilution branch assumptions",
            "formal-value blockers and source-integrity status",
            "explicit statement that the result is not a buy/sell signal",
        ],
        "scale_out_procedure": [
            "ingest same-date issuer balance sheet and claim facts with source timestamps and hashes",
            "classify assets and claims using sector-specific bridge policy",
            "run lease, preferred, NCI and dilution double-count guards",
            "compute A only with formal forecasts/WACC/bridge; otherwise display B with full ranges or C",
        ],
        "scale_out_exceptions": [
            "banks and insurers require equity-based rather than generic FCFF enterprise bridges",
            "REITs require FFO/AFFO and property/debt conventions",
            "convertibles, pensions, material litigation and cross-holdings require issuer-specific claim modules",
            "IFRS ADRs require taxonomy and accounting-policy mapping",
        ],
        "decision_options": [
            {
                "option": "limited_research_display_first",
                "recommendation": "recommended",
                "scope": "show GOOGL B-grade range, assumptions, driver ranking and hold status behind a research label",
                "condition": "no buy/sell signal and no production deployment until separately approved",
            },
            {
                "option": "continue_googl_design_only",
                "recommendation": "use only if raw-file provenance or dilution policy is considered mandatory before any user display",
                "scope": "finish hashable source import and point-in-time dilution before exposing B-grade output",
            },
        ],
        "formal_results": {
            "formal_fair_value": None,
            "operational_buy_signal": None,
            "status": "hold",
        },
        "operational_use": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the GOOGL practical B-grade value audit")
    parser.add_argument(
        "--bridge-evidence",
        type=Path,
        default=ROOT / "docs" / "evidence" / "googl-equity-bridge-v2.4.json",
    )
    parser.add_argument(
        "--scenario-evidence",
        type=Path,
        default=ROOT / "docs" / "evidence" / "googl-scenarios-v2.3.json",
    )
    parser.add_argument("--universe", type=Path, default=ROOT / "data" / "universe.csv")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_audit(args.bridge_evidence, args.scenario_evidence, args.universe)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
