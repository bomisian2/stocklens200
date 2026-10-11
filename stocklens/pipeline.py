from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from .providers import NasdaqUniverseProvider, ProviderError, UniverseProvider, YahooUniverseProvider, rank_companies
from .storage import SnapshotError, SnapshotStore


@dataclass(frozen=True)
class UpdateResult:
    frame: pd.DataFrame
    source: str
    used_cache: bool
    errors: tuple[str, ...]


def _safe_error(provider: str, exc: Exception) -> str:
    # Do not persist URLs, response bodies, environment values, or secrets in Actions logs.
    detail = str(exc).replace("\n", " ")[:180] if isinstance(exc, ProviderError) else "provider call failed"
    return f"{provider}: {type(exc).__name__}: {detail}"


def update_universe(
    store: SnapshotStore,
    providers: Iterable[UniverseProvider] | None = None,
    now: datetime | None = None,
) -> UpdateResult:
    now = now or datetime.now(timezone.utc)
    timestamp = now.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    # Yahoo currently has the more consistent shares-outstanding values. Nasdaq
    # remains independent fallback coverage when Yahoo returns 401/403/429.
    provider_chain = list(providers or [YahooUniverseProvider(), NasdaqUniverseProvider()])
    errors: list[str] = []

    for provider in provider_chain:
        try:
            result = provider.fetch()
            ranked = rank_companies(result.records, limit=200)
            if len(ranked) != 200:
                raise ValueError(f"only {len(ranked)} distinct ranked companies were available")
            status = {
                "schema_version": 1,
                "generated_at_utc": timestamp,
                "data_generated_at_utc": timestamp,
                "market_data_as_of_utc": max(record.as_of for record in ranked),
                "source": result.source,
                "candidate_count": result.candidate_count,
                "company_count": 200,
                "used_cache": False,
                "degraded": bool(errors),
                "provider_errors": errors,
                "validation": {
                    "exactly_200_companies": True,
                    "unique_tickers": True,
                    "unique_issuers": True,
                    "positive_market_caps": True,
                    "market_caps_descending": True,
                },
            }
            frame = store.save(ranked, status)
            return UpdateResult(frame=frame, source=result.source, used_cache=False, errors=tuple(errors))
        except Exception as exc:
            errors.append(_safe_error(provider.name, exc))

    try:
        frame = store.load()
        previous = store.load_status()
    except SnapshotError as exc:
        raise RuntimeError("all providers failed and no valid last-good snapshot exists") from exc

    status = {
        **previous,
        "schema_version": 1,
        "generated_at_utc": timestamp,
        "source": "last_good_cache",
        "company_count": 200,
        "used_cache": True,
        "degraded": True,
        "provider_errors": errors,
    }
    store.save_status(status)
    return UpdateResult(frame=frame, source="last_good_cache", used_cache=True, errors=tuple(errors))
