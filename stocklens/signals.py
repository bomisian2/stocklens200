from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, replace
from typing import Iterable

import numpy as np
import pandas as pd


SIGNAL_VERSION = "signals-v2.0.0"
ROUND_TRIP_COST = 0.002
HORIZONS = (5, 10, 20)
MIN_TRAIN_SAMPLES = 20
MIN_TEST_SAMPLES = 10
MIN_TOTAL_SAMPLES = 30

STRATEGIES = {
    "ema_crossback": "EMA Crossback",
    "trend_breakout": "추세 돌파",
    "pullback_rebound": "눌림목 반등",
    "rsi_recovery": "RSI 회복",
}


@dataclass(frozen=True)
class BacktestResult:
    ticker: str
    strategy: str
    strategy_label: str
    horizon: int
    signal_version: str
    train_samples: int
    test_samples: int
    total_samples: int
    test_win_rate: float | None
    test_average_return: float | None
    test_median_return: float | None
    test_payoff_ratio: float | None
    test_max_drawdown: float | None
    test_average_qqq_return: float | None
    test_average_excess_return: float | None
    test_excess_ci_low: float | None
    test_excess_ci_high: float | None
    test_excess_pvalue: float | None
    fdr_qvalue: float | None
    train_average_excess_return: float | None
    test_fold_count: int
    positive_test_folds: int
    preliminary_validated: bool
    validated: bool
    hold_reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    result = 100 - 100 / (1 + relative_strength)
    return result.where(average_loss.ne(0), 100.0)


