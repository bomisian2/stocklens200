from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from .market_calendar import FreshnessAssessment, assess_market_times, latest_completed_session
from .providers import (
    NasdaqUniverseProvider,
    ProviderError,
    ProviderResult,
    UniverseProvider,
    YahooUniverseProvider,
    enrich_metadata,
    rank_companies,
)
from .quality import CrossValidationResult, cross_validate_universes
from .storage import SnapshotError, SnapshotStore


@dataclass(frozen=True)
class UpdateResult:
    frame: pd.DataFrame
    source: str
    used_cache: bool
    state: str
    errors: tuple[str, ...]


def _safe_error(provider: str, exc: Exception) -> str:
    # Do not persist URLs, response bodies, environment values, or secrets in Actions logs.
    detail = str(exc).replace("\n", " ")[:180] if isinstance(exc, ProviderError) else "provider call failed"
    return f"{provider}: {type(exc).__name__}: {detail}"


def _provider_quality_error(provider: str, reason: str) -> str:
    return f"{provider}: data quality rejected: {reason[:180]}"


def _select_reference(
    primary_source: str, ranked_results: dict[str, list]
) -> tuple[str, list] | None:
    for source, rows in ranked_results.items():
        if source != primary_source:
            return source, rows
    return None


def _publish_status(
    *,
    timestamp: str,
    result: ProviderResult,
    ranked: list,
    freshness: FreshnessAssessment,
    reference_source: str,
    reference_freshness: FreshnessAssessment,
    cross_validation: CrossValidationResult,
    quality: dict[str, float | int],
    errors: list[str],
) -> dict:
    collected = sorted(record.collected_at_utc for record in ranked)
    state = "degraded" if errors else "healthy"
    return {
        "schema_version": 2,
        "generated_at_utc": timestamp,
        "data_generated_at_utc": timestamp,
        "market_session_date": freshness.expected_session_date,
        # Compatibility field: median is robust to a single late/outlier quote.
        "market_data_as_of_utc": freshness.market_time_median_utc,
        "market_time_min_utc": freshness.market_time_min_utc,
        "market_time_max_utc": freshness.market_time_max_utc,
        "collected_at_min_utc": collected[0],
        "collected_at_max_utc": collected[-1],
        "source": result.source,
        "data_source": result.source,
        "verification_source": reference_source,
        "verification_source_freshness_state": reference_freshness.state,
        "candidate_count": result.candidate_count,
        "company_count": 200,
        "used_cache": False,
        "data_state": state,
        "freshness_state": freshness.state,
        "degraded": state == "degraded",
        "eligible_for_signals": state == "healthy" and freshness.can_publish,
        "provider_errors": errors,
        "freshness": freshness.to_dict(),
        "cross_validation": cross_validation.to_dict(),
        "quality": quality,
        "validation": {
            "exactly_200_companies": True,
            "unique_tickers": True,
            "unique_issuers": True,
            "positive_market_caps": True,
            "market_caps_descending": True,
            "market_times_verified": freshness.can_publish,
            "independent_source_passed": cross_validation.passed,
        },
    }


def _cache_state(previous: dict, frame: pd.DataFrame, now: datetime) -> tuple[str, str | None]:
    expected = latest_completed_session(now).session_date
    previous_session = previous.get("market_session_date")
    if not previous_session:
        parsed = pd.to_datetime(
            frame["market_time_utc"], errors="coerce", utc=True, format="mixed"
        ).dropna()
        dates = sorted({value.date().isoformat() for value in parsed})
        previous_session = dates[0] if len(dates) == 1 else None
    if not previous_session:
        return "unverified", None
    if previous_session < expected:
        return "stale", previous_session
    if previous_session > expected:
        return "unverified", previous_session
    return "degraded", previous_session


