from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .providers import UniverseRecord


SNAPSHOT_COLUMNS = [
    "rank",
    "ticker",
    "name",
    "sector",
    "industry",
    "country",
    "market_cap",
    "price",
    "change_percent",
    "volume",
    "as_of",
    "source",
    "issuer_key",
]


class SnapshotError(RuntimeError):
    pass


class SnapshotStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.snapshot_path = self.data_dir / "universe.csv"
        self.status_path = self.data_dir / "status.json"

    def load(self) -> pd.DataFrame:
        if not self.snapshot_path.exists():
            raise SnapshotError(f"snapshot not found: {self.snapshot_path}")
        frame = pd.read_csv(self.snapshot_path)
        self.validate(frame)
        return frame

    def load_status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {}
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def validate(frame: pd.DataFrame) -> None:
        missing = set(SNAPSHOT_COLUMNS) - set(frame.columns)
        if missing:
            raise SnapshotError(f"snapshot columns missing: {sorted(missing)}")
        if len(frame) != 200:
            raise SnapshotError(f"snapshot must contain exactly 200 companies, got {len(frame)}")
        if frame["ticker"].duplicated().any() or frame["issuer_key"].duplicated().any():
            raise SnapshotError("snapshot contains duplicate tickers or issuers")
        caps = pd.to_numeric(frame["market_cap"], errors="coerce")
        if caps.isna().any() or (caps <= 0).any() or not caps.is_monotonic_decreasing:
            raise SnapshotError("market caps must be positive and sorted descending")
        ranks = pd.to_numeric(frame["rank"], errors="coerce").tolist()
        if ranks != list(range(1, 201)):
            raise SnapshotError("rank must be contiguous from 1 through 200")

    def save(self, records: Iterable[UniverseRecord], status: dict[str, Any]) -> pd.DataFrame:
        rows = []
        for rank, record in enumerate(records, start=1):
            rows.append({"rank": rank, **asdict(record)})
        frame = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
        self.validate(frame)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_csv(frame, self.snapshot_path)
        self._atomic_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", self.status_path)
        return frame

    def save_status(self, status: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", self.status_path)

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
