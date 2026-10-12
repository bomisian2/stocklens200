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
    return {
        "schema_version": 1,
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
    )
    rendered = json.dumps(audit, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

