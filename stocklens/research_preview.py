from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ResearchPreviewError(RuntimeError):
    """Raised when research-only data could be confused with operating output."""


FORBIDDEN_OUTPUT_KEYS = {
    "buy_signal",
    "sell_signal",
    "recommendation",
    "target_price",
    "buy_zone",
    "upside",
    "undervalued",
    "overvalued",
}


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        for child in value.values():
            keys.update(_walk_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for child in value:
            keys.update(_walk_keys(child))
        return keys
    return set()


def validate_research_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the hard boundary between a research preview and production data."""

    if payload.get("schema_version") != 1:
        raise ResearchPreviewError("research preview schema version 1 is required")
    if payload.get("dataset_kind") != "isolated_research_preview":
        raise ResearchPreviewError("dataset must be explicitly marked as isolated_research_preview")
    if payload.get("fixture") is not False:
        raise ResearchPreviewError("fixture data cannot be displayed as an actual research result")
    if payload.get("operational_use") is not False:
        raise ResearchPreviewError("research preview cannot be eligible for operational use")
    for field in ("eligible_for_signals", "eligible_for_ranking", "eligible_for_alerts"):
        if payload.get(field) is not False:
            raise ResearchPreviewError(f"{field} must be false")
    if payload.get("formal_fair_value") is not None:
        raise ResearchPreviewError("formal fair value must remain null in a B-grade preview")
    if payload.get("official_midpoint") is not None:
        raise ResearchPreviewError("B-grade preview cannot select an official midpoint")
    if payload.get("judgement") != "hold":
        raise ResearchPreviewError("B-grade preview judgement must remain hold")
    if payload.get("price_comparison_enabled") is not False:
        raise ResearchPreviewError("automatic current-price comparison must be disabled")

    keys = _walk_keys(payload)
    forbidden = sorted(keys & FORBIDDEN_OUTPUT_KEYS)
    if forbidden:
        raise ResearchPreviewError(f"forbidden operating or recommendation keys: {forbidden}")

    value_range = payload.get("research_per_share_range") or {}
    low = value_range.get("low")
    high = value_range.get("high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        raise ResearchPreviewError("research per-share range must be numeric")
    if low <= 0 or high <= low:
        raise ResearchPreviewError("research per-share range must satisfy 0 < low < high")
    if len(payload.get("top_uncertainties") or []) != 3:
        raise ResearchPreviewError("exactly three top uncertainties are required")
    if not payload.get("formal_blockers"):
        raise ResearchPreviewError("formal valuation blockers must be displayed")

    source = payload.get("official_source") or {}
    if not source.get("url") or not source.get("accession") or not source.get("accepted_at_utc"):
        raise ResearchPreviewError("official source URL, accession, and acceptance time are required")
    if source.get("raw_file_hash_verified") is not False:
        raise ResearchPreviewError("current preview must preserve the unverified raw-file hash state")
    return payload


def load_research_preview(path: Path) -> dict[str, Any]:
    if "data" in {part.lower() for part in path.parts}:
        raise ResearchPreviewError("research preview must not be loaded from the production data directory")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchPreviewError("research preview file is unavailable or invalid") from exc
    if not isinstance(payload, dict):
        raise ResearchPreviewError("research preview root must be an object")
    return validate_research_preview(payload)
