from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from stocklens.history import YahooHistoryCollector, history_error_category, validate_history
from stocklens.signal_storage import SignalStore, SignalStoreError
from stocklens.signals import (
    SIGNAL_VERSION,
    apply_fdr,
    build_trades,
    calculate_signals,
    evaluate_strategy,
)


def _history(rows: int = 1800) -> pd.DataFrame:
    dates = pd.bdate_range("2018-01-01", periods=rows)
    index = np.arange(rows)
    close = 100 + 0.05 * index + 4 * np.sin(index / 4)
    opening = close * (1 + 0.002 * np.sin(index / 3))
    return pd.DataFrame({
        "date": dates,
        "ticker": "TEST",
        "open": opening,
        "high": np.maximum(opening, close) * 1.01,
        "low": np.minimum(opening, close) * 0.99,
        "close": close,
        "volume": 1_000_000 * (1 + 0.5 * (np.sin(index / 3) > 0)),
    })


def _benchmark(history: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "date": history["date"],
        "ticker": "QQQ",
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
        "volume": 1_000_000,
    })


def test_signal_calculation_does_not_change_when_future_prices_change() -> None:
    original = _history()
    cutoff = 900
    changed = original.copy()
    changed.loc[cutoff + 1 :, ["open", "high", "low", "close"]] *= 10
    before = calculate_signals(original).iloc[: cutoff + 1]
    after = calculate_signals(changed).iloc[: cutoff + 1]
    pd.testing.assert_frame_equal(
        before[["ema_crossback", "trend_breakout", "pullback_rebound", "rsi_recovery"]],
        after[["ema_crossback", "trend_breakout", "pullback_rebound", "rsi_recovery"]],
    )


def test_backtest_enters_at_next_session_open_and_has_no_overlapping_trades() -> None:
    asset = _history()
    benchmark = _benchmark(asset)
    calculated = calculate_signals(asset)
    first_signal = int(calculated.index[calculated["ema_crossback"]][0])
    trades = build_trades(
        asset, benchmark, strategy="ema_crossback", horizon=5
    )
    first = trades.iloc[0]
    assert first["signal_date"] == asset.iloc[first_signal]["date"]
    assert first["entry_date"] == asset.iloc[first_signal + 1]["date"]
    assert first["exit_date"] == asset.iloc[first_signal + 5]["date"]
    expected = (
        asset.iloc[first_signal + 5]["close"]
        / asset.iloc[first_signal + 1]["open"]
        - 1
        - 0.002
    )
    assert abs(first["asset_return"] - expected) < 1e-12
    assert (trades["signal_date"].iloc[1:].reset_index(drop=True) > trades["exit_date"].iloc[:-1].reset_index(drop=True)).all()


def test_strategy_reports_all_metrics_but_holds_if_any_gate_fails() -> None:
    asset = _history()
    result, trades = evaluate_strategy(
        ticker="TEST",
        asset=asset,
        benchmark=_benchmark(asset),
        strategy="ema_crossback",
        horizon=5,
    )
    assert len(trades) >= 30
    assert result.test_win_rate is not None
    assert result.test_average_return is not None
    assert result.test_average_excess_return is not None
    assert result.test_max_drawdown is not None
    assert result.test_excess_ci_low is not None
    assert result.test_fold_count == 3
    assert 0 <= result.positive_test_folds <= result.test_fold_count
    assert result.validated is False
    assert result.preliminary_validated is False
    assert "payoff ratio" in result.hold_reason


def test_fdr_counts_every_strategy_comparison() -> None:
    asset = _history()
    result, _ = evaluate_strategy(
        ticker="TEST", asset=asset, benchmark=_benchmark(asset),
        strategy="ema_crossback", horizon=5,
    )
    adjusted = apply_fdr([result] * 12)
    assert len(adjusted) == 12
    assert all(item.fdr_qvalue is not None for item in adjusted)
    assert all(item.validated is False for item in adjusted)


def test_chronological_stability_folds_cover_only_out_of_sample_trades() -> None:
    asset = _history()
    result, trades = evaluate_strategy(
        ticker="TEST", asset=asset, benchmark=_benchmark(asset),
        strategy="ema_crossback", horizon=10,
    )
    assert result.train_samples + result.test_samples == len(trades)
    assert result.test_fold_count == min(3, result.test_samples)
    assert result.positive_test_folds <= result.test_fold_count


def test_history_validation_rejects_stale_latest_session() -> None:
    frame = _history(rows=700)
    expected = frame["date"].iloc[-1].date()
    verified = validate_history(frame, ticker="TEST", expected_session=expected)
    assert len(verified) == 700
    try:
        validate_history(
            frame, ticker="TEST", expected_session=expected + pd.Timedelta(days=1)
        )
    except ValueError as exc:
        assert "latest session" in str(exc)
    else:
        raise AssertionError("stale history must be rejected")
    assert history_error_category(ValueError("history row count 10 < 600")) == "insufficient_history"
    assert history_error_category(ValueError("history latest session mismatch")) == "stale_history"


def test_collector_reuses_only_same_session_verified_cache(tmp_path: Path, monkeypatch) -> None:
    frame = _history(rows=700)
    expected = frame["date"].iloc[-1].date()
    collector = YahooHistoryCollector(cache_dir=tmp_path)
    collector._save_cache("TEST", validate_history(
        frame, ticker="TEST", expected_session=expected
    ))

    def unexpected_download(*args, **kwargs):
        raise AssertionError("same-session cache should avoid a provider request")

    monkeypatch.setattr(collector, "_download_batch", unexpected_download)
    result = collector.collect(["TEST"], expected_session=expected)
    assert set(result.frames) == {"TEST"}
    assert result.cached_tickers == {"TEST"}
    assert not result.fresh_tickers


def test_cache_only_mode_marks_missing_history_without_network(tmp_path: Path, monkeypatch) -> None:
    collector = YahooHistoryCollector(cache_dir=tmp_path)

    def unexpected_download(*args, **kwargs):
        raise AssertionError("cache-only mode must not call the provider")

    monkeypatch.setattr(collector, "_download_batch", unexpected_download)
    result = collector.collect(
        ["MISSING"], expected_session=date(2025, 1, 2), allow_network=False
    )
    assert result.unavailable_tickers == {"MISSING"}
    assert result.errors[0]["category"] == "verified_cache_unavailable"


def test_store_rejects_unvalidated_active_signal(tmp_path: Path) -> None:
    store = SignalStore(tmp_path)
    invalid = pd.DataFrame([{
        "ticker": "TEST",
        "signal_date": date(2025, 1, 2).isoformat(),
        "strategy": "ema_crossback",
        "best_horizon": 5,
        "test_samples": 30,
        "average_excess_return": 0.02,
        "excess_ci_low": -0.01,
        "status": "candidate",
        "signal_version": SIGNAL_VERSION,
    }])
    try:
        store.save_signals(invalid, expected_session="2025-01-02")
    except SignalStoreError as exc:
        assert "only validated" in str(exc)
    else:
        raise AssertionError("unvalidated signal must not be published")

