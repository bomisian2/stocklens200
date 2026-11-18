from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.signals import _block_bootstrap  # noqa: E402


def _draw_null(rng: np.random.Generator, model: str, samples: int) -> np.ndarray:
    if model == "normal":
        return rng.normal(0, 0.04, samples)
    if model == "heavy_tail":
        values = rng.standard_t(df=3, size=samples)
        return values / np.std(values, ddof=1) * 0.04
    if model == "ar1":
        shocks = rng.normal(0, 0.038, samples)
        values = np.zeros(samples)
        values[0] = shocks[0]
        for index in range(1, samples):
            values[index] = 0.30 * values[index - 1] + shocks[index]
        return values
    if model == "volatility_cluster":
        regimes = np.where(rng.random(samples) < 0.20, 0.09, 0.025)
        return rng.normal(0, regimes)
    raise ValueError(f"unknown model: {model}")


def calibrate(*, trials: int, bootstrap_repetitions: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for samples in (8, 12, 15, 20):
        for model in ("normal", "heavy_tail", "ar1", "volatility_cluster"):
            passes = 0
            for trial in range(trials):
                values = _draw_null(rng, model, samples)
                low, _, _, block_length = _block_bootstrap(
                    pd.Series(values),
                    seed_key=f"calibration:{seed}:{model}:{samples}:{trial}",
                    repetitions=bootstrap_repetitions,
                )
                wins = values[values > 0]
                losses = values[values < 0]
                payoff = (
                    float(wins.mean() / abs(losses.mean()))
                    if len(wins) and len(losses) else 0.0
                )
                if values.mean() > 0 and payoff > 1 and low is not None and low > 0:
                    passes += 1
            rows.append({
                "samples": samples,
                "null_model": model,
                "trials": trials,
                "false_positive_rate": passes / trials,
                "bootstrap_repetitions": bootstrap_repetitions,
                "block_length": block_length,
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Empirically calibrate the untouched-final sample gate under null returns"
    )
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--bootstrap-repetitions", type=int, default=800)
    parser.add_argument("--seed", type=int, default=20261012)
    args = parser.parse_args()
    if args.trials < 1 or args.bootstrap_repetitions < 100:
        raise ValueError("trials must be positive and bootstrap repetitions at least 100")
    rows = calibrate(
        trials=args.trials,
        bootstrap_repetitions=args.bootstrap_repetitions,
        seed=args.seed,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
