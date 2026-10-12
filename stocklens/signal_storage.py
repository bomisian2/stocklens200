from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from .signals import MIN_FINAL_SAMPLES, MIN_INDEPENDENT_TOTAL, SIGNAL_VERSION


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
            "ticker", "strategy", "horizon", "signal_version", "total_samples",
            "final_samples", "final_period_start", "final_average_excess_return", "final_payoff_ratio",
            "final_mtm_max_drawdown", "final_excess_pvalue", "fdr_qvalue",
            "research_candidate", "independent_validated", "validation_state",
            "validated", "hold_reason",
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
        if (pd.to_numeric(frame.loc[validated, "total_samples"]) < MIN_INDEPENDENT_TOTAL).any():
            raise SignalStoreError("validated output does not meet the total-sample gate")
        if (pd.to_numeric(frame.loc[validated, "final_samples"]) < MIN_FINAL_SAMPLES).any():
            raise SignalStoreError("validated output does not meet the untouched-final gate")
        if not frame.loc[validated, "independent_validated"].astype(bool).all():
            raise SignalStoreError("published validation must pass the independent period")
        if not frame.loc[validated, "validation_state"].eq("independent_validated").all():
            raise SignalStoreError("validated output has an inconsistent state")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_csv(frame, self.backtests_path)

    def save_signals(self, frame: pd.DataFrame, *, expected_session: str) -> None:
        required = {
            "ticker", "signal_date", "entry_expected_date", "strategy", "best_horizon",
            "validation_samples", "expected_return", "average_excess_return",
            "mtm_max_drawdown", "average_mae", "average_mfe", "excess_ci_low",
            "confidence", "status", "signal_version",
        }
        if frame.empty:
            frame = pd.DataFrame(columns=sorted(required))
        missing = required - set(frame.columns)
        if missing:
            raise SignalStoreError(f"signal columns missing: {sorted(missing)}")
        if not frame.empty:
            if not frame["status"].eq("current_active").all():
                raise SignalStoreError("only current independently validated signals may be published")
            if not frame["confidence"].eq("independent_validation_passed").all():
                raise SignalStoreError("active signal confidence is not independently validated")
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

