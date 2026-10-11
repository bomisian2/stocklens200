from __future__ import annotations

from datetime import datetime, timezone

from stocklens.pipeline import update_universe
from stocklens.providers import ProviderResult, UniverseRecord
from stocklens.storage import SnapshotStore


def records(source: str = "test") -> list[UniverseRecord]:
    return [
        UniverseRecord(
            ticker=f"T{i:03d}",
            name=f"Company {i:03d} Common Stock",
            sector="Test",
            industry="Test",
            country="United States",
            market_cap=float(1_000_000 - i),
            price=100.0,
            change_percent=0.0,
            volume=float(i),
            as_of="2026-01-01T00:00:00+00:00",
            source=source,
            issuer_key=f"company-{i:03d}",
        )
        for i in range(205)
    ]


class FailingProvider:
    name = "failed"

    def fetch(self) -> ProviderResult:
        raise RuntimeError("HTTP 403")


class GoodProvider:
    name = "backup"

    def fetch(self) -> ProviderResult:
        return ProviderResult(records(), "backup", 7_000)


def test_pipeline_uses_backup_and_writes_valid_snapshot(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    result = update_universe(
        store,
        providers=[FailingProvider(), GoodProvider()],
        now=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert result.source == "backup"
    assert not result.used_cache
    assert len(store.load()) == 200
    status = store.load_status()
    assert status["degraded"] is True
    assert status["provider_errors"][0].startswith("failed: RuntimeError")


def test_pipeline_uses_last_good_cache_when_all_providers_fail(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    update_universe(store, providers=[GoodProvider()])
    result = update_universe(store, providers=[FailingProvider()])
    assert result.used_cache
    assert result.source == "last_good_cache"
    assert len(result.frame) == 200
    assert store.load_status()["degraded"] is True
