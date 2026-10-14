"""Deterministic, research-only market-regime read model.

The model consumes only evidence-bearing local observations that were
available by ``analysis_as_of``.  It deliberately has no network client and
does not produce an operating valuation or trading signal.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


REGIME_MODEL_VERSION = "market-regime-read-model-v1"
REGIME_MINIMUM_COVERAGE = 0.60
ALLOWED_STATUSES = {"verified", "proxy", "partial", "stale", "unavailable", "blocked"}
REQUIRED_OBSERVATION_FIELDS = {
    "metric_id",
    "value",
    "unit",
    "source",
    "source_hash",
    "as_of",
    "available_at",
    "frequency",
    "status",
}


class MarketRegimeError(ValueError):
    """Raised when a local regime snapshot violates the evidence contract."""


def _score_low_good(value: float, favorable: float, adverse: float) -> int:
    if value <= favorable:
        return 1
    if value >= adverse:
        return -1
    return 0


def _score_high_good(value: float, adverse: float, favorable: float) -> int:
    if value >= favorable:
        return 1
    if value <= adverse:
        return -1
    return 0


# Each rule is intentionally explicit. Scores are research classifications,
# not forecasts. Weight totals are used to calculate coverage.
METRIC_RULES: dict[str, dict[str, Any]] = {
    "policy_rate_pct": {
        "unit": "percent",
        "max_age_days": 45,
        "axes": {"economy": (1.0, lambda v: _score_low_good(v, 2.5, 4.5))},
        "threshold": "<=2.5 favorable; >=4.5 adverse",
    },
    "treasury_2y_yield_pct": {
        "unit": "percent",
        "max_age_days": 10,
        "axes": {"economy": (0.5, lambda v: _score_low_good(v, 3.0, 5.0))},
        "threshold": "<=3.0 favorable; >=5.0 adverse",
    },
    "treasury_10y_yield_pct": {
        "unit": "percent",
        "max_age_days": 10,
        "axes": {"economy": (0.5, lambda v: _score_low_good(v, 3.5, 5.0))},
        "threshold": "<=3.5 favorable; >=5.0 adverse",
    },
    "treasury_2s10s_spread_pp": {
        "unit": "percentage_point",
        "max_age_days": 10,
        "axes": {"economy": (1.0, lambda v: _score_high_good(v, -0.25, 0.50))},
        "threshold": "<=-0.25 adverse; >=0.50 favorable",
    },
    "unemployment_rate_change_3m_pp": {
        "unit": "percentage_point",
        "max_age_days": 50,
        "axes": {"economy": (1.0, lambda v: _score_low_good(v, 0.0, 0.30))},
        "threshold": "<=0.00 favorable; >=0.30 adverse",
    },
    "employment_growth_3m_ratio": {
        "unit": "ratio",
        "max_age_days": 50,
        "axes": {"economy": (1.0, lambda v: _score_high_good(v, 0.0, 0.004))},
        "threshold": "<=0.0 adverse; >=0.4% favorable",
    },
    "cpi_yoy_trend_3m_pp": {
        "unit": "percentage_point",
        "max_age_days": 50,
        "axes": {"inflation": (1.5, lambda v: _score_low_good(v, -0.20, 0.20))},
        "threshold": "<=-0.20 disinflationary; >=0.20 inflationary",
    },
    "pce_yoy_trend_3m_pp": {
        "unit": "percentage_point",
        "max_age_days": 65,
        "axes": {"inflation": (1.5, lambda v: _score_low_good(v, -0.20, 0.20))},
        "threshold": "<=-0.20 disinflationary; >=0.20 inflationary",
    },
    "dxy_return_63d_ratio": {
        "unit": "ratio",
        "max_age_days": 10,
        "axes": {
            "inflation": (0.5, lambda v: _score_high_good(v, -0.03, 0.03)),
            "risk": (0.5, lambda v: _score_low_good(v, -0.03, 0.05)),
        },
        "threshold": "inflation: stronger USD favorable; risk: >5% adverse",
    },
    "vix_index": {
        "unit": "index_point",
        "max_age_days": 10,
        "axes": {"risk": (1.5, lambda v: _score_low_good(v, 18.0, 25.0))},
        "threshold": "<=18 risk-on; >=25 risk-off",
    },
    "hy_oas_pct": {
        "unit": "percent",
        "max_age_days": 10,
        "axes": {"risk": (1.5, lambda v: _score_low_good(v, 3.5, 5.0))},
        "threshold": "<=3.5 risk-on; >=5.0 risk-off",
    },
    "qqq_trend_63d_ratio": {
        "unit": "ratio",
        "max_age_days": 10,
        "axes": {
            "economy": (0.5, lambda v: _score_high_good(v, -0.05, 0.05)),
            "risk": (1.0, lambda v: _score_high_good(v, -0.05, 0.05)),
        },
        "threshold": "<=-5% adverse; >=5% favorable",
    },
    "qqq_realized_vol_20d_ratio": {
        "unit": "ratio",
        "max_age_days": 10,
        "axes": {"risk": (1.0, lambda v: _score_low_good(v, 0.20, 0.30))},
        "threshold": "<=20% risk-on; >=30% risk-off",
    },
    "qqq_drawdown_observed_ratio": {
        "unit": "ratio",
        "max_age_days": 10,
        "axes": {"risk": (1.0, lambda v: _score_high_good(v, -0.18, -0.08))},
        "threshold": "<=-18% risk-off; >=-8% risk-on",
    },
}

AXIS_LABELS = {
    "economy": {1: "expansion", 0: "neutral_mixed", -1: "slowdown"},
    "inflation": {1: "stable_disinflation", 0: "neutral_mixed", -1: "inflationary"},
    "risk": {1: "risk_on", 0: "neutral", -1: "risk_off"},
}


def _parse_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MarketRegimeError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketRegimeError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise MarketRegimeError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise MarketRegimeError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise MarketRegimeError(f"{label} must be an ISO date") from exc


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise MarketRegimeError(f"{label} must be a SHA-256 hash")
    try:
        int(value, 16)
    except ValueError as exc:
        raise MarketRegimeError(f"{label} must be a SHA-256 hash") from exc
    return value.lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_hash(observations: list[dict[str, Any]]) -> str:
    encoded = json.dumps(observations, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_observation(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MarketRegimeError(f"observations[{index}] must be an object")
    missing = REQUIRED_OBSERVATION_FIELDS - set(raw)
    if missing:
        raise MarketRegimeError(f"observations[{index}] missing fields: {sorted(missing)}")
    metric_id = str(raw["metric_id"])
    if metric_id not in METRIC_RULES:
        raise MarketRegimeError(f"unsupported metric_id: {metric_id}")
    rule = METRIC_RULES[metric_id]
    if raw["unit"] != rule["unit"]:
        raise MarketRegimeError(
            f"{metric_id} unit must be {rule['unit']}, got {raw['unit']}"
        )
    value = raw["value"]
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise MarketRegimeError(f"{metric_id} value must be finite numeric data")
    if not isinstance(raw["source"], str) or not raw["source"].strip():
        raise MarketRegimeError(f"{metric_id} source must be non-empty")
    if not isinstance(raw["frequency"], str) or not raw["frequency"].strip():
        raise MarketRegimeError(f"{metric_id} frequency must be non-empty")
    status = str(raw["status"])
    if status not in ALLOWED_STATUSES:
        raise MarketRegimeError(f"{metric_id} status is unsupported: {status}")
    result = dict(raw)
    result["value"] = float(value)
    result["source_hash"] = _require_hash(raw["source_hash"], f"{metric_id}.source_hash")
    result["as_of"] = _parse_date(raw["as_of"], f"{metric_id}.as_of").isoformat()
    result["available_at"] = _parse_datetime(
        raw["available_at"], f"{metric_id}.available_at"
    ).isoformat()
    result["status"] = status
    return result


def _axis_result(
    axis: str,
    observations: dict[str, dict[str, Any]],
    exclusions: dict[str, str],
) -> dict[str, Any]:
    expected = {
        metric_id: rule
        for metric_id, rule in METRIC_RULES.items()
        if axis in rule["axes"]
    }
    total_weight = sum(rule["axes"][axis][0] for rule in expected.values())
    used_weight = 0.0
    weighted_score = 0.0
    used_inputs: list[str] = []
    missing_inputs: list[str] = []
    trace: list[str] = []
    for metric_id, rule in expected.items():
        weight, scorer = rule["axes"][axis]
        observation = observations.get(metric_id)
        if observation is None:
            missing_inputs.append(metric_id)
            trace.append(f"{metric_id}: excluded ({exclusions.get(metric_id, 'missing')})")
            continue
        score = int(scorer(observation["value"]))
        used_weight += weight
        weighted_score += weight * score
        used_inputs.append(metric_id)
        trace.append(
            f"{metric_id}={observation['value']:g} {observation['unit']} -> {score:+d}; "
            f"weight={weight:g}; rule={rule['threshold']}"
        )
    coverage = used_weight / total_weight if total_weight else 0.0
    score = weighted_score / used_weight if used_weight else 0.0
    sign = 1 if score >= 0.35 else (-1 if score <= -0.35 else 0)
    confidence = "high" if coverage >= 0.80 else ("medium" if coverage >= 0.60 else "low")
    return {
        "regime": AXIS_LABELS[axis][sign],
        "score": score,
        "confidence": confidence,
        "coverage": coverage,
        "used_inputs": used_inputs,
        "missing_inputs": missing_inputs,
        "rule_trace": trace,
    }


def build_market_regime(
    raw_observations: list[dict[str, Any]],
    *,
    analysis_as_of: str,
) -> dict[str, Any]:
    """Classify economy, inflation, and risk using local point-in-time data."""

    analysis_time = _parse_datetime(analysis_as_of, "analysis_as_of")
    accepted: dict[str, dict[str, Any]] = {}
    exclusions: dict[str, str] = {}
    normalized = [_normalized_observation(row, index) for index, row in enumerate(raw_observations)]
    for row in normalized:
        metric_id = row["metric_id"]
        if metric_id in accepted:
            raise MarketRegimeError(f"duplicate metric_id: {metric_id}")
        available_at = _parse_datetime(row["available_at"], f"{metric_id}.available_at")
        as_of = _parse_date(row["as_of"], f"{metric_id}.as_of")
        age_days = (analysis_time.date() - as_of).days
        if available_at > analysis_time:
            exclusions[metric_id] = "look_ahead_blocked"
        elif row["status"] not in {"verified", "proxy", "partial"}:
            exclusions[metric_id] = f"status_{row['status']}"
        elif age_days < 0:
            exclusions[metric_id] = "future_as_of_blocked"
        elif age_days > METRIC_RULES[metric_id]["max_age_days"]:
            exclusions[metric_id] = f"stale_{age_days}d"
        else:
            accepted[metric_id] = row

    axes = {
        axis: _axis_result(axis, accepted, exclusions)
        for axis in ("economy", "inflation", "risk")
    }
    overall_coverage = sum(axis["coverage"] for axis in axes.values()) / len(axes)
    source_hash = _snapshot_hash(normalized)
    latest_as_of = max((row["as_of"] for row in accepted.values()), default=None)
    latest_available_at = max((row["available_at"] for row in accepted.values()), default=None)
    statuses = {row["status"] for row in accepted.values()}
    if overall_coverage < REGIME_MINIMUM_COVERAGE:
        status = "proxy_low_confidence"
    elif "proxy" in statuses or "partial" in statuses:
        status = "research_proxy"
    else:
        status = "research_verified_inputs"
    return {
        "model_version": REGIME_MODEL_VERSION,
        "source": "local point-in-time macro/market observation snapshot",
        "source_hash": source_hash,
        "as_of": latest_as_of,
        "available_at": latest_available_at,
        "analysis_as_of": analysis_time.isoformat(),
        "unit": "normalized regime score [-1, 1]",
        "calculation_basis": (
            "deterministic weighted thresholds; unavailable, stale, invalid-status, and future-available "
            "observations are excluded rather than imputed"
        ),
        "status": status,
        "operational_eligible": False,
        "minimum_coverage": REGIME_MINIMUM_COVERAGE,
        "overall_coverage": overall_coverage,
        "economy_regime": axes["economy"],
        "inflation_regime": axes["inflation"],
        "risk_regime": axes["risk"],
        "accepted_observations": list(accepted.values()),
        "excluded_observations": exclusions,
    }


def load_market_regime_snapshot(path: str | Path, *, analysis_as_of: str) -> dict[str, Any]:
    snapshot_path = Path(path)
    with snapshot_path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        raise MarketRegimeError("snapshot observations must be an array")
    return build_market_regime(observations, analysis_as_of=analysis_as_of)


def build_qqq_proxy_observations(
    csv_path: str | Path,
    manifest_path: str | Path,
) -> list[dict[str, Any]]:
    """Derive three transparent risk proxies from the local QQQ cache."""

    price_path = Path(csv_path)
    metadata_path = Path(manifest_path)
    if not price_path.is_file() or not metadata_path.is_file():
        return []
    with metadata_path.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    file_hash = _sha256_file(price_path)
    if manifest.get("sha256") != file_hash:
        raise MarketRegimeError("QQQ cache SHA-256 does not match its manifest")
    with price_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    closes = [float(row["close"]) for row in rows]
    if len(closes) < 64 or any(not math.isfinite(value) or value <= 0 for value in closes):
        return []
    market_date = str(manifest.get("market_date") or rows[-1]["date"])
    available_at = str(manifest.get("collected_at_utc") or "")
    returns_20 = [math.log(closes[index] / closes[index - 1]) for index in range(len(closes) - 19, len(closes))]
    realized_vol = statistics.stdev(returns_20) * math.sqrt(252) if len(returns_20) >= 2 else None
    values = {
        "qqq_trend_63d_ratio": closes[-1] / closes[-64] - 1,
        "qqq_realized_vol_20d_ratio": realized_vol,
        "qqq_drawdown_observed_ratio": closes[-1] / max(closes) - 1,
    }
    basis = {
        "qqq_trend_63d_ratio": "latest raw close / raw close 63 trading sessions earlier - 1",
        "qqq_realized_vol_20d_ratio": "sample stdev of latest 19 log returns × sqrt(252)",
        "qqq_drawdown_observed_ratio": "latest raw close / maximum close in available local cache - 1",
    }
    return [
        {
            "metric_id": metric_id,
            "value": value,
            "unit": "ratio",
            "source": "local Alpha Vantage QQQ TIME_SERIES_DAILY raw-unadjusted cache",
            "source_hash": file_hash,
            "as_of": market_date,
            "available_at": available_at,
            "frequency": "daily",
            "status": "proxy",
            "calculation_basis": basis[metric_id],
        }
        for metric_id, value in values.items()
        if value is not None
    ]


def build_regime_adjusted_candidate(
    self_history: dict[str, Any],
    regime: dict[str, Any],
) -> dict[str, Any]:
    """Select a research PER band without mutating the base self-history model."""

    coverage = float(regime.get("overall_coverage") or 0.0)
    economy = regime.get("economy_regime", {}).get("regime")
    inflation = regime.get("inflation_regime", {}).get("regime")
    risk = regime.get("risk_regime", {}).get("regime")
    if coverage < REGIME_MINIMUM_COVERAGE:
        low_p, high_p = 35, 50
        posture = "neutral_low_coverage"
        status = "proxy_low_confidence"
        reason = (
            f"coverage {coverage:.1%} < {REGIME_MINIMUM_COVERAGE:.0%}; "
            "regime change is not authorized and P35-P50 is retained"
        )
    elif economy == "expansion" and inflation == "stable_disinflation" and risk == "risk_on":
        low_p, high_p = 50, 70
        posture = "favorable"
        status = "research_only"
        reason = "expansion + stable/disinflation + risk-on -> P50-P70"
    elif risk == "risk_off" and economy == "slowdown":
        low_p, high_p = 25, 35
        posture = "adverse"
        status = "research_only"
        reason = "slowdown + risk-off -> P25-P35"
    else:
        low_p, high_p = 35, 50
        posture = "neutral_mixed"
        status = "research_only"
        reason = "mixed/neutral regime -> P35-P50"

    percentiles = self_history.get("full_history_percentiles") or {}
    eps = self_history.get("current_ttm_eps")
    low_multiple = percentiles.get(f"P{low_p}")
    high_multiple = percentiles.get(f"P{high_p}")
    eligible = bool(self_history.get("sample_gate_passed")) and all(
        isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0
        for value in (eps, low_multiple, high_multiple)
    )
    low = float(eps) * float(low_multiple) if eligible else None
    high = float(eps) * float(high_multiple) if eligible else None
    if not eligible:
        status = "unavailable"
    lineage = {
        "self_history_source_hash": self_history.get("source_hash"),
        "regime_source_hash": regime.get("source_hash"),
        "model_version": REGIME_MODEL_VERSION,
        "current_ttm_eps": eps,
        "basis_percentiles": [f"P{low_p}", f"P{high_p}"],
        "selected_multiples": [low_multiple, high_multiple],
    }
    source_hash = hashlib.sha256(
        json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "low": low,
        "high": high,
        "source": "v23 self-history PER distribution + local market-regime read model",
        "source_hash": source_hash,
        "as_of": max(
            str(self_history.get("as_of") or ""),
            str(regime.get("as_of") or ""),
        ) or None,
        "available_at": max(
            str(self_history.get("available_at") or ""),
            str(regime.get("available_at") or ""),
        ) or None,
        "unit": "USD per share",
        "calculation_basis": (
            f"current v23 TTM diluted EPS × cleaned historical PER P{low_p}-P{high_p}; "
            "the regime model selects only the disclosed percentile band"
        ),
        "status": status,
        "operational_eligible": False,
        "posture": posture,
        "basis_percentiles": [f"P{low_p}", f"P{high_p}"],
        "coverage": coverage,
        "rule_trace": [reason],
        "lineage": lineage,
    }
