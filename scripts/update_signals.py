from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.history import YahooHistoryCollector  # noqa: E402
from stocklens.market_calendar import next_session_date  # noqa: E402
from stocklens.signal_storage import SignalStore  # noqa: E402
from stocklens.signals import (  # noqa: E402
    HORIZONS,
    SIGNAL_VERSION,
    STRATEGIES,
    apply_fdr,
    calculate_signals,
    evaluate_strategy,
    latest_active_signals,
    strategy_overlap,
)
from stocklens.storage import SnapshotStore  # noqa: E402


def historical_concentration(
    universe: pd.DataFrame, frames: dict[str, pd.DataFrame]
) -> dict:
    sector_by_ticker = universe.set_index("ticker")["sector"].to_dict()
    events: list[dict] = []
    for ticker, history in frames.items():
        if ticker == "QQQ":
            continue
        calculated = calculate_signals(history)
        for strategy in STRATEGIES:
            for signal_date in calculated.loc[calculated[strategy], "date"]:
                events.append({
                    "date": pd.Timestamp(signal_date).date().isoformat(),
                    "ticker": ticker,
                    "sector": sector_by_ticker.get(ticker, "Unknown"),
                    "strategy": strategy,
                })
    if not events:
        return {
            "signal_days": 0,
            "maximum_same_day_tickers": 0,
            "p95_same_day_tickers": 0.0,
            "multi_strategy_ticker_day_ratio": 0.0,
            "median_largest_sector_share": None,
            "maximum_sector_signal_count_correlation": None,
        }
    frame = pd.DataFrame(events)
    ticker_day = frame.groupby(["date", "ticker"]).size()
    daily_tickers = ticker_day.groupby("date").size()
    sector_daily = (
        frame.drop_duplicates(["date", "ticker"])
        .groupby(["date", "sector"])["ticker"]
        .nunique()
        .unstack(fill_value=0)
    )
    largest_sector_shares = sector_daily.max(axis=1) / sector_daily.sum(axis=1)
    correlations = sector_daily.corr(min_periods=20)
    if len(correlations) > 1:
        mask = ~pd.DataFrame(
            np.eye(len(correlations), dtype=bool),
            index=correlations.index,
            columns=correlations.columns,
        )
        correlation_values = correlations.where(mask).stack()
        max_correlation = (
            float(correlation_values.abs().max()) if not correlation_values.empty else None
        )
    else:
        max_correlation = None
    return {
        "signal_days": int(len(daily_tickers)),
        "maximum_same_day_tickers": int(daily_tickers.max()),
        "p95_same_day_tickers": float(daily_tickers.quantile(0.95)),
        "multi_strategy_ticker_day_ratio": float((ticker_day > 1).mean()),
        "median_largest_sector_share": float(largest_sector_shares.median()),
        "maximum_sector_signal_count_correlation": max_correlation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect adjusted daily history and run fixed point-in-time signal tests"
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument(
        "--cache-dir", type=Path, default=ROOT / "data" / "price-history-cache"
    )
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--max-companies", type=int, default=None)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument(
        "--cache-only", action="store_true",
        help="Recompute from same-session verified cache without provider requests",
    )
    args = parser.parse_args()

    snapshot = SnapshotStore(args.data_dir)
    universe = snapshot.load()
    universe_status = snapshot.load_status()
    if (
        universe_status.get("freshness_state") != "verified"
        or universe_status.get("data_state") != "healthy"
        or not universe_status.get("eligible_for_signals")
    ):
        raise RuntimeError("universe snapshot is not eligible for new signals")
    if args.tickers:
        requested = {value.upper() for value in args.tickers}
        universe = universe[universe["ticker"].isin(requested)]
    if args.max_companies:
        universe = universe.head(args.max_companies)
    if universe.empty:
        raise RuntimeError("no universe rows selected")
    expected_session = str(universe_status["market_session_date"])
    expected_date = datetime.fromisoformat(expected_session).date()
    tickers = list(universe["ticker"].astype(str))
    collector = YahooHistoryCollector(cache_dir=args.cache_dir)
    collection = collector.collect(
        [*tickers, "QQQ"],
        expected_session=expected_date,
        allow_network=not args.cache_only,
    )

    qqq = collection.frames.get("QQQ")
    available_tickers = [ticker for ticker in tickers if ticker in collection.frames]
    if qqq is None:
        available_tickers = []
        collection.errors.append({"ticker": "QQQ", "category": "benchmark_unavailable"})
    if not args.allow_partial and len(available_tickers) != len(tickers):
        raise RuntimeError(
            f"verified history required for all selected tickers: "
            f"{len(available_tickers)}/{len(tickers)}"
        )

    result_objects = []
    for ticker in available_tickers:
        overlap = strategy_overlap(collection.frames[ticker])
        for strategy in STRATEGIES:
            for horizon in HORIZONS:
                result, _ = evaluate_strategy(
                    ticker=ticker,
                    asset=collection.frames[ticker],
                    benchmark=qqq,
                    strategy=strategy,
                    horizon=horizon,
                    maximum_strategy_overlap=overlap[strategy],
                )
                result_objects.append(result)

    result_objects = apply_fdr(result_objects)
    backtests = [result.to_dict() for result in result_objects]
    signals = []
    expected_entry_date = next_session_date(expected_session)
    complete_cross_section = len(available_tickers) == len(tickers) and qqq is not None
    for ticker in available_tickers:
        ticker_results = [result for result in result_objects if result.ticker == ticker]
        signals.extend(latest_active_signals(
            ticker=ticker,
            asset=collection.frames[ticker],
            backtests=ticker_results,
            next_session_date=expected_entry_date,
        ))

    if not complete_cross_section:
        signals = []

    backtest_frame = pd.DataFrame(backtests)
    signal_frame = pd.DataFrame(signals)
    if not signal_frame.empty:
        signal_frame = signal_frame.sort_values(
            ["excess_ci_low", "average_excess_return"], ascending=False
        ).reset_index(drop=True)
    store = SignalStore(args.data_dir)
    if backtest_frame.empty:
        raise RuntimeError("no backtest output was produced")
    validated_count = int(backtest_frame["validated"].astype(bool).sum())
    research_candidate_count = int(backtest_frame["research_candidate"].astype(bool).sum())
    state_counts = {
        str(key): int(value)
        for key, value in backtest_frame["validation_state"].value_counts().items()
    }
    historical_concentration_summary = historical_concentration(
        universe, {ticker: collection.frames[ticker] for ticker in available_tickers}
    )
    if signal_frame.empty:
        concentration = {
            "active_tickers": 0,
            "active_sectors": 0,
            "largest_sector_share": None,
            "same_ticker_multi_strategy_count": 0,
        }
    else:
        sector_by_ticker = universe.set_index("ticker")["sector"].to_dict()
        signal_frame["sector"] = signal_frame["ticker"].map(sector_by_ticker).fillna("Unknown")
        sector_counts = signal_frame.drop_duplicates("ticker")["sector"].value_counts()
        concentration = {
            "active_tickers": int(signal_frame["ticker"].nunique()),
            "active_sectors": int(len(sector_counts)),
            "largest_sector_share": float(sector_counts.max() / sector_counts.sum()),
            "same_ticker_multi_strategy_count": int(
                signal_frame.loc[signal_frame["signal_cluster_size"] > 1, "ticker"].nunique()
            ),
        }
    store.save_backtests(backtest_frame)
    store.save_signals(signal_frame, expected_session=expected_session)
    status = {
        "schema_version": 1,
        "signal_version": SIGNAL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "market_session_date": expected_session,
        "source": "yahoo_adjusted_daily_via_yfinance",
        "collection_mode": "verified_cache_only" if args.cache_only else "live_with_cache_fallback",
        "benchmark": "QQQ",
        "selected_ticker_count": len(tickers),
        "history_available_ticker_count": len(available_tickers),
        "history_unavailable_ticker_count": len(tickers) - len(available_tickers),
        "fresh_history_ticker_count": len(collection.fresh_tickers & set(tickers)),
        "cached_history_ticker_count": len(collection.cached_tickers & set(tickers)),
        "backtest_count": len(backtest_frame),
        "research_candidate_count": research_candidate_count,
        "validated_backtest_count": validated_count,
        "active_validated_signal_count": len(signal_frame),
        "validation_state_counts": state_counts,
        "signal_concentration": concentration,
        "historical_signal_concentration": historical_concentration_summary,
        "provider_errors": collection.errors[:200],
        "universe_bias_state": "current_constituents_only",
        "limitations": [
            "Historical Top-200 membership is not yet available; results have survivorship and selection bias.",
            "Yahoo/yfinance history is approved for development validation only; production redistribution review is pending.",
            "Adjusted history can be revised by later corporate actions; point-in-time adjustment factors are not yet archived.",
            "Daily close MTM drawdown and intratrade low/high MAE/MFE are estimates from daily bars, not intraday execution records.",
            "A validated backtest is a statistical research result, not a recommendation or guaranteed forecast.",
        ],
        "data_state": (
            "healthy" if len(available_tickers) == len(tickers) and qqq is not None
            else "degraded" if available_tickers and qqq is not None else "unverified"
        ),
        "eligible_for_display": bool(len(signal_frame)) and complete_cross_section,
        "methodology": {
            "signal_time": "session close",
            "entry": "next aligned session open",
            "exit": "close after 5, 10, or 20 trading sessions from signal",
            "round_trip_cost": 0.002,
            "stressed_round_trip_cost": 0.006,
            "benchmark_cost": "QQQ receives the same entry/exit cost assumption",
            "split": (
                "first 80% development period with expanding chronological walk-forward folds; "
                "last 20% untouched final confirmation period"
            ),
            "dependence_control": (
                "circular moving-block bootstrap p-values and Benjamini-Yekutieli FDR; "
                "same-day strategy overlap recorded with Jaccard similarity"
            ),
            "non_overlapping_trades": True,
            "publication_gate": (
                "development research gate, at least 80 total and 20 untouched-final trades, "
                "positive final QQQ excess lower bound, payoff > 1, positive return at 60 bps, "
                "and dependence-robust FDR q <= 0.05"
            ),
        },
    }
    store.save_status(status)
    print(json.dumps({
        "selected": len(tickers),
        "history_available": len(available_tickers),
        "backtests": len(backtest_frame),
        "validated_backtests": validated_count,
        "research_candidates": research_candidate_count,
        "active_signals": len(signal_frame),
        "data_state": status["data_state"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

