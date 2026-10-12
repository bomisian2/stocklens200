from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocklens.history import YahooHistoryCollector  # noqa: E402
from stocklens.signal_storage import SignalStore  # noqa: E402
from stocklens.signals import (  # noqa: E402
    HORIZONS,
    SIGNAL_VERSION,
    STRATEGIES,
    apply_fdr,
    evaluate_strategy,
    latest_active_signals,
)
from stocklens.storage import SnapshotStore  # noqa: E402


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
        for strategy in STRATEGIES:
            for horizon in HORIZONS:
                result, _ = evaluate_strategy(
                    ticker=ticker,
                    asset=collection.frames[ticker],
                    benchmark=qqq,
                    strategy=strategy,
                    horizon=horizon,
                )
                result_objects.append(result)

    result_objects = apply_fdr(result_objects)
    backtests = [result.to_dict() for result in result_objects]
    signals = []
    for ticker in available_tickers:
        ticker_results = [result for result in result_objects if result.ticker == ticker]
        signals.extend(latest_active_signals(
            ticker=ticker,
            asset=collection.frames[ticker],
            backtests=ticker_results,
        ))

    backtest_frame = pd.DataFrame(backtests)
    signal_frame = pd.DataFrame(signals)
    if not signal_frame.empty:
        signal_frame = signal_frame.sort_values(
            ["excess_ci_low", "average_excess_return"], ascending=False
        ).reset_index(drop=True)
    store = SignalStore(args.data_dir)
    if backtest_frame.empty:
        raise RuntimeError("no backtest output was produced")
    store.save_backtests(backtest_frame)
    store.save_signals(signal_frame, expected_session=expected_session)
    validated_count = int(backtest_frame["validated"].astype(bool).sum())
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
        "validated_backtest_count": validated_count,
        "active_validated_signal_count": len(signal_frame),
        "provider_errors": collection.errors[:200],
        "universe_bias_state": "current_constituents_only",
        "limitations": [
            "Historical Top-200 membership is not yet available; results have survivorship and selection bias.",
            "Yahoo/yfinance history is approved for development validation only; production redistribution review is pending.",
            "Adjusted history can be revised by later corporate actions; point-in-time adjustment factors are not yet archived.",
            "Maximum drawdown is measured on closed-trade equity and excludes intratrade mark-to-market drawdown.",
            "A validated backtest is a statistical research result, not a recommendation or guaranteed forecast.",
        ],
        "data_state": (
            "healthy" if len(available_tickers) == len(tickers) and qqq is not None
            else "degraded" if available_tickers and qqq is not None else "unverified"
        ),
        "eligible_for_display": bool(len(signal_frame)),
        "methodology": {
            "signal_time": "session close",
            "entry": "next aligned session open",
            "exit": "close after 5, 10, or 20 trading sessions from signal",
            "round_trip_cost": 0.002,
            "split": (
                "chronological 70% training / 30% out-of-sample test; "
                "test segment checked in up to three sequential stability folds"
            ),
            "non_overlapping_trades": True,
            "publication_gate": (
                "minimum samples, positive train/test QQQ excess return, test payoff > 1, "
                "positive 95% bootstrap lower bound, positive excess return in at least "
                "two-thirds of sequential test folds, and Benjamini-Hochberg FDR q <= 0.05"
            ),
        },
    }
    store.save_status(status)
    print(json.dumps({
        "selected": len(tickers),
        "history_available": len(available_tickers),
        "backtests": len(backtest_frame),
        "validated_backtests": validated_count,
        "active_signals": len(signal_frame),
        "data_state": status["data_state"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

