from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.signals import MIN_FINAL_SAMPLES, MIN_INDEPENDENT_TOTAL


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    status = json.loads((args.data_dir / "signal_status.json").read_text(encoding="utf-8"))
    backtests = pd.read_csv(args.data_dir / "signal_backtests.csv")
    signals = pd.read_csv(args.data_dir / "signals.csv")
    selected = int(status["selected_ticker_count"])
    available = int(status["history_available_ticker_count"])
    if not args.allow_partial and available != selected:
        raise RuntimeError(f"history coverage {available}/{selected}")
    expected_backtests = available * 4 * 3
    if len(backtests) != expected_backtests:
        raise RuntimeError(f"backtest count {len(backtests)} != {expected_backtests}")
    validated = backtests["validated"].astype(str).str.lower().eq("true")
    required = {
        "walk_forward_fold_count", "positive_walk_forward_folds",
        "final_samples", "final_period_start", "final_mtm_max_drawdown", "final_excess_ci_low",
        "final_excess_pvalue", "fdr_qvalue", "validation_state",
        "independent_validated",
    }
    missing = required - set(backtests.columns)
    if missing:
        raise RuntimeError(f"backtest validation columns missing: {sorted(missing)}")
    if pd.to_numeric(backtests.loc[validated, "fdr_qvalue"], errors="coerce").gt(0.05).any():
        raise RuntimeError("validated backtest failed the FDR gate")
    if pd.to_numeric(
        backtests.loc[validated, "total_samples"], errors="coerce"
    ).lt(MIN_INDEPENDENT_TOTAL).any():
        raise RuntimeError("validated backtest failed the total-sample gate")
    if pd.to_numeric(
        backtests.loc[validated, "final_samples"], errors="coerce"
    ).lt(MIN_FINAL_SAMPLES).any():
        raise RuntimeError("validated backtest failed the untouched-final gate")
    if not backtests.loc[validated, "validation_state"].eq("independent_validated").all():
        raise RuntimeError("validated backtest has an inconsistent state")
    if available != selected and not signals.empty:
        raise RuntimeError("active signals must be held when cross-sectional coverage is partial")
    if not signals.empty:
        if not signals["status"].eq("current_active").all():
            raise RuntimeError("unvalidated active signal was published")
        if pd.to_numeric(signals["excess_ci_low"], errors="coerce").le(0).any():
            raise RuntimeError("active signal failed the excess-return confidence gate")
    print(json.dumps({
        "selected": selected,
        "available": available,
        "backtests": len(backtests),
        "validated": int(validated.sum()),
        "active_signals": len(signals),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

