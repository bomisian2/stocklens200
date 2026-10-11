from __future__ import annotations

import pandas as pd
import pytest

from stocklens.storage import SNAPSHOT_COLUMNS, SnapshotError, SnapshotStore


def valid_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "rank": index + 1,
                "ticker": f"T{index:03d}",
                "name": f"Company {index}",
                "sector": "Technology",
                "industry": "Software",
                "country": "United States",
                "market_cap": 1_000_000 - index,
                "price": 100.0,
                "change_percent": 0.0,
                "volume": 1_000,
                "market_time_utc": "2026-01-02T21:00:00+00:00",
                "collected_at_utc": "2026-01-03T12:00:00+00:00",
                "source": "test",
                "metadata_source": "reference",
                "issuer_key": f"company-{index}",
            }
            for index in range(200)
        ],
        columns=SNAPSHOT_COLUMNS,
    )


def test_validation_rejects_incomplete_snapshot() -> None:
    frame = pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    with pytest.raises(SnapshotError, match="exactly 200"):
        SnapshotStore.validate(frame)


def test_validation_rejects_excessive_missing_prices() -> None:
    frame = valid_frame()
    frame.loc[:2, "price"] = None
    with pytest.raises(SnapshotError, match="prices"):
        SnapshotStore.validate(frame)


def test_validation_rejects_missing_or_invalid_market_times() -> None:
    frame = valid_frame()
    frame.loc[0, "market_time_utc"] = "not-a-time"
    with pytest.raises(SnapshotError, match="market timestamps"):
        SnapshotStore.validate(frame)


def test_quality_metrics_report_metadata_coverage() -> None:
    frame = valid_frame()
    frame.loc[:19, "sector"] = "Unknown"
    metrics = SnapshotStore.quality_metrics(frame)
    assert metrics["sector_coverage"] == 0.9
    assert metrics["price_coverage"] == 1.0
