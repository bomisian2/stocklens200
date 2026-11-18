from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.dcf import equity_fcf_schedule, reverse_dcf_sensitivity, solve_implied_fcf_growth  # noqa: E402
from stocklens.fcff import (  # noqa: E402
    enterprise_fcff_schedule,
    enterprise_to_equity_bridge,
    fcff_components,
    operating_fcff_projection,
    operating_tax_evidence,
    wacc_evidence,
    working_capital_evidence,
)


def _number(row: pd.Series, field: str) -> float:
    value = pd.to_numeric(row.get(field), errors="coerce")
    if pd.isna(value):
        raise ValueError(f"GOOGL DCF audit requires {field}")
    return float(value)


def _cagr(first: float, last: float, years: int) -> float:
    if first <= 0 or last <= 0 or years <= 0:
        raise ValueError("CAGR requires positive endpoints and years")
    return (last / first) ** (1 / years) - 1


def build_audit(
    financials: Path,
    universe: Path,
    *,
    risk_free_rate: float,
    capex_guidance_low: float,
    capex_guidance_high: float,
    capex_guidance_source: str,
    fcff_evidence_path: Path | None = None,
) -> dict[str, Any]:
    frame = pd.read_csv(financials)
    frame = frame[frame["ticker"].eq("GOOGL")].copy()
    ttm = frame[frame["period_type"].eq("TTM")]
    annual = frame[frame["period_type"].eq("FY")].sort_values("period_end")
    if len(ttm) != 1 or len(annual) < 4:
        raise ValueError("audit requires one GOOGL TTM and at least four annual periods")
    ttm_row = ttm.iloc[0]
    universe_frame = pd.read_csv(universe)
    market = universe_frame[universe_frame["ticker"].eq("GOOGL")]
    if len(market) != 1:
        raise ValueError("audit requires one verified GOOGL universe row")
    market_row = market.iloc[0]

    fcf = _number(ttm_row, "fcf")
    cfo = _number(ttm_row, "cfo")
    capex = _number(ttm_row, "capex")
    revenue = _number(ttm_row, "revenue")
    shares = _number(ttm_row, "diluted_shares")
    price = _number(market_row, "price")
    revenue_growth = _cagr(_number(annual.iloc[0], "revenue"), _number(annual.iloc[-1], "revenue"), len(annual) - 1)
    fcf_growth = _cagr(_number(annual.iloc[0], "fcf"), _number(annual.iloc[-1], "fcf"), len(annual) - 1)
    annual_revenues = [_number(row, "revenue") for _, row in annual.iterrows()]
    annual_revenue_growth = [
        current / previous - 1
        for previous, current in zip(annual_revenues, annual_revenues[1:])
    ]
    annual_operating_margins = [
        _number(row, "operating_income") / _number(row, "revenue")
        for _, row in annual.iterrows()
    ]
    historical_growth = statistics.median([revenue_growth, fcf_growth])
    discount_rate = risk_free_rate + 0.05 + 0.015
    terminal_growth = min(0.025, max(0.0, historical_growth / 2))
    base = equity_fcf_schedule(
        base_fcf=fcf,
        growth_rate=historical_growth,
        discount_rate=discount_rate,
        terminal_growth=terminal_growth,
        diluted_shares=shares,
    )
    sensitivity = reverse_dcf_sensitivity(
        target_price=price,
        base_fcf=fcf,
        diluted_shares=shares,
        discount_rates=(0.09, 0.10, discount_rate, 0.13, 0.15),
        terminal_growth_rates=(0.015, 0.025, 0.03),
    )
    base_reverse = next(
        row for row in sensitivity
        if abs(row["discount_rate"] - discount_rate) < 1e-12
        and abs(row["terminal_growth"] - terminal_growth) < 1e-12
    )
    implied_year_5_revenue = revenue * ((1 + revenue_growth) ** 5)

    history = []
    for _, row in pd.concat([annual, ttm]).sort_values("period_end").iterrows():
        row_revenue = _number(row, "revenue")
        row_capex = _number(row, "capex")
        depreciation = pd.to_numeric(row.get("depreciation_amortization"), errors="coerce")
        history.append(
            {
                "period": f"{int(row['fiscal_year'])}-{row['period_type']}",
                "period_end": str(row["period_end"]),
                "revenue": row_revenue,
                "capex": row_capex,
                "capex_to_revenue": row_capex / row_revenue,
                "depreciation_amortization": None if pd.isna(depreciation) else float(depreciation),
                "capex_minus_depreciation": None if pd.isna(depreciation) else row_capex - float(depreciation),
            }
        )

    capex_scenarios = []
    scenario_specs = [
        ("reported_ttm", capex, "issuer-reported TTM"),
        ("fy2025_actual_counterfactual", _number(annual.iloc[-1], "capex"), "holds TTM CFO constant; not a forecast"),
        ("fy2026_guidance_low_mechanical", capex_guidance_low, "official guidance; holds TTM CFO constant"),
        ("fy2026_guidance_mid_mechanical", (capex_guidance_low + capex_guidance_high) / 2, "official guidance midpoint; holds TTM CFO constant"),
        ("fy2026_guidance_high_mechanical", capex_guidance_high, "official guidance; holds TTM CFO constant"),
        ("depreciation_floor_not_normalized", _number(ttm_row, "depreciation_amortization"), "illustrative maintenance floor only; growth CAPEX split unavailable"),
    ]
    for name, scenario_capex, basis in scenario_specs:
        scenario_fcf = cfo - scenario_capex
        solved = None
        if scenario_fcf > 0:
            solved = solve_implied_fcf_growth(
                target_price=price,
                base_fcf=scenario_fcf,
                diluted_shares=shares,
                discount_rate=discount_rate,
                terminal_growth=terminal_growth,
            )
        capex_scenarios.append(
            {
                "name": name,
                "basis": basis,
                "capex": scenario_capex,
                "mechanical_fcf_at_constant_ttm_cfo": scenario_fcf,
                "reverse_dcf": solved,
                "operational_use": False,
            }
        )

    cash = _number(ttm_row, "cash")
    debt = _number(ttm_row, "total_debt")
    marketable = pd.to_numeric(ttm_row.get("marketable_securities"), errors="coerce")
    marketable_value = None if pd.isna(marketable) else float(marketable)
    q1 = frame[(frame["period_type"].eq("Q")) & frame["period_end"].eq("2026-03-31")].iloc[0]
    q2 = frame[(frame["period_type"].eq("Q")) & frame["period_end"].eq("2026-06-30")].iloc[0]
    fcff_audit = None
    if fcff_evidence_path is not None:
        evidence = json.loads(fcff_evidence_path.read_text(encoding="utf-8"))
        if evidence.get("schema_version") != 1 or evidence.get("ticker") != "GOOGL":
            raise ValueError("FCFF evidence must be schema v1 for GOOGL")
        gains = evidence["identified_equity_security_gains"]
        tax = operating_tax_evidence(
            operating_income=_number(ttm_row, "operating_income"),
            pretax_income=_number(ttm_row, "pretax_income"),
            income_tax=_number(ttm_row, "income_tax"),
            identified_one_time_pretax_gain=float(gains["pretax_total"]),
            identified_one_time_after_tax_gain=float(
                gains["issuer_stated_after_tax_net_income_effect_total"]
            ),
        )
        snapshots = evidence["working_capital_snapshots"]
        current_wc = snapshots["current_2026_06_30"]
        prior_wc = snapshots["prior_2025_06_30"]
        working_capital = working_capital_evidence(
            current_operating_assets={
                "accounts_receivable": current_wc.get("accounts_receivable"),
                "inventory": current_wc.get("inventory"),
            },
            current_operating_liabilities={
                "accounts_payable": current_wc.get("accounts_payable"),
                "accrued_compensation": current_wc.get("accrued_compensation"),
                "accrued_revenue_share": current_wc.get("accrued_revenue_share"),
                "deferred_revenue": current_wc.get("deferred_revenue"),
            },
            prior_operating_assets={
                "accounts_receivable": prior_wc.get("accounts_receivable"),
                "inventory": prior_wc.get("inventory"),
            },
            prior_operating_liabilities={
                "accounts_payable": prior_wc.get("accounts_payable"),
                "accrued_compensation": prior_wc.get("accrued_compensation"),
                "accrued_revenue_share": prior_wc.get("accrued_revenue_share"),
                "deferred_revenue": prior_wc.get("deferred_revenue"),
            },
            unclassified_fields=snapshots["unclassified_components"],
        )
        fcff = fcff_components(
            operating_income=_number(ttm_row, "operating_income"),
            depreciation_amortization=_number(ttm_row, "depreciation_amortization"),
            capex=_number(ttm_row, "capex"),
            verified_operating_tax_rate=tax["verified_operating_tax_rate"],
            verified_change_operating_working_capital=working_capital[
                "verified_change_operating_working_capital"
            ],
            research_tax_rate=tax["event_adjusted_total_effective_tax_rate"],
        )
        wacc_inputs = evidence["wacc_evidence"]
        if abs(float(wacc_inputs["risk_free_rate"]) - risk_free_rate) > 1e-12:
            raise ValueError("risk-free input does not match the dated FCFF evidence")
        verified_wacc = ["risk_free_rate"] if wacc_inputs["risk_free_rate_verified"] else []
        wacc = wacc_evidence(
            risk_free_rate=risk_free_rate,
            beta=wacc_inputs.get("beta"),
            equity_risk_premium=wacc_inputs.get("equity_risk_premium"),
            pretax_cost_of_debt=wacc_inputs.get("pretax_cost_of_debt"),
            marginal_tax_rate=wacc_inputs.get("marginal_operating_tax_rate"),
            equity_market_value=price * shares,
            debt_market_value=wacc_inputs.get("debt_market_value"),
            verified_components=verified_wacc,
            legacy_data_quality_premium=wacc_inputs.get("legacy_data_quality_premium"),
        )
        enterprise_dcf = None
        if fcff["formal_fcff"] is not None and wacc["wacc"] is not None:
            enterprise_dcf = enterprise_fcff_schedule(
                base_fcff=float(fcff["formal_fcff"]),
                growth_rate=historical_growth,
                wacc=float(wacc["wacc"]),
                terminal_growth=terminal_growth,
            )
        capital = evidence["capital_structure_2026_06_30"]
        bridge = enterprise_to_equity_bridge(
            enterprise_value=None,
            cash=capital.get("cash"),
            marketable_securities=capital.get("marketable_securities"),
            debt=capital.get("reported_long_term_debt"),
            preferred_equity=capital.get("preferred_equity_liquidation_preference"),
            noncontrolling_interest=capital.get("noncontrolling_interest"),
            debt_like_lease_liabilities=None,
            other_verified_nonoperating_assets=None,
            other_verified_potential_claims=capital.get("other_verified_potential_claims"),
            diluted_shares=shares,
        )
        forecast = evidence["forecast_evidence"]
        operating_projection = operating_fcff_projection(
            base_revenue=revenue,
            scenarios=[{
                "revenue_growth": forecast.get("official_revenue_growth_guidance"),
                "operating_margin": forecast.get("official_operating_margin_guidance"),
                "operating_tax_rate": None,
                "depreciation_amortization": forecast.get("verified_depreciation_forecast"),
                "capex": (
                    float(forecast["official_2026_capex_guidance_low"])
                    + float(forecast["official_2026_capex_guidance_high"])
                ) / 2,
                "change_operating_working_capital": forecast.get(
                    "verified_operating_working_capital_forecast"
                ),
                "evidence_verified": False,
            }],
        )
        fcff_audit = {
            "evidence_file": fcff_evidence_path.as_posix(),
            "sources": evidence["sources"],
            "tax": tax,
            "operating_working_capital": working_capital,
            "fcff": fcff,
            "wacc": wacc,
            "enterprise_fcff_dcf": enterprise_dcf,
            "enterprise_to_common_equity_bridge": {
                **bridge,
                "observed_non_marketable_securities_excluded": capital.get(
                    "non_marketable_securities"
                ),
                "observed_noncurrent_lease_liabilities_not_complete_debt_like_claim": capital.get(
                    "reported_noncurrent_operating_lease_liabilities"
                ),
                "capital_structure_limitations": capital["limitations"],
            },
            "operating_projection": operating_projection,
            "formal_fcff_eligible": fcff["formal_fcff"] is not None,
            "enterprise_value_eligible": enterprise_dcf is not None,
            "formal_common_equity_value_eligible": False,
            "operational_judgement_eligible": False,
        }
    return {
        "schema_version": 2,
        "ticker": "GOOGL",
        "market": {
            "price": price,
            "price_as_of_utc": str(market_row["market_time_utc"]),
            "source": str(market_row["source"]),
            "target_equity_value": price * shares,
        },
        "existing_model_audit": base,
        "historical_growth": {
            "revenue_cagr": revenue_growth,
            "fcf_cagr": fcf_growth,
            "median_used_once": historical_growth,
        },
        "capital_structure": {
            "cash": cash,
            "marketable_securities": marketable_value,
            "debt": debt,
            "observed_net_financial_assets": None if marketable_value is None else cash + marketable_value - debt,
            "bridge_applied": False,
            "reason": base["bridge_reason"],
        },
        "capex_history": history,
        "capex_guidance": {
            "fiscal_year": 2026,
            "low": capex_guidance_low,
            "high": capex_guidance_high,
            "source_url": capex_guidance_source,
            "operational_normalization": False,
        },
        "capex_scenarios": capex_scenarios,
        "reverse_dcf_sensitivity": sensitivity,
        "operating_model_identification": {
            "status": "underdetermined",
            "reason": (
                "revenue growth, target operating margin, working capital, taxes, CAPEX and terminal growth "
                "cannot be uniquely solved from one market price"
            ),
            "no_price_fitting": True,
            "identified_complete_combination_count": 0,
            "historical_and_official_comparison_anchors": {
                "annual_revenue_growth_observed_min": min(annual_revenue_growth),
                "annual_revenue_growth_observed_max": max(annual_revenue_growth),
                "annual_operating_margin_observed_min": min(annual_operating_margins),
                "annual_operating_margin_observed_max": max(annual_operating_margins),
                "official_2026_capex_guidance_low": capex_guidance_low,
                "official_2026_capex_guidance_high": capex_guidance_high,
                "status": "comparators_not_forecasts",
            },
            "missing_forward_paths": [
                "verified multi-year revenue growth",
                "verified target operating margin",
                "verified operating tax rate",
                "verified depreciation path",
                "verified operating working-capital path",
                "verified WACC",
            ],
            "illustrative_historical_revenue_anchor": {
                "revenue_growth": revenue_growth,
                "year_5_revenue": implied_year_5_revenue,
                "year_5_fcf": base_reverse["implied_year_5_fcf"],
                "implied_fcf_margin": base_reverse["implied_year_5_fcf"] / implied_year_5_revenue,
                "current_operating_margin": _number(ttm_row, "operating_income") / revenue,
                "status": "diagnostic_not_forecast",
            },
        },
        "earnings_quality": {
            "q1_equity_securities_gain": _number(q1, "equity_securities_gain_loss"),
            "q1_cash_flow_debt_equity_gain_adjustment": _number(q1, "securities_gain_loss"),
            "q1_issuer_stated_after_tax_net_income_effect": 28_700_000_000.0,
            "q1_issuer_stated_diluted_eps_effect": 2.35,
            "q2_equity_securities_gain": _number(q2, "equity_securities_gain_loss"),
            "q2_cash_flow_debt_equity_gain_adjustment": _number(q2, "securities_gain_loss"),
            "q2_issuer_stated_after_tax_net_income_effect": 77_100_000_000.0,
            "q2_issuer_stated_diluted_eps_effect": 6.26,
            "ttm_gaap_eps": None,
            "ttm_normalized_eps": None,
            "pe_model_eligible": False,
        },
        "fcff_economic_definition_audit": fcff_audit,
        "confidence_dimensions": {
            "financial_data_quality": "verified_official_source_with_unaudited_interim",
            "forecast_quality": "insufficient_except_capex_guidance",
            "valuation_model_confidence": "research_only",
            "operational_judgement_eligibility": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit GOOGL DCF and reverse market expectations")
    parser.add_argument("--financials", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--risk-free-rate", type=float, required=True)
    parser.add_argument(
        "--fcff-evidence",
        type=Path,
        default=ROOT / "docs" / "evidence" / "googl-fcff-v2.2.json",
    )
    parser.add_argument("--capex-guidance-low", type=float, default=175_000_000_000.0)
    parser.add_argument("--capex-guidance-high", type=float, default=185_000_000_000.0)
    parser.add_argument(
        "--capex-guidance-source",
        default="https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2025q4-alphabet-earnings-release.pdf",
    )
    args = parser.parse_args()
    audit = build_audit(
        args.financials,
        args.universe,
        risk_free_rate=args.risk_free_rate,
        capex_guidance_low=args.capex_guidance_low,
        capex_guidance_high=args.capex_guidance_high,
        capex_guidance_source=args.capex_guidance_source,
        fcff_evidence_path=args.fcff_evidence,
    )
    rendered = json.dumps(audit, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

