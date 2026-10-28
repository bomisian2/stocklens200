from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, replace
from typing import Iterable

import numpy as np
import pandas as pd


SIGNAL_VERSION = "signals-v2.1.0"
BASE_ROUND_TRIP_COST = 0.002
STRESSED_ROUND_TRIP_COST = 0.006
HORIZONS = (5, 10, 20)
MIN_RESEARCH_TOTAL = 30
MIN_TRAIN_SAMPLES = 20
MIN_WALK_FORWARD_SAMPLES = 10
MIN_FINAL_SAMPLES = 20
MIN_INDEPENDENT_TOTAL = 80
MIN_TOTAL_SAMPLES = MIN_INDEPENDENT_TOTAL

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
    walk_forward_samples: int
    final_samples: int
    total_samples: int
    final_period_start: str | None
    walk_forward_fold_count: int
    positive_walk_forward_folds: int
    research_average_excess_return: float | None
    final_win_rate: float | None
    final_average_return: float | None
    final_median_return: float | None
    final_payoff_ratio: float | None
    final_trade_equity_max_drawdown: float | None
    final_mtm_max_drawdown: float | None
    final_average_mae: float | None
    final_average_mfe: float | None
    final_worst_entry_gap: float | None
    final_average_qqq_return: float | None
    final_average_excess_return: float | None
    final_excess_ci_low: float | None
    final_excess_ci_high: float | None
    final_excess_pvalue: float | None
    fdr_qvalue: float | None
    final_zero_cost_average_return: float | None
    final_stressed_cost_average_return: float | None
    maximum_strategy_overlap: float | None
    preliminary_validated: bool
    research_candidate: bool
    independent_validated: bool
    validated: bool
    validation_state: str
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

    frame["ema_crossback"] = trend & (close > frame["ema20"]) & (
        close.shift(1) <= frame["ema20"].shift(1)
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
    frame["rsi_recovery"] = trend & (frame["rsi14"] > 40) & (
        frame["rsi14"].shift(1) <= 40
    )
    for strategy in STRATEGIES:
        frame[strategy] = frame[strategy].fillna(False).astype(bool)
    return frame.reset_index(drop=True)


def strategy_overlap(history: pd.DataFrame) -> dict[str, float]:
    """Return each strategy's largest same-day Jaccard overlap with another rule."""
    frame = calculate_signals(history)
    event_sets = {
        strategy: set(frame.loc[frame[strategy], "date"].astype(str))
        for strategy in STRATEGIES
    }
    result: dict[str, float] = {}
    for strategy, events in event_sets.items():
        overlaps: list[float] = []
        for other, other_events in event_sets.items():
            if other == strategy:
                continue
            union = events | other_events
            overlaps.append(len(events & other_events) / len(union) if union else 0.0)
        result[strategy] = max(overlaps, default=0.0)
    return result


def _aligned(asset: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    left = calculate_signals(asset)
    right = benchmark.copy()
    right["date"] = pd.to_datetime(right["date"], errors="coerce").dt.normalize()
    right = right.sort_values("date").drop_duplicates("date", keep="last")
    right = right[["date", "open", "close"]].rename(
        columns={"open": "qqq_open", "close": "qqq_close"}
    )
    return left.merge(right, on="date", how="inner", validate="one_to_one").sort_values(
        "date"
    ).reset_index(drop=True)


def _net_return(entry: float, exit_value: float, round_trip_cost: float) -> float:
    half_cost = round_trip_cost / 2
    return float(exit_value * (1 - half_cost) / (entry * (1 + half_cost)) - 1)


def build_trades(
    asset: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    strategy: str,
    horizon: int,
    cost: float = BASE_ROUND_TRIP_COST,
) -> pd.DataFrame:
    """Enter next-session open and capture daily MTM risk without overlapping trades."""
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
        signal = frame.iloc[index]
        entry = frame.iloc[entry_index]
        exit_row = frame.iloc[exit_index]
        numbers = (
            signal["close"], entry["open"], exit_row["close"],
            entry["qqq_open"], exit_row["qqq_close"],
        )
        if any(pd.isna(value) or float(value) <= 0 for value in numbers):
            continue
        holding = frame.iloc[entry_index : exit_index + 1]
        entry_exec = float(entry["open"]) * (1 + cost / 2)
        exit_exec = float(exit_row["close"]) * (1 - cost / 2)
        qqq_entry_exec = float(entry["qqq_open"]) * (1 + cost / 2)
        qqq_exit_exec = float(exit_row["qqq_close"]) * (1 - cost / 2)
        mtm_multipliers = (holding["close"].astype(float) / entry_exec).tolist()
        mtm_multipliers[-1] = exit_exec / entry_exec
        zero_cost_return = float(exit_row["close"] / entry["open"] - 1)
        stressed_return = _net_return(
            float(entry["open"]), float(exit_row["close"]), STRESSED_ROUND_TRIP_COST
        )
        asset_return = exit_exec / entry_exec - 1
        qqq_return = qqq_exit_exec / qqq_entry_exec - 1
        rows.append({
            "signal_date": signal["date"],
            "entry_date": entry["date"],
            "exit_date": exit_row["date"],
            "signal_close": float(signal["close"]),
            "entry_open": float(entry["open"]),
            "exit_close": float(exit_row["close"]),
            "entry_gap_return": float(entry["open"] / signal["close"] - 1),
            "zero_cost_return": zero_cost_return,
            "asset_return": asset_return,
            "stressed_cost_return": stressed_return,
            "qqq_return": qqq_return,
            "excess_return": asset_return - qqq_return,
            "mae": float(holding["low"].min() / entry_exec - 1),
            "mfe": float(holding["high"].max() / entry_exec - 1),
            "mtm_multipliers": tuple(mtm_multipliers),
        })
        next_eligible = exit_index + 1
    return pd.DataFrame(rows)


def _trade_equity_max_drawdown(returns: Iterable[float]) -> float | None:
    values = np.asarray(list(returns), dtype=float)
    if len(values) == 0:
        return None
    equity = np.cumprod(1 + values)
    peak = np.maximum.accumulate(np.insert(equity, 0, 1.0))[1:]
    return float(np.min(equity / peak - 1))


def _mtm_max_drawdown(trades: pd.DataFrame) -> float | None:
    if trades.empty:
        return None
    equity = 1.0
    levels = [equity]
    for _, trade in trades.iterrows():
        for multiplier in trade["mtm_multipliers"]:
            levels.append(equity * float(multiplier))
        equity *= 1 + float(trade["asset_return"])
    values = np.asarray(levels, dtype=float)
    peaks = np.maximum.accumulate(values)
    return float(np.min(values / peaks - 1))


def _payoff_ratio(returns: pd.Series) -> float | None:
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def _block_bootstrap(
    values: pd.Series, *, seed_key: str, repetitions: int = 4000
) -> tuple[float | None, float | None, float | None, int | None]:
    """Circular moving-block CI and one-sided centered-null p-value."""
    clean = values.dropna().to_numpy(dtype=float)
    if len(clean) < 3:
        return None, None, None, None
    block_length = min(len(clean), max(2, math.ceil(len(clean) ** (1 / 3))))
    block_count = math.ceil(len(clean) / block_length)
    seed = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest()[:16], 16)
    rng = np.random.default_rng(seed)
    offsets = np.arange(block_length)

    def sampled_means(source: np.ndarray) -> np.ndarray:
        starts = rng.integers(0, len(source), size=(repetitions, block_count))
        indexes = (starts[:, :, None] + offsets) % len(source)
        samples = source[indexes].reshape(repetitions, -1)[:, : len(source)]
        return samples.mean(axis=1)

    means = sampled_means(clean)
    low, high = np.quantile(means, [0.025, 0.975])
    centered_means = sampled_means(clean - clean.mean())
    pvalue = (1 + int(np.sum(centered_means >= clean.mean()))) / (repetitions + 1)
    return float(low), float(high), float(pvalue), block_length


def _metrics(trades: pd.DataFrame, *, seed_key: str) -> dict[str, float | int | None]:
    if trades.empty:
        return {
            "samples": 0, "win_rate": None, "average_return": None,
            "median_return": None, "payoff_ratio": None,
            "trade_equity_max_drawdown": None, "mtm_max_drawdown": None,
            "average_mae": None, "average_mfe": None, "worst_entry_gap": None,
            "average_qqq_return": None, "average_excess_return": None,
            "excess_ci_low": None, "excess_ci_high": None, "excess_pvalue": None,
            "bootstrap_block_length": None, "zero_cost_average_return": None,
            "stressed_cost_average_return": None,
        }
    low, high, pvalue, block_length = _block_bootstrap(
        trades["excess_return"], seed_key=seed_key
    )
    return {
        "samples": int(len(trades)),
        "win_rate": float((trades["asset_return"] > 0).mean()),
        "average_return": float(trades["asset_return"].mean()),
        "median_return": float(trades["asset_return"].median()),
        "payoff_ratio": _payoff_ratio(trades["asset_return"]),
        "trade_equity_max_drawdown": _trade_equity_max_drawdown(trades["asset_return"]),
        "mtm_max_drawdown": _mtm_max_drawdown(trades),
        "average_mae": float(trades["mae"].mean()),
        "average_mfe": float(trades["mfe"].mean()),
        "worst_entry_gap": float(trades["entry_gap_return"].min()),
        "average_qqq_return": float(trades["qqq_return"].mean()),
        "average_excess_return": float(trades["excess_return"].mean()),
        "excess_ci_low": low,
        "excess_ci_high": high,
        "excess_pvalue": pvalue,
        "bootstrap_block_length": block_length,
        "zero_cost_average_return": float(trades["zero_cost_return"].mean()),
        "stressed_cost_average_return": float(trades["stressed_cost_return"].mean()),
    }


def _split_walk_forward(
    trades: pd.DataFrame, *, final_period_start: pd.Timestamp,
) -> tuple[pd.DataFrame, list[pd.DataFrame], pd.DataFrame]:
    if trades.empty:
        return trades.copy(), [], trades.copy()
    signal_dates = pd.to_datetime(trades["signal_date"], errors="coerce")
    development = trades.loc[signal_dates < final_period_start]
    final = trades.loc[signal_dates >= final_period_start]
    train_count = math.floor(len(development) * 0.50)
    train = development.iloc[:train_count]
    walk_forward = development.iloc[train_count:]
    indexes = (
        np.array_split(np.arange(len(walk_forward)), min(3, len(walk_forward)))
        if len(walk_forward) else []
    )
    folds = [walk_forward.iloc[index] for index in indexes if len(index)]
    return train, folds, final


def evaluate_strategy(
    *,
    ticker: str,
    asset: pd.DataFrame,
    benchmark: pd.DataFrame,
    strategy: str,
    horizon: int,
    maximum_strategy_overlap: float | None = None,
) -> tuple[BacktestResult, pd.DataFrame]:
    trades = build_trades(asset, benchmark, strategy=strategy, horizon=horizon)
    asset_dates = pd.to_datetime(asset["date"], errors="coerce").dropna().dt.normalize()
    benchmark_dates = pd.to_datetime(
        benchmark["date"], errors="coerce"
    ).dropna().dt.normalize()
    common_dates = pd.Series(sorted(set(asset_dates) & set(benchmark_dates)))
    if common_dates.empty:
        raise ValueError("asset and benchmark have no aligned sessions")
    cutoff_index = min(len(common_dates) - 1, math.floor(len(common_dates) * 0.80))
    final_period_start = pd.Timestamp(common_dates.iloc[cutoff_index])
    train, walk_forward_folds, final = _split_walk_forward(
        trades, final_period_start=final_period_start
    )
    walk_forward = (
        pd.concat(walk_forward_folds).sort_index()
        if walk_forward_folds else trades.iloc[:0]
    )
    train_metrics = _metrics(train, seed_key=f"{ticker}:{strategy}:{horizon}:train")
    walk_metrics = _metrics(
        walk_forward, seed_key=f"{ticker}:{strategy}:{horizon}:walk-forward"
    )
    final_metrics = _metrics(final, seed_key=f"{ticker}:{strategy}:{horizon}:final")
    positive_folds = sum(
        float(fold["excess_return"].mean()) > 0 for fold in walk_forward_folds
    )
    required_positive = (
        math.ceil(len(walk_forward_folds) * 2 / 3) if walk_forward_folds else 0
    )

    research_reasons: list[str] = []
    if len(trades) < MIN_RESEARCH_TOTAL:
        research_reasons.append(f"total samples {len(trades)} < {MIN_RESEARCH_TOTAL}")
    if len(train) < MIN_TRAIN_SAMPLES:
        research_reasons.append(f"development train samples {len(train)} < {MIN_TRAIN_SAMPLES}")
    if len(walk_forward) < MIN_WALK_FORWARD_SAMPLES:
        research_reasons.append(
            f"walk-forward samples {len(walk_forward)} < {MIN_WALK_FORWARD_SAMPLES}"
        )
    if (train_metrics["average_excess_return"] or 0) <= 0:
        research_reasons.append("development train QQQ excess return is not positive")
    if (walk_metrics["average_excess_return"] or 0) <= 0:
        research_reasons.append("walk-forward QQQ excess return is not positive")
    if walk_metrics["payoff_ratio"] is None or float(walk_metrics["payoff_ratio"]) <= 1:
        research_reasons.append("walk-forward payoff ratio is not above 1")
    if walk_metrics["excess_ci_low"] is None or float(walk_metrics["excess_ci_low"]) <= 0:
        research_reasons.append("walk-forward block-bootstrap lower bound is not positive")
    if positive_folds < required_positive:
        research_reasons.append(
            f"positive walk-forward folds {positive_folds} "
            f"< {required_positive}/{len(walk_forward_folds)}"
        )
    research_candidate = not research_reasons

    independent_reasons: list[str] = []
    if len(trades) < MIN_INDEPENDENT_TOTAL:
        independent_reasons.append(
            f"independent-validation total samples {len(trades)} < {MIN_INDEPENDENT_TOTAL}"
        )
    if len(final) < MIN_FINAL_SAMPLES:
        independent_reasons.append(
            f"untouched final samples {len(final)} < {MIN_FINAL_SAMPLES}"
        )
    if (final_metrics["average_excess_return"] or 0) <= 0:
        independent_reasons.append("untouched final QQQ excess return is not positive")
    if final_metrics["payoff_ratio"] is None or float(final_metrics["payoff_ratio"]) <= 1:
        independent_reasons.append("untouched final payoff ratio is not above 1")
    if final_metrics["excess_ci_low"] is None or float(final_metrics["excess_ci_low"]) <= 0:
        independent_reasons.append("untouched final block-bootstrap lower bound is not positive")
    if (final_metrics["stressed_cost_average_return"] or 0) <= 0:
        independent_reasons.append("untouched final return is not positive at 60 bps cost")

    preliminary = research_candidate and not independent_reasons
    if research_candidate:
        state = "research_candidate"
        reasons = independent_reasons
    elif len(trades) < MIN_INDEPENDENT_TOTAL or len(final) < MIN_FINAL_SAMPLES:
        state = "data_insufficient"
        reasons = research_reasons + independent_reasons
    else:
        state = "not_validated"
        reasons = research_reasons
    result = BacktestResult(
        ticker=ticker,
        strategy=strategy,
        strategy_label=STRATEGIES[strategy],
        horizon=horizon,
        signal_version=SIGNAL_VERSION,
        train_samples=len(train),
        walk_forward_samples=len(walk_forward),
        final_samples=len(final),
        total_samples=len(trades),
        final_period_start=final_period_start.date().isoformat(),
        walk_forward_fold_count=len(walk_forward_folds),
        positive_walk_forward_folds=positive_folds,
        research_average_excess_return=walk_metrics["average_excess_return"],
        final_win_rate=final_metrics["win_rate"],
        final_average_return=final_metrics["average_return"],
        final_median_return=final_metrics["median_return"],
        final_payoff_ratio=final_metrics["payoff_ratio"],
        final_trade_equity_max_drawdown=final_metrics["trade_equity_max_drawdown"],
        final_mtm_max_drawdown=final_metrics["mtm_max_drawdown"],
        final_average_mae=final_metrics["average_mae"],
        final_average_mfe=final_metrics["average_mfe"],
        final_worst_entry_gap=final_metrics["worst_entry_gap"],
        final_average_qqq_return=final_metrics["average_qqq_return"],
        final_average_excess_return=final_metrics["average_excess_return"],
        final_excess_ci_low=final_metrics["excess_ci_low"],
        final_excess_ci_high=final_metrics["excess_ci_high"],
        final_excess_pvalue=final_metrics["excess_pvalue"] if preliminary else 1.0,
        fdr_qvalue=None,
        final_zero_cost_average_return=final_metrics["zero_cost_average_return"],
        final_stressed_cost_average_return=final_metrics["stressed_cost_average_return"],
        maximum_strategy_overlap=maximum_strategy_overlap,
        preliminary_validated=preliminary,
        research_candidate=research_candidate,
        independent_validated=False,
        validated=False,
        validation_state=state,
        hold_reason="; ".join(reasons),
    )
    return result, trades


def apply_fdr(results: Iterable[BacktestResult], *, alpha: float = 0.05) -> list[BacktestResult]:
    """Apply dependence-robust Benjamini-Yekutieli correction to final-period p-values."""
    rows = list(results)
    if not rows:
        return []
    pvalues = np.asarray([
        result.final_excess_pvalue
        if result.final_excess_pvalue is not None and math.isfinite(result.final_excess_pvalue)
        else 1.0
        for result in rows
    ], dtype=float)
    order = np.argsort(pvalues)
    adjusted = np.ones(len(rows), dtype=float)
    harmonic = sum(1 / rank for rank in range(1, len(rows) + 1))
    running = 1.0
    for reverse_rank in range(len(rows) - 1, -1, -1):
        index = order[reverse_rank]
        rank = reverse_rank + 1
        candidate = min(1.0, pvalues[index] * len(rows) * harmonic / rank)
        running = min(running, candidate)
        adjusted[index] = running
    output: list[BacktestResult] = []
    for index, result in enumerate(rows):
        passes = result.preliminary_validated and adjusted[index] <= alpha
        reason = result.hold_reason
        state = result.validation_state
        if passes:
            state = "independent_validated"
            reason = ""
        elif result.preliminary_validated:
            state = "research_candidate"
            reason = (
                f"dependence-robust FDR q-value {adjusted[index]:.6f} exceeds {alpha:.2f}"
            )
        output.append(replace(
            result,
            fdr_qvalue=float(adjusted[index]),
            independent_validated=passes,
            validated=passes,
            validation_state=state,
            hold_reason=reason,
        ))
    return output


def latest_active_signals(
    *, ticker: str, asset: pd.DataFrame, backtests: Iterable[BacktestResult],
    next_session_date: str | None = None,
) -> list[dict]:
    frame = calculate_signals(asset)
    if frame.empty:
        return []
    latest = frame.iloc[-1]
    by_strategy: dict[str, list[BacktestResult]] = {}
    for result in backtests:
        if result.independent_validated:
            by_strategy.setdefault(result.strategy, []).append(result)
    active_strategies = [strategy for strategy in by_strategy if bool(latest[strategy])]
    output: list[dict] = []
    for strategy in active_strategies:
        best = max(
            by_strategy[strategy],
            key=lambda item: (
                item.final_excess_ci_low or float("-inf"),
                item.final_average_excess_return or float("-inf"),
            ),
        )
        output.append({
            "ticker": ticker,
            "signal_date": pd.Timestamp(latest["date"]).date().isoformat(),
            "entry_expected_date": next_session_date,
            "strategy": strategy,
            "strategy_label": STRATEGIES[strategy],
            "overlapping_strategies": ", ".join(STRATEGIES[item] for item in active_strategies),
            "signal_cluster_size": len(active_strategies),
            "best_horizon": best.horizon,
            "validation_samples": best.final_samples,
            "win_rate": best.final_win_rate,
            "expected_return": best.final_average_return,
            "payoff_ratio": best.final_payoff_ratio,
            "mtm_max_drawdown": best.final_mtm_max_drawdown,
            "average_mae": best.final_average_mae,
            "average_mfe": best.final_average_mfe,
            "average_excess_return": best.final_average_excess_return,
            "excess_ci_low": best.final_excess_ci_low,
            "confidence": "independent_validation_passed",
            "signal_version": SIGNAL_VERSION,
            "status": "current_active",
        })
    return output
