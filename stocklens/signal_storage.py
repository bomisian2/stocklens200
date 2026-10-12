from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from .signals import MIN_TEST_SAMPLES, SIGNAL_VERSION


class SignalStoreError(RuntimeError):
    pass


class SignalStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.backtests_path = self.data_dir / "signal_backtests.csv"
        self.signals_path = self.data_dir / "signals.csv"
        self.history_path = self.data_dir / "signal_history.csv"
        self.status_path = self.data_dir / "signal_status.json"

    def save_backtests(self, frame: pd.DataFrame) -> None:
        required = {
            "ticker", "strategy", "horizon", "signal_version", "test_samples",
            "test_average_excess_return", "test_payoff_ratio", "test_excess_pvalue",
            "fdr_qvalue", "test_fold_count", "positive_test_folds", "validated",
            "hold_reason",
        }
        missing = required - set(frame.columns)
        if missing:
            raise SignalStoreError(f"backtest columns missing: {sorted(missing)}")
        duplicates = frame.duplicated(["ticker", "strategy", "horizon"])
        if duplicates.any():
            raise SignalStoreError("backtest output contains duplicate strategy horizons")
        validated = frame["validated"].astype(bool)
        if (frame.loc[validated, "signal_version"] != SIGNAL_VERSION).any():
            raise SignalStoreError("validated output has an unexpected signal version")
        if (pd.to_numeric(frame.loc[validated, "test_samples"]) < MIN_TEST_SAMPLES).any():
            raise SignalStoreError("validated output does not meet the sample gate")
        required_folds = (
            pd.to_numeric(frame.loc[validated, "test_fold_count"]) * 2 / 3
        ).apply(math.ceil)
        if (
            pd.to_numeric(frame.loc[validated, "positive_test_folds"]).to_numpy()
            < required_folds.to_numpy()
        ).any():
            raise SignalStoreError("validated output failed the chronological stability gate")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_csv(frame, self.backtests_path)

    def save_signals(self, frame: pd.DataFrame, *, expected_session: str) -> None:
        required = {
            "ticker", "signal_date", "strategy", "best_horizon", "test_samples",
            "average_excess_return", "excess_ci_low", "status", "signal_version",
        }
        if frame.empty:
            frame = pd.DataFrame(columns=sorted(required))
        missing = required - set(frame.columns)
        if missing:
            raise SignalStoreError(f"signal columns missing: {sorted(missing)}")
        if not frame.empty:
            if not frame["status"].eq("validated").all():
                raise SignalStoreError("only validated signals may be published")
            if not frame["signal_version"].eq(SIGNAL_VERSION).all():
                raise SignalStoreError("signal version mismatch")
            dates = pd.to_datetime(frame["signal_date"], errors="coerce")
            if dates.isna().any() or not dates.dt.date.astype(str).eq(expected_session).all():
                raise SignalStoreError("published signals must match the verified market session")
            if pd.to_numeric(frame["excess_ci_low"], errors="coerce").le(0).any():
                raise SignalStoreError("published signals require a positive excess-return lower bound")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_csv(frame, self.signals_path)
        if not frame.empty:
            self._append_history(frame)

    def _append_history(self, frame: pd.DataFrame) -> None:
        selected = frame.copy()
        if self.history_path.exists():
            history = pd.read_csv(self.history_path)
            selected = pd.concat([history, selected], ignore_index=True)
        selected = selected.drop_duplicates(
            ["signal_date", "ticker", "strategy", "signal_version"], keep="last"
        ).sort_values(["signal_date", "ticker", "strategy"])
        self._atomic_csv(selected, self.history_path)

    def save_status(self, status: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_text(
            json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            self.status_path,
        )

    @staticmethod
    def _atomic_csv(frame: pd.DataFrame, target: Path) -> None:
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        os.close(handle)
        temp = Path(temp_name)
        try:
            frame.to_csv(temp, index=False, encoding="utf-8")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _atomic_text(content: str, target: Path) -> None:
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        os.close(handle)
        temp = Path(temp_name)
        try:
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