def calculate_signals(history: pd.DataFrame) -> pd.DataFrame:
    """Calculate fixed rules using only data available at each session close."""
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"price history columns missing: {sorted(missing)}")
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    close = frame["close"]
    frame["ema20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    frame["ema50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    frame["ema200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()
    frame["rsi14"] = _rsi(close)
    frame["prior_high20"] = frame["high"].shift(1).rolling(20, min_periods=20).max()
    frame["prior_volume20"] = frame["volume"].shift(1).rolling(20, min_periods=20).mean()
    trend = (close > frame["ema200"]) & (frame["ema50"] > frame["ema200"])

    frame["ema_crossback"] = (
        trend
        & (close > frame["ema20"])
        & (close.shift(1) <= frame["ema20"].shift(1))
    )
    frame["trend_breakout"] = (
        trend
        & (close > frame["prior_high20"])
        & (frame["volume"] >= frame["prior_volume20"] * 1.20)
    )
    touched_ema_recently = (
        frame["low"].shift(1).rolling(5, min_periods=1).min()
        <= frame["ema20"].shift(1).rolling(5, min_periods=1).max() * 1.01
    )
    frame["pullback_rebound"] = (
        trend
        & touched_ema_recently
        & (close > frame["ema20"])
        & (close > frame["open"])
        & (close > frame["high"].shift(1))
        & (frame["rsi14"] >= 45)
    )
    frame["rsi_recovery"] = (
        trend
        & (frame["rsi14"] > 40)
        & (frame["rsi14"].shift(1) <= 40)
    )
    for strategy in STRATEGIES:
        frame[strategy] = frame[strategy].fillna(False).astype(bool)
    return frame.reset_index(drop=True)


def _aligned(asset: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    left = calculate_signals(asset)
    right = benchmark.copy()
    right["date"] = pd.to_datetime(right["date"], errors="coerce").dt.normalize()
    right = right.sort_values("date").drop_duplicates("date", keep="last")
    right = right[["date", "open", "close"]].rename(
        columns={"open": "qqq_open", "close": "qqq_close"}
    )
    combined = left.merge(right, on="date", how="inner", validate="one_to_one")
    return combined.sort_values("date").reset_index(drop=True)


def build_trades(
    asset: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    strategy: str,
    horizon: int,
    cost: float = ROUND_TRIP_COST,
) -> pd.DataFrame:
    """Enter next session open and exit at the horizon close without overlapping trades."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    if horizon not in HORIZONS:
        raise ValueError(f"unsupported horizon: {horizon}")
    frame = _aligned(asset, benchmark)
    rows: list[dict] = []
    next_eligible = 0
    for index in range(len(frame)):
        if index < next_eligible or not bool(frame.at[index, strategy]):
            continue
        entry_index = index + 1
        exit_index = index + horizon
        if exit_index >= len(frame):
            continue
        entry = frame.iloc[entry_index]
        exit_row = frame.iloc[exit_index]
        numbers = (
            entry["open"], exit_row["close"], entry["qqq_open"], exit_row["qqq_close"]
        )
        if any(pd.isna(value) or float(value) <= 0 for value in numbers):
            continue
        asset_return = float(exit_row["close"] / entry["open"] - 1 - cost)
        qqq_return = float(exit_row["qqq_close"] / entry["qqq_open"] - 1)
        rows.append({
            "signal_date": frame.at[index, "date"],
            "entry_date": entry["date"],
            "exit_date": exit_row["date"],
            "asset_return": asset_return,
            "qqq_return": qqq_return,
            "excess_return": asset_return - qqq_return,
        })
        next_eligible = exit_index + 1
    return pd.DataFrame(rows)


def _max_drawdown(returns: Iterable[float]) -> float | None:
    values = np.asarray(list(returns), dtype=float)
    if len(values) == 0:
        return None
    equity = np.cumprod(1 + values)
    peak = np.maximum.accumulate(np.insert(equity, 0, 1.0))[1:]
    return float(np.min(equity / peak - 1))


def _payoff_ratio(returns: pd.Series) -> float | None:
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def _bootstrap_ci(values: pd.Series, *, seed_key: str) -> tuple[float | None, float | None]:
    clean = values.dropna().to_numpy(dtype=float)
    if len(clean) < 2:
        return None, None
    seed = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest()[:16], 16)
    rng = np.random.default_rng(seed)
    samples = rng.choice(clean, size=(2000, len(clean)), replace=True).mean(axis=1)
    low, high = np.quantile(samples, [0.025, 0.975])
    return float(low), float(high)


def _metrics(trades: pd.DataFrame, *, seed_key: str) -> dict[str, float | int | None]:
    if trades.empty:
        return {
            "samples": 0, "win_rate": None, "average_return": None,
            "median_return": None, "payoff_ratio": None, "max_drawdown": None,
            "average_qqq_return": None, "average_excess_return": None,
            "excess_ci_low": None, "excess_ci_high": None, "excess_pvalue": None,
        }
    low, high = _bootstrap_ci(trades["excess_return"], seed_key=seed_key)
    excess = trades["excess_return"].dropna().to_numpy(dtype=float)
    pvalue = None
    if len(excess) >= 2:
        standard_error = float(np.std(excess, ddof=1) / math.sqrt(len(excess)))
        if standard_error > 0:
            z_score = float(np.mean(excess) / standard_error)
            pvalue = float(0.5 * math.erfc(z_score / math.sqrt(2)))
        elif float(np.mean(excess)) > 0:
            pvalue = 0.0
    return {
        "samples": int(len(trades)),
        "win_rate": float((trades["asset_return"] > 0).mean()),
        "average_return": float(trades["asset_return"].mean()),
        "median_return": float(trades["asset_return"].median()),
        "payoff_ratio": _payoff_ratio(trades["asset_return"]),
        "max_drawdown": _max_drawdown(trades["asset_return"]),
        "average_qqq_return": float(trades["qqq_return"].mean()),
        "average_excess_return": float(trades["excess_return"].mean()),
        "excess_ci_low": low,
        "excess_ci_high": high,
        "excess_pvalue": pvalue,
    }


def evaluate_strategy(
    *,
    ticker: str,
    asset: pd.DataFrame,
    benchmark: pd.DataFrame,
    strategy: str,
    horizon: int,
) -> tuple[BacktestResult, pd.DataFrame]:
    trades = build_trades(asset, benchmark, strategy=strategy, horizon=horizon)
    if trades.empty:
        split = 0
    else:
        split = max(1, min(len(trades) - 1, math.floor(len(trades) * 0.70)))
    train = trades.iloc[:split]
    test = trades.iloc[split:]
    train_metrics = _metrics(train, seed_key=f"{ticker}:{strategy}:{horizon}:train")
    test_metrics = _metrics(test, seed_key=f"{ticker}:{strategy}:{horizon}:test")
    fold_indices = np.array_split(np.arange(len(test)), min(3, len(test))) if len(test) else []
    test_folds = [test.iloc[indices] for indices in fold_indices if len(indices)]
    positive_test_folds = sum(
        float(fold["excess_return"].mean()) > 0 for fold in test_folds
    )
    required_positive_folds = math.ceil(len(test_folds) * 2 / 3) if test_folds else 0

    reasons: list[str] = []
    if len(trades) < MIN_TOTAL_SAMPLES:
        reasons.append(f"total samples {len(trades)} < {MIN_TOTAL_SAMPLES}")
    if len(train) < MIN_TRAIN_SAMPLES:
        reasons.append(f"train samples {len(train)} < {MIN_TRAIN_SAMPLES}")
    if len(test) < MIN_TEST_SAMPLES:
        reasons.append(f"test samples {len(test)} < {MIN_TEST_SAMPLES}")
    if (train_metrics["average_excess_return"] or 0) <= 0:
        reasons.append("training QQQ excess return is not positive")
    if (test_metrics["average_excess_return"] or 0) <= 0:
        reasons.append("test QQQ excess return is not positive")
    if test_metrics["payoff_ratio"] is None or float(test_metrics["payoff_ratio"]) <= 1:
        reasons.append("test payoff ratio is not above 1")
    if test_metrics["excess_ci_low"] is None or float(test_metrics["excess_ci_low"]) <= 0:
        reasons.append("95% bootstrap lower bound for excess return is not positive")
    if positive_test_folds < required_positive_folds:
        reasons.append(
            f"positive chronological test folds {positive_test_folds} "
            f"< {required_positive_folds}/{len(test_folds)}"
        )
    preliminary_validated = not reasons
    result = BacktestResult(
        ticker=ticker,
        strategy=strategy,
        strategy_label=STRATEGIES[strategy],
        horizon=horizon,
        signal_version=SIGNAL_VERSION,
        train_samples=len(train),
        test_samples=len(test),
        total_samples=len(trades),
        test_win_rate=test_metrics["win_rate"],
        test_average_return=test_metrics["average_return"],
        test_median_return=test_metrics["median_return"],
        test_payoff_ratio=test_metrics["payoff_ratio"],
        test_max_drawdown=test_metrics["max_drawdown"],
        test_average_qqq_return=test_metrics["average_qqq_return"],
        test_average_excess_return=test_metrics["average_excess_return"],
        test_excess_ci_low=test_metrics["excess_ci_low"],
        test_excess_ci_high=test_metrics["excess_ci_high"],
        test_excess_pvalue=test_metrics["excess_pvalue"],
        fdr_qvalue=None,
        train_average_excess_return=train_metrics["average_excess_return"],
        test_fold_count=len(test_folds),
        positive_test_folds=positive_test_folds,
        preliminary_validated=preliminary_validated,
        validated=False,
        hold_reason="" if preliminary_validated else "; ".join(reasons),
    )
    return result, trades


def apply_fdr(results: Iterable[BacktestResult], *, alpha: float = 0.05) -> list[BacktestResult]:
    """Apply Benjamini-Hochberg across every strategy/ticker/horizon comparison."""
    rows = list(results)
    if not rows:
        return []
    pvalues = np.asarray([
        result.test_excess_pvalue
        if result.test_excess_pvalue is not None and math.isfinite(result.test_excess_pvalue)
        else 1.0
        for result in rows
    ], dtype=float)
    order = np.argsort(pvalues)
    adjusted = np.ones(len(rows), dtype=float)
    running = 1.0
    for reverse_rank in range(len(rows) - 1, -1, -1):
        index = order[reverse_rank]
        rank = reverse_rank + 1
        candidate = min(1.0, pvalues[index] * len(rows) / rank)
        running = min(running, candidate)
        adjusted[index] = running
    output: list[BacktestResult] = []
    for index, result in enumerate(rows):
        passes = result.preliminary_validated and adjusted[index] <= alpha
        reason = result.hold_reason
        if result.preliminary_validated and not passes:
            reason = (
                f"FDR-adjusted q-value {adjusted[index]:.6f} exceeds {alpha:.2f}"
            )
        output.append(replace(
            result,
            fdr_qvalue=float(adjusted[index]),
            validated=passes,
            hold_reason="" if passes else reason,
        ))
    return output


def latest_active_signals(
    *, ticker: str, asset: pd.DataFrame, backtests: Iterable[BacktestResult]
) -> list[dict]:
    frame = calculate_signals(asset)
    if frame.empty:
        return []
    latest = frame.iloc[-1]
    by_strategy: dict[str, list[BacktestResult]] = {}
    for result in backtests:
        if result.validated:
            by_strategy.setdefault(result.strategy, []).append(result)
    output: list[dict] = []
    for strategy, results in by_strategy.items():
        if not bool(latest[strategy]):
            continue
        best = max(
            results,
            key=lambda item: (
                item.test_excess_ci_low or float("-inf"),
                item.test_average_excess_return or float("-inf"),
            ),
        )
        output.append({
            "ticker": ticker,
            "signal_date": pd.Timestamp(latest["date"]).date().isoformat(),
            "strategy": strategy,
            "strategy_label": STRATEGIES[strategy],
            "best_horizon": best.horizon,
            "test_samples": best.test_samples,
            "win_rate": best.test_win_rate,
            "average_return": best.test_average_return,
            "payoff_ratio": best.test_payoff_ratio,
            "max_drawdown": best.test_max_drawdown,
            "average_excess_return": best.test_average_excess_return,
            "excess_ci_low": best.test_excess_ci_low,
            "signal_version": SIGNAL_VERSION,
            "status": "validated",
        })
    return output

