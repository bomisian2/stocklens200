from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from experiments.library_poc import _validated_daily_returns


def test_quantstats_input_rejects_trade_summary(tmp_path: Path) -> None:
    path = tmp_path / "trade-summary.csv"
    pd.DataFrame({"average_return": [0.1], "win_rate": [0.7]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="daily return input lacks"):
        _validated_daily_returns(path)


def test_quantstats_input_accepts_complete_daily_series(tmp_path: Path) -> None:
    path = tmp_path / "daily.csv"
    pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=252, freq="D"),
            "strategy_return": [0.001] * 252,
            "qqq_return": [0.0005] * 252,
        }
    ).to_csv(path, index=False)
    result = _validated_daily_returns(path)
    assert list(result.columns) == ["strategy_return", "qqq_return"]
    assert len(result) == 252
