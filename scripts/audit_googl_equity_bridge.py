from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_googl_scenarios import build_audit as build_scenario_audit  # noqa: E402
from stocklens.equity_bridge import (  # noqa: E402
    assess_equity_bridge,
    per_share_balance_sensitivity,
)


def build_audit(
    evidence_path: Path,
    scenario_evidence_path: Path,
    universe_path: Path,
) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != 1 or evidence.get("ticker") != "GOOGL":
        raise ValueError("GOOGL equity-bridge evidence schema v1 is required")
    if evidence.get("operational_use") is not False:
        raise ValueError("equity-bridge evidence must remain non-operational")

    scenario_audit = build_scenario_audit(scenario_evidence_path, universe_path)
    reference_ev_range = scenario_audit["research_enterprise_value_ranges"].get(
        "reference_research"
    )
    policy = evidence["accounting_policy"]
    formal_shares = evidence["share_counts"]["formal_point_in_time_fully_diluted"]
    bridge = assess_equity_bridge(
        enterprise_value_range=reference_ev_range,
        items=evidence["bridge_items"],
        share_denominator=formal_shares,
        fcff_lease_basis=policy["fcff_lease_basis"],
        preferred_treatment=policy["preferred_treatment"],
    )
    issued_shares = evidence["share_counts"]["issued_outstanding_common"]["value"]
    scale = per_share_balance_sensitivity(10_000_000_000, issued_shares)

    states: dict[str, list[str]] = {
        "official_observed": [],
        "book_value_proxy": [],
        "model_estimate": [],
        "missing": [],
    }
    for item in evidence["bridge_items"]:
        states[item["state"]].append(item["id"])

    return {
        "schema_version": 1,
        "model_version": "googl-equity-bridge-audit-v2.4.0",
        "ticker": "GOOGL",
        "evidence_as_of": evidence["evidence_as_of"],
        "source_policy": {
            "official_filing_reference_recorded": True,
            "filing_source_hash_available": False,
            "automated_sec_input": False,
            "paid_api_used": False,
        },
        "classification": states,
        "accounting_policy": policy,
        "bridge_assessment": bridge,
        "value_status": {
            "A_verified_value_range": None,
            "B_research_reference": {
                "level": "enterprise_value_only",
                "range": reference_ev_range,
                "is_official_base_fair_value": False,
                "operational_use": False,
            },
            "C_per_share_unavailable": {
                "status": "active",
                "reason": bridge["reason"],
            },
        },
        "mechanical_scale_diagnostic": scale,
        "formal_blockers": evidence["formal_blockers"],
        "formal_results": {
            "formal_common_equity_value": None,
            "formal_per_share_value": None,
            "official_base_fair_value": None,
            "operational_buy_signal": None,
            "status": "hold",
        },
        "minimum_data_for_200_company_operation": [
            "dated cash, investments and interest-bearing debt with a common valuation date",
            "issuer-specific operating-cash and non-operating-asset classification",
            "tax and liquidity treatment for investment assets",
            "lease convention reconciled to FCFF",
            "NCI, preferred, pension, legal and contingent-claim policy",
            "point-in-time fully diluted shares including awards and convertibles without double counting",
            "formal WACC and source-supported multi-year operating forecast",
        ],
        "next_tasks": [
            "import and hash issuer filings or IR notes into a reusable bridge-fact schema",
            "validate one consistent lease, preferred and point-in-time dilution policy",
            "establish operating-cash and investment tax/liquidity policy, then rerun the A/B/C gate",
        ],
        "operational_use": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the non-operational GOOGL v2.4 bridge audit")
    parser.add_argument(
        "--evidence",
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
    audit = build_audit(args.evidence, args.scenario_evidence, args.universe)
    rendered = json.dumps(audit, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
