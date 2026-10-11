from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from stocklens.pipeline import update_universe
from stocklens.providers import ProviderResult, UniverseRecord
from stocklens.storage import SnapshotStore


MARKET_TIME = "2026-01-02T21:00:00+00:00"
COLLECTED_AT = "2026-01-03T12:00:00+00:00"
RUN_TIME = datetime(2026, 1, 3, 12, tzinfo=timezone.utc)


def records(source: str = "primary", *, market_time: str | None = MARKET_TIME) -> list[UniverseRecord]:
    return [
        UniverseRecord(
            ticker=f"T{i:03d}",
            name=f"Company {i:03d} Common Stock",
            sector="Technology",
            industry="Software",
            country="United States",
            market_cap=float(1_000_000 - i),
            price=100.0,
            change_percent=0.0,
            volume=float(i),
            market_time_utc=market_time,
            collected_at_utc=COLLECTED_AT,
            source=source,
            metadata_source=source,
            issuer_key=f"company-{i:03d}",
        )
        for i in range(205)
    ]


class FailingProvider:
    name = "failed"

    def fetch(self) -> ProviderResult:
        raise RuntimeError("HTTP 403")


class Provider:
    def __init__(self, name: str, rows: list[UniverseRecord]) -> None:
        self.name = name
        self.rows = rows

    def fetch(self) -> ProviderResult:
        return ProviderResult(self.rows, self.name, 7_000)


def providers(*, primary_rows: list[UniverseRecord] | None = None):
    return [
        Provider("primary", primary_rows or records()),
        Provider("reference", records("reference", market_time=None)),
    ]


def test_pipeline_uses_verified_primary_and_independent_reference(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    result = update_universe(store, providers=providers(), now=RUN_TIME)
    assert result.source == "primary"
    assert result.state == "healthy"
    assert not result.used_cache
    assert len(store.load()) == 200
    status = store.load_status()
    assert status["freshness_state"] == "verified"
    assert status["market_session_date"] == "2026-01-02"
    assert status["cross_validation"]["passed"] is True
    assert status["verification_source_freshness_state"] == "unverified"
    assert status["eligible_for_signals"] is True


def test_pipeline_marks_backup_path_degraded_after_primary_failure(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    result = update_universe(
        store,
        providers=[FailingProvider(), *providers()],
        now=RUN_TIME,
    )
    assert result.source == "primary"
    assert result.state == "degraded"
    assert result.errors[0].startswith("failed: RuntimeError")
    assert store.load_status()["eligible_for_signals"] is False


def test_pipeline_preserves_snapshot_when_market_time_is_missing(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    update_universe(store, providers=providers(), now=RUN_TIME)
    before = store.snapshot_path.read_bytes()
    partial = records()
    partial[0] = replace(partial[0], market_time_utc=None)

    result = update_universe(store, providers=providers(primary_rows=partial), now=RUN_TIME)

    assert result.used_cache
    assert result.state == "degraded"
    assert store.snapshot_path.read_bytes() == before
    assert store.load_status()["eligible_for_signals"] is False


def test_pipeline_marks_old_cache_stale_after_new_session(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    update_universe(store, providers=providers(), now=RUN_TIME)
    before = store.snapshot_path.read_bytes()

    result = update_universe(
        store,
        providers=[FailingProvider()],
        now=datetime(2026, 1, 6, 12, tzinfo=timezone.utc),
    )

    assert result.used_cache
    assert result.state == "stale"
    assert store.snapshot_path.read_bytes() == before
    assert store.load_status()["freshness_state"] == "stale"


def test_pipeline_uses_last_good_cache_when_all_providers_fail(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    update_universe(store, providers=providers(), now=RUN_TIME)
    result = update_universe(store, providers=[FailingProvider()], now=RUN_TIME)
    assert result.used_cache
    assert result.source == "last_good_cache"
    assert len(result.frame) == 200
    assert store.load_status()["degraded"] is True
