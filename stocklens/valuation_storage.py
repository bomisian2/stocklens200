from __future__ import annotations

import json
import os
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .financials import FinancialPeriod
from .valuation import ValuationResult


class ValuationStoreError(RuntimeError):
    pass


class ValuationStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.financials_path = self.data_dir / "financials.csv"
        self.valuations_path = self.data_dir / "valuations.csv"
        self.history_path = self.data_dir / "valuation_history.csv"
        self.status_path = self.data_dir / "valuation_status.json"

    def load_financials(self) -> list[FinancialPeriod]:
        if not self.financials_path.exists():
            return []
        try:
            frame = pd.read_csv(self.financials_path, keep_default_na=False)
        except pd.errors.EmptyDataError:
            return []
        allowed = {field.name for field in fields(FinancialPeriod)}
        missing = allowed - set(frame.columns)
        if missing:
            raise ValuationStoreError(f"financial columns missing: {sorted(missing)}")
        numeric = {
            "revenue", "operating_income", "net_income", "pretax_income", "income_tax",
            "diluted_eps", "diluted_shares", "cfo", "capex", "fcf", "cash", "total_debt",
            "equity", "sbc", "depreciation_amortization", "buybacks",
        }
        rows: list[FinancialPeriod] = []
        for raw in frame.to_dict(orient="records"):
            values: dict[str, Any] = {}
            for key in allowed:
                value = raw.get(key)
                if key in numeric:
                    values[key] = None if value in {"", None} else float(value)
                elif key in {"cik", "fiscal_year"}:
                    values[key] = int(value)
                elif key == "period_start":
                    values[key] = None if value in {"", None} else str(value)
                else:
                    values[key] = str(value)
            rows.append(FinancialPeriod(**values))
        return rows

    def save_financials(self, periods: Iterable[FinancialPeriod]) -> pd.DataFrame:
        financial_columns = [field.name for field in fields(FinancialPeriod)]
        frame = pd.DataFrame([period.to_dict() for period in periods], columns=financial_columns)
        if not frame.empty:
            frame = frame.sort_values(["ticker", "period_end", "filed_at"]).drop_duplicates(
                ["ticker", "period_type", "period_end", "available_at_utc"], keep="last"
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_csv(frame, self.financials_path)
        return frame

    def load_valuations(self) -> pd.DataFrame:
        if not self.valuations_path.exists():
            return pd.DataFrame()
        return pd.read_csv(self.valuations_path)

    def save_valuations(self, results: Iterable[ValuationResult], *, require_200: bool = True) -> pd.DataFrame:
        frame = pd.DataFrame([result.to_dict() for result in results])
        if frame.empty:
            raise ValuationStoreError("valuation output is empty")
        if frame["ticker"].duplicated().any():
            raise ValuationStoreError("valuation output contains duplicate tickers")
        if require_200 and len(frame) != 200:
            raise ValuationStoreError(f"valuation output must contain 200 rows, got {len(frame)}")
        evaluated = frame["status"].eq("evaluated")
        if frame.loc[evaluated, "base_value"].isna().any() or (frame.loc[evaluated, "base_value"] <= 0).any():
            raise ValuationStoreError("evaluated rows require a positive base value")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_csv(frame, self.valuations_path)
        self._append_history(frame)
        return frame

    def _append_history(self, current: pd.DataFrame) -> None:
        selected = current[
            [
                "evaluated_at_utc", "price_as_of_utc", "ticker", "model_version", "status",
                "conservative_value", "base_value", "optimistic_value", "confidence_score",
                "judgement", "change_reason",
            ]
        ].copy()
        if self.history_path.exists():
            history = pd.read_csv(self.history_path)
            combined = pd.concat([history, selected], ignore_index=True)
        else:
            combined = selected
        combined = combined.drop_duplicates(
            ["evaluated_at_utc", "ticker", "model_version"], keep="last"
        ).sort_values(["evaluated_at_utc", "ticker"])
        self._atomic_csv(combined, self.history_path)

    def save_status(self, status: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", self.status_path)

    def load_status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {}
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _atomic_csv(frame: pd.DataFrame, target: Path) -> None:
        handle, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(handle)
        temp = Path(temp_name)
        try:
            frame.to_csv(temp, index=False, encoding="utf-8")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _atomic_text(content: str, target: Path) -> None:
        handle, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(handle)
        temp = Path(temp_name)
        try:
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

