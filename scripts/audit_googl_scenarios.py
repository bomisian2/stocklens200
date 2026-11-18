from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.scenario_valuation import (  # noqa: E402
    discount_fcff_sensitivity,
    market_expectations_status,
    project_revenue_to_fcff,
)


def _assumption(value: float, rationale: str, source_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "value": value,
        "state": "model_assumption",
        "source_ids": source_ids or [],
        "rationale": rationale,
    }


def _scenario_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    paths = {
        "revenue_growth": spec["revenue_growth"],
        "operating_margin": spec["operating_margin"],
        "operating_tax_rate": spec["operating_tax_rate"],
        "depreciation_amortization_to_revenue": spec["depreciation_to_revenue"],
        "capex_to_revenue": spec["capex_to_revenue"],
        "change_operating_working_capital_to_incremental_revenue": spec[
            "change_nwc_to_incremental_revenue"
        ],
        "growth_capex_lag_years": spec["growth_capex_lag_years"],
    }
    lengths = {len(values) for values in paths.values()}
    if lengths != {5}:
        raise ValueError(f"{spec['name']} must contain five values for every scenario path")
    rows: list[dict[str, Any]] = []
    for index in range(5):
        row = {"label": f"forecast_year_{index + 1}"}
        for name, values in paths.items():
            source_ids = ["alphabet_fy2025", "alphabet_h1_2026"]
            rationale = spec["assumption_basis"]
            if name == "capex_to_revenue" and index == 0:
                rationale += " Year-one ratio mechanically links the selected point in the official 2026 CAPEX range to assumed revenue."
            elif name == "growth_capex_lag_years":
                rationale = (
                    "Scenario timing tag for comparison only; it does not claim that a dollar of CAPEX "
                    "causes a measured amount of revenue."
                )
            elif name == "change_operating_working_capital_to_incremental_revenue":
                rationale = (
                    "Non-zero stress ratio because official operating working-capital history is incomplete; "
                    "this is not a reported Alphabet metric."
                )
            row[name] = _assumption(float(values[index]), rationale, source_ids)
        rows.append(row)
    return rows


