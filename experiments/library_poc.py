"""Isolated FinanceToolkit/QuantStats compatibility experiment.

This module is intentionally outside the production package and never downloads data.
It accepts only normalized financials or an explicit daily return series. Third-party
source trees must be supplied by path so neither library becomes a runtime dependency.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _number(row: pd.Series, field: str) -> float:
    value = pd.to_numeric(row.get(field), errors="coerce")
    if pd.isna(value):
        raise ValueError(f"normalized financial row lacks {field}")
    return float(value)


def finance_toolkit_poc(financials: Path, toolkit_root: Path) -> dict[str, Any]:
    frame = pd.read_csv(financials)
    ttm = frame[(frame["ticker"] == "GOOGL") & (frame["period_type"] == "TTM")]
    start = frame[
        (frame["ticker"] == "GOOGL")
        & (frame["period_type"] == "YTD")
        & (frame["period_end"] == "2025-06-30")
    ]
    if len(ttm) != 1 or len(start) != 1:
        raise ValueError("PoC requires one GOOGL TTM row and the prior-year matching YTD balance row")
    ttm_row, start_row = ttm.iloc[0], start.iloc[0]
    module_path = toolkit_root / "financetoolkit" / "ratios" / "profitability_model.py"
    ratios = _load_module(module_path, "financetoolkit_profitability_model_poc")

    revenue = _number(ttm_row, "revenue")
    fcf = _number(ttm_row, "fcf")
    common_income = _number(ttm_row, "common_net_income")
    dividends = _number(ttm_row, "dividends")
    average_equity = (_number(start_row, "equity") + _number(ttm_row, "equity")) / 2
    average_debt = (_number(start_row, "total_debt") + _number(ttm_row, "total_debt")) / 2
    inputs = {
        "revenue": revenue,
        "fcf": fcf,
        "common_net_income": common_income,
        "dividends": dividends,
        "average_equity": average_equity,
        "average_debt": average_debt,
    }

    started = time.perf_counter()
    toolkit_values: dict[str, float] = {}
    for _ in range(10_000):
        toolkit_values = {
            "fcf_margin": float(ratios.get_free_cash_flow_margin(fcf, revenue)),
            "roe": float(ratios.get_return_on_equity(common_income, average_equity)),
            "roic_net_income_basis": float(
                ratios.get_return_on_invested_capital(
                    common_income, dividends, average_equity, average_debt
                )
            ),
        }
    elapsed_ms = (time.perf_counter() - started) * 1000
    local_values = {
        "fcf_margin": fcf / revenue,
        "roe": common_income / average_equity,
        "roic_net_income_basis": (common_income - dividends) / (average_equity + average_debt),
    }
    differences = {key: toolkit_values[key] - local_values[key] for key in local_values}
    if any(not math.isclose(value, 0.0, abs_tol=1e-12) for value in differences.values()):
        raise RuntimeError("FinanceToolkit ratio result differs from the identical local formula")
    return {
        "input": inputs,
        "toolkit": toolkit_values,
        "local": local_values,
        "absolute_differences": differences,
        "runtime_ms_for_10000_iterations": round(elapsed_ms, 3),
        "wacc": {
            "status": "blocked",
            "reason": "same-basis verified beta, benchmark returns, and interest expense are unavailable",
        },
        "warning": (
            "FinanceToolkit ROIC is the net-income-minus-dividends variant, not NOPAT ROIC; "
            "GOOGL investment gains materially distort this result"
        ),
    }


def _validated_daily_returns(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"date", "strategy_return", "qqq_return"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"daily return input lacks {sorted(missing)}")
    frame = frame[["date", "strategy_return", "qqq_return"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True, errors="coerce")
    frame["strategy_return"] = pd.to_numeric(frame["strategy_return"], errors="coerce")
    frame["qqq_return"] = pd.to_numeric(frame["qqq_return"], errors="coerce")
    if frame.isna().any().any() or frame["date"].duplicated().any():
        raise ValueError("daily return input contains missing or duplicate observations")
    if not frame["date"].is_monotonic_increasing or len(frame) < 252:
        raise ValueError("daily return input must be sorted and contain at least 252 observations")
    return frame.set_index("date")


def quantstats_poc(daily_returns: Path, quantstats_root: Path) -> dict[str, Any]:
    frame = _validated_daily_returns(daily_returns)
    sys.path.insert(0, str(quantstats_root))
    try:
        from quantstats import stats  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    strategy = frame["strategy_return"]
    benchmark = frame["qqq_return"]
    started = time.perf_counter()
    result = {
        "sharpe": float(stats.sharpe(strategy)),
        "sortino": float(stats.sortino(strategy)),
        "cagr": float(stats.cagr(strategy)),
        "max_drawdown": float(stats.max_drawdown(strategy)),
        "qqq_cagr": float(stats.cagr(benchmark)),
    }
    result["runtime_ms"] = round((time.perf_counter() - started) * 1000, 3)
    result["observation_count"] = len(frame)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--financials", type=Path)
    parser.add_argument("--financetoolkit-root", type=Path)
    parser.add_argument("--daily-returns", type=Path)
    parser.add_argument("--quantstats-root", type=Path)
    args = parser.parse_args()
    output: dict[str, Any] = {}
    if args.financials and args.financetoolkit_root:
        output["financetoolkit"] = finance_toolkit_poc(args.financials, args.financetoolkit_root)
    if args.daily_returns and args.quantstats_root:
        output["quantstats"] = quantstats_poc(args.daily_returns, args.quantstats_root)
    if not output:
        parser.error("provide a matched financials/toolkit or daily-returns/quantstats pair")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