def update_universe(
    store: SnapshotStore,
    providers: Iterable[UniverseProvider] | None = None,
    now: datetime | None = None,
) -> UpdateResult:
    now = now or datetime.now(timezone.utc)
    timestamp = now.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    # Yahoo supplies per-security market timestamps. Nasdaq is an independent
    # membership/market-cap verifier and metadata source, but cannot publish by
    # itself while its free screener's `asOf` field is null.
    provider_chain = list(providers or [YahooUniverseProvider(), NasdaqUniverseProvider()])
    errors: list[str] = []
    raw_results: dict[str, ProviderResult] = {}
    ranked_results: dict[str, list] = {}
    freshness_results: dict[str, FreshnessAssessment] = {}

    for provider in provider_chain:
        try:
            result = provider.fetch()
            ranked = rank_companies(result.records, limit=200)
            if len(ranked) != 200:
                raise ProviderError(f"only {len(ranked)} distinct ranked companies were available")
            raw_results[result.source] = result
            ranked_results[result.source] = ranked
            freshness_results[result.source] = assess_market_times(
                (record.market_time_utc for record in ranked), now
            )
        except Exception as exc:
            errors.append(_safe_error(provider.name, exc))

    for provider in provider_chain:
        source = provider.name
        result = raw_results.get(source)
        ranked = ranked_results.get(source)
        freshness = freshness_results.get(source)
        if result is None or ranked is None or freshness is None:
            continue
        if not freshness.can_publish:
            errors.append(_provider_quality_error(source, freshness.reason))
            continue

        reference = _select_reference(source, ranked_results)
        if reference is None:
            errors.append(_provider_quality_error(source, "independent reference source unavailable"))
            continue
        reference_source, reference_rows = reference
        reference_freshness = freshness_results[reference_source]
        cross_validation = cross_validate_universes(ranked, reference_rows)
        if not cross_validation.passed:
            errors.append(_provider_quality_error(source, cross_validation.reason))
            continue

        enriched = enrich_metadata(ranked, reference_rows)
        frame = store.build_frame(enriched)
        try:
            store.validate(frame)
        except SnapshotError as exc:
            errors.append(_provider_quality_error(source, str(exc)))
            continue
        quality = store.quality_metrics(frame)
        if quality["sector_coverage"] < 0.80 or quality["country_coverage"] < 0.80:
            errors.append(_provider_quality_error(source, "sector/country coverage is below 80%"))
            continue

        status = _publish_status(
            timestamp=timestamp,
            result=result,
            ranked=enriched,
            freshness=freshness,
            reference_source=reference_source,
            reference_freshness=reference_freshness,
            cross_validation=cross_validation,
            quality=quality,
            errors=errors,
        )
        saved = store.save(enriched, status)
        return UpdateResult(
            frame=saved,
            source=result.source,
            used_cache=False,
            state=status["data_state"],
            errors=tuple(errors),
        )

    try:
        frame = store.load()
        previous = store.load_status()
    except SnapshotError as exc:
        detail = "; ".join(errors) if errors else "no provider result"
        raise RuntimeError(
            f"all providers failed validation and no valid last-good snapshot exists ({detail})"
        ) from exc

    state, previous_session = _cache_state(previous, frame, now)
    previous_source = previous.get("data_source") or previous.get("source")
    if previous_source == "last_good_cache":
        previous_source = "unknown"
    status = {
        **previous,
        "schema_version": max(2, int(previous.get("schema_version") or 1)),
        "generated_at_utc": timestamp,
        "last_refresh_attempt_utc": timestamp,
        "source": "last_good_cache",
        "data_source": previous_source,
        "market_session_date": previous_session,
        "company_count": 200,
        "used_cache": True,
        "data_state": state,
        "freshness_state": state if state in {"stale", "unverified"} else "verified",
        "degraded": True,
        "eligible_for_signals": False,
        "provider_errors": errors,
    }
    store.save_status(status)
    return UpdateResult(
        frame=frame,
        source="last_good_cache",
        used_cache=True,
        state=state,
        errors=tuple(errors),
    )
