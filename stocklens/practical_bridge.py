from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


def _finite(value: Any, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _range(raw: Mapping[str, Any], name: str) -> tuple[float, float]:
    low = _finite(raw.get("low"), f"{name}.low")
    high = _finite(raw.get("high"), f"{name}.high")
    if low < 0 or high < low:
        raise ValueError(f"{name} must satisfy 0 <= low <= high")
    return low, high


def calculate_research_bridge_envelope(
    *,
    enterprise_value_range: Mapping[str, Any],
    additive_asset_ranges: Iterable[Mapping[str, Any]],
    claim_ranges: Iterable[Mapping[str, Any]],
    share_range: Mapping[str, Any],
    preferred_policy: str,
) -> dict[str, Any]:
    """Calculate a deliberately wide B-grade research bridge envelope.

    The function does not select a midpoint or probability. Asset and claim
    endpoints must be supplied with their evidence basis and rationale. This is
    a transparency tool, not a formal fair-value model or an operating signal.
    """

    ev_low, ev_high = _range(enterprise_value_range, "enterprise_value_range")
    share_low, share_high = _range(share_range, "share_range")
    if share_low <= 0:
        raise ValueError("share range must be positive")
    if preferred_policy not in {"claim", "if_converted"}:
        raise ValueError("preferred_policy must be claim or if_converted")

    assets: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for raw in additive_asset_ranges:
        item_id = str(raw.get("id", "")).strip()
        if not item_id:
            raise ValueError("asset id is required")
        low, high = _range(raw, item_id)
        if not str(raw.get("basis", "")).strip() or not str(raw.get("rationale", "")).strip():
            raise ValueError(f"{item_id} requires basis and rationale")
        assets.append({**dict(raw), "low": low, "high": high})
    for raw in claim_ranges:
        item_id = str(raw.get("id", "")).strip()
        if not item_id:
            raise ValueError("claim id is required")
        low, high = _range(raw, item_id)
        if not str(raw.get("basis", "")).strip() or not str(raw.get("rationale", "")).strip():
            raise ValueError(f"{item_id} requires basis and rationale")
        claims.append({**dict(raw), "low": low, "high": high})

    asset_low = sum(item["low"] for item in assets)
    asset_high = sum(item["high"] for item in assets)
    claim_low = sum(item["low"] for item in claims)
    claim_high = sum(item["high"] for item in claims)
    equity_low = ev_low + asset_low - claim_high
    equity_high = ev_high + asset_high - claim_low
    if equity_low <= 0:
        raise ValueError("research bridge envelope produced non-positive common equity")

    return {
        "status": "B_research_reference_envelope",
        "enterprise_value_range": {"low": ev_low, "high": ev_high},
        "additive_asset_ranges": assets,
        "claim_ranges": claims,
        "asset_total_range": {"low": asset_low, "high": asset_high},
        "claim_total_range": {"low": claim_low, "high": claim_high},
        "common_equity_range": {"low": equity_low, "high": equity_high},
        "share_range": {
            "low": share_low,
            "high": share_high,
            "basis": share_range.get("basis"),
            "rationale": share_range.get("rationale"),
        },
        "per_share_reference_range": {
            "low": equity_low / share_high,
            "high": equity_high / share_low,
        },
        "preferred_policy": preferred_policy,
        "no_selected_midpoint": True,
        "formal_fair_value": None,
        "operational_use": False,
        "warning": (
            "The endpoints are mechanical bounds from explicit recognition and dilution ranges, "
            "not confidence intervals, price targets, or buy/sell signals."
        ),
    }


def rank_value_drivers(drivers: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rank comparable research sensitivities by absolute per-share span."""

    output: list[dict[str, Any]] = []
    for raw in drivers:
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError("driver name is required")
        low = _finite(raw.get("low_value_usd"), f"{name}.low_value_usd")
        high = _finite(raw.get("high_value_usd"), f"{name}.high_value_usd")
        shares = _finite(raw.get("comparison_shares"), f"{name}.comparison_shares")
        if shares <= 0:
            raise ValueError("comparison shares must be positive")
        span = abs(high - low)
        output.append(
            {
                **dict(raw),
                "value_span_usd": span,
                "mechanical_per_share_span": span / shares,
                "is_price_target": False,
                "operational_use": False,
            }
        )
    output.sort(key=lambda item: item["mechanical_per_share_span"], reverse=True)
    for rank, item in enumerate(output, start=1):
        item["impact_rank"] = rank
    return output
