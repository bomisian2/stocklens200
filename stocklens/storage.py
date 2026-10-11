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
    "market_time_utc",
    "collected_at_utc",
    "source",
    "metadata_source",
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
        frame = self._upgrade_legacy_frame(frame)
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
        prices = pd.to_numeric(frame["price"], errors="coerce")
        if prices.notna().mean() < 0.99 or (prices.dropna() <= 0).any():
            raise SnapshotError("at least 99% of prices must be present and positive")
        volumes = pd.to_numeric(frame["volume"], errors="coerce")
        if volumes.notna().mean() < 0.95 or (volumes.dropna() < 0).any():
            raise SnapshotError("at least 95% of volumes must be present and non-negative")
        market_times = pd.to_datetime(frame["market_time_utc"], errors="coerce", utc=True, format="mixed")
        collected_times = pd.to_datetime(frame["collected_at_utc"], errors="coerce", utc=True, format="mixed")
        if market_times.notna().mean() < 1.0:
            raise SnapshotError("all market timestamps must be present and valid")
        if collected_times.notna().mean() < 1.0:
            raise SnapshotError("all collection timestamps must be present and valid")

    @staticmethod
    def quality_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
        def coverage(column: str, *, numeric: bool = False) -> float:
            values = pd.to_numeric(frame[column], errors="coerce") if numeric else frame[column]
            present = values.notna() & values.astype(str).str.strip().ne("") & values.astype(str).ne("Unknown")
            return round(float(present.mean()), 4)

        prices = pd.to_numeric(frame["price"], errors="coerce")
        caps = pd.to_numeric(frame["market_cap"], errors="coerce")
        volumes = pd.to_numeric(frame["volume"], errors="coerce")
        market_times = pd.to_datetime(frame["market_time_utc"], errors="coerce", utc=True, format="mixed")
        return {
            "row_count": int(len(frame)),
            "price_coverage": coverage("price", numeric=True),
            "market_cap_coverage": coverage("market_cap", numeric=True),
            "volume_coverage": coverage("volume", numeric=True),
            "market_time_coverage": round(float(market_times.notna().mean()), 4),
            "sector_coverage": coverage("sector"),
            "country_coverage": coverage("country"),
            "nonpositive_price_count": int((prices.dropna() <= 0).sum()),
            "nonpositive_market_cap_count": int((caps.dropna() <= 0).sum()),
            "negative_volume_count": int((volumes.dropna() < 0).sum()),
        }

    @staticmethod
    def build_frame(records: Iterable[UniverseRecord]) -> pd.DataFrame:
        rows = [{"rank": rank, **asdict(record)} for rank, record in enumerate(records, start=1)]
        return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)

    def save(self, records: Iterable[UniverseRecord], status: dict[str, Any]) -> pd.DataFrame:
        frame = self.build_frame(records)
        self.validate(frame)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_csv(frame, self.snapshot_path)
        self._atomic_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", self.status_path)
        return frame

    def _upgrade_legacy_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Read schema-v1 last-good data without conflating quote and collection time."""
        upgraded = frame.copy()
        if "market_time_utc" not in upgraded and "as_of" in upgraded:
            upgraded = upgraded.rename(columns={"as_of": "market_time_utc"})
        status = self.load_status()
        if "collected_at_utc" not in upgraded:
            collected_at = status.get("data_generated_at_utc") or status.get("generated_at_utc")
            upgraded["collected_at_utc"] = collected_at
        if "metadata_source" not in upgraded:
            upgraded["metadata_source"] = upgraded.get("source", "legacy")
        return upgraded

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
