from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd


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
        "test_fold_count", "positive_test_folds", "test_excess_ci_low",
        "test_excess_pvalue", "fdr_qvalue",
    }
    missing = required - set(backtests.columns)
    if missing:
        raise RuntimeError(f"backtest validation columns missing: {sorted(missing)}")
    if pd.to_numeric(backtests.loc[validated, "fdr_qvalue"], errors="coerce").gt(0.05).any():
        raise RuntimeError("validated backtest failed the FDR gate")
    required_folds = (
        pd.to_numeric(backtests.loc[validated, "test_fold_count"]) * 2 / 3
    ).apply(math.ceil)
    if (
        pd.to_numeric(backtests.loc[validated, "positive_test_folds"]).to_numpy()
        < required_folds.to_numpy()
    ).any():
        raise RuntimeError("validated backtest failed the sequential-fold gate")
    if not signals.empty:
        if not signals["status"].eq("validated").all():
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

