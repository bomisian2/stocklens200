from __future__ import annotations

from typing import Iterable


ALPHA_VANTAGE_FREE_DAILY_LIMIT = 25


def build_alpha_vantage_call_plan(
    market_tickers: Iterable[str],
    estimate_tickers: Iterable[str],
    *,
    estimates_entitlement_validated: bool = False,
) -> dict:
    market = tuple(dict.fromkeys(value.upper().strip() for value in market_tickers if value.strip()))
    estimates = tuple(dict.fromkeys(value.upper().strip() for value in estimate_tickers if value.strip()))
    requested = len(market) + len(estimates)
    return {
        "provider": "Alpha Vantage",
        "published_free_daily_limit": ALPHA_VANTAGE_FREE_DAILY_LIMIT,
        "market": {
            "tickers": list(market),
            "calls": len(market),
            "cadence": "after each completed US session",
            "enabled": True,
        },
        "estimates": {
            "tickers": list(estimates),
            "calls": len(estimates),
            "cadence": "weekly after entitlement validation",
            "enabled": estimates_entitlement_validated,
            "reason": None if estimates_entitlement_validated else "Ticker-level free entitlement and local-cache behavior are not live-validated",
        },
        "total_calls_if_all_enabled": requested,
        "reserve_calls": ALPHA_VANTAGE_FREE_DAILY_LIMIT - requested,
        "within_published_limit": requested <= ALPHA_VANTAGE_FREE_DAILY_LIMIT,
        "automatic_retries": 0,
        "requires_explicit_runtime_budget": True,
    }