def _build_projection(evidence: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    return project_revenue_to_fcff(
        base_revenue=evidence["observed_ttm"]["revenue"],
        years=_scenario_rows(spec),
        reinvestment_efficiency_ceiling=evidence["scenario_method"][
            "reinvestment_efficiency_ceiling"
        ],
    )


def _sensitivity(evidence: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    wacc_review = evidence["wacc_review"]
    wacc_candidates = [
        _assumption(
            value,
            wacc_review["candidate_rationale"],
            ["us_treasury_2026_10_01", "damodaran_erp_2026_09"],
        )
        for value in wacc_review["sensitivity_candidates"]
    ]
    terminal_review = evidence["terminal_growth_review"]
    terminal_candidates = [
        _assumption(value, terminal_review["candidate_rationale"])
        for value in terminal_review["sensitivity_candidates"]
    ]
    return discount_fcff_sensitivity(
        projection=projection,
        wacc_candidates=wacc_candidates,
        terminal_growth_candidates=terminal_candidates,
    )


def _capex_guide_sensitivity(
    evidence: dict[str, Any], reference_spec: dict[str, Any]
) -> list[dict[str, Any]]:
    base_revenue = float(evidence["observed_ttm"]["revenue"]["value"])
    year_one_revenue = base_revenue * (1 + float(reference_spec["revenue_growth"][0]))
    low = float(evidence["issuer_guidance"]["2026_capex_low"]["value"])
    high = float(evidence["issuer_guidance"]["2026_capex_high"]["value"])
    variants = (("guide_low", low), ("guide_midpoint", (low + high) / 2), ("guide_high", high))
    output: list[dict[str, Any]] = []
    for name, amount in variants:
        spec = deepcopy(reference_spec)
        spec["capex_to_revenue"][0] = amount / year_one_revenue
        projection = _build_projection(evidence, spec)
        sensitivity = _sensitivity(evidence, projection)
        output.append(
            {
                "name": name,
                "year_one_capex": amount,
                "year_one_capex_state": (
                    "model_assumption" if name == "guide_midpoint" else "issuer_guidance"
                ),
                "year_one_capex_source_ids": ["alphabet_fy2025"],
                "post_year_one_capex_state": "model_assumption",
                "projection": projection,
                "enterprise_value_sensitivity": sensitivity,
                "operational_use": False,
            }
        )
    return output


def build_audit(evidence_path: Path, universe_path: Path) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != 1 or evidence.get("ticker") != "GOOGL":
        raise ValueError("GOOGL scenario evidence schema v1 is required")
    if evidence.get("operational_use") is not False:
        raise ValueError("scenario evidence must remain non-operational")

    universe = pd.read_csv(universe_path)
    market = universe[universe["ticker"].eq("GOOGL")]
    if len(market) != 1:
        raise ValueError("one GOOGL market snapshot row is required")
    market_row = market.iloc[0]
    if pd.isna(market_row.get("market_time_utc")):
        raise ValueError("GOOGL market snapshot requires a market timestamp")

    scenario_results: list[dict[str, Any]] = []
    for spec in evidence["scenarios"]:
        projection = _build_projection(evidence, spec)
        scenario_results.append(
            {
                "name": spec["name"],
                "display_role": spec["display_role"],
                "assumption_basis": spec["assumption_basis"],
                "projection": projection,
                "enterprise_value_sensitivity": _sensitivity(evidence, projection),
                "official_base_fair_value": False,
                "operational_use": False,
            }
        )

    reference = next(spec for spec in evidence["scenarios"] if spec["name"] == "reference_research")
    capex_sensitivity = _capex_guide_sensitivity(evidence, reference)
    scenario_ranges = {
        result["name"]: result["enterprise_value_sensitivity"]["stable_enterprise_value_range"]
        for result in scenario_results
    }
    market_expectations = market_expectations_status(
        current_price={
            "value": float(market_row["price"]),
            "state": "observed",
            "source_ids": ["universe_snapshot"],
            "as_of_utc": str(market_row["market_time_utc"]),
            "unit": "USD_per_share",
        },
        diluted_shares=evidence["observed_ttm"]["diluted_shares"],
        missing_equity_bridge_components=evidence["equity_bridge_missing_components"],
    )
    return {
        "schema_version": 1,
        "model_version": "googl-scenario-research-v2.3.0",
        "ticker": "GOOGL",
        "market_as_of_utc": str(market_row["market_time_utc"]),
        "evidence_as_of_utc": evidence["evidence_as_of_utc"],
        "epistemic_policy": {
            "states": ["observed", "issuer_guidance", "model_assumption", "missing"],
            "promotion_forbidden": True,
            "research_results_are_not_operational_valuations": True,
        },
        "new_observed_financial_data": [],
        "reused_observed_ttm": evidence["observed_ttm"],
        "issuer_guidance": evidence["issuer_guidance"],
        "historical_comparators": evidence["observed_historical_anchors"],
        "wacc_component_review": evidence["wacc_review"],
        "scenario_results": scenario_results,
        "research_enterprise_value_ranges": scenario_ranges,
        "capex_guide_sensitivity": capex_sensitivity,
        "market_expectations": market_expectations,
        "formal_results": {
            "formal_fcff": None,
            "formal_wacc": None,
            "formal_enterprise_value": None,
            "formal_common_equity_value": None,
            "formal_per_share_value": None,
            "official_base_fair_value": None,
            "operational_buy_signal": None,
            "status": "hold",
        },
        "confidence_dimensions": {
            "financial_data_quality": "verified_official_source_with_unaudited_interim",
            "forward_outlook_quality": "insufficient_except_one_year_capex_range",
            "scenario_internal_consistency": "tested_research_assumptions",
            "wacc_quality": "sensitivity_only_formal_wacc_missing",
            "equity_bridge_quality": "incomplete",
            "operational_judgement_eligibility": False,
        },
        "minimum_data_for_operation": [
            "source-verified operating tax rate or tax reconciliation suitable for NOPAT",
            "classified operating working-capital history and forecast method",
            "point-in-time beta methodology and source",
            "documented ERP selection policy",
            "pre-tax cost of debt and market-value capital weights",
            "complete enterprise-to-common-equity bridge",
            "defensible multi-year revenue, margin, D&A, CAPEX and working-capital paths",
            "out-of-sample validation and forecast-error calibration",
        ],
        "operational_use": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the non-operational GOOGL v2.3 scenario audit")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "docs" / "evidence" / "googl-scenarios-v2.3.json",
    )
    parser.add_argument("--universe", type=Path, default=ROOT / "data" / "universe.csv")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    audit = build_audit(args.evidence, args.universe)
    rendered = json.dumps(audit, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
