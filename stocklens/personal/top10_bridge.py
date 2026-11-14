"""Read-only bridge for approved Nasdaq Top10 local research snapshots.

The source files belong to another local project.  This module never writes to
them, never calls their upstream providers, and never promotes their values to
an operating valuation or trading signal.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any


APPROVED_MACRO_SHA256 = "662310097AE7C2C5F56B7A563E65B3A5C3FDB84D772FFEA59F5093F59AA75FAE"
APPROVED_CONSENSUS_SHA256 = "BCFBF53FCDB7891166A714C490C48EA4B91D2B27B935768A137A95B3C31AD478"
SUPPORTED_FORWARD_TICKERS = frozenset({"AVGO", "NVDA"})
REQUIRED_PROXY_INDICATORS = frozenset({"tenYear", "oil", "vix", "qqq", "rsp", "hyg"})


class Top10BridgeError(ValueError):
    """Raised when a local cross-project snapshot fails its approved contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _load_snapshot(path: str | Path, *, expected_sha256: str | None) -> tuple[dict[str, Any], str]:
    source_path = Path(path)
    if not source_path.is_file():
        raise Top10BridgeError("local snapshot path does not exist")
    raw = source_path.read_bytes()
    source_hash = _sha256(raw)
    if expected_sha256 and source_hash != expected_sha256.upper():
        raise Top10BridgeError("local snapshot SHA-256 does not match the approved snapshot")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Top10BridgeError("local snapshot is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise Top10BridgeError("local snapshot root must be an object")
    return payload, source_hash


def _iso_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise Top10BridgeError(f"{label} must be an ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Top10BridgeError(f"{label} must be an ISO timestamp") from exc
    return value


def _iso_date(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise Top10BridgeError(f"{label} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise Top10BridgeError(f"{label} must be an ISO date") from exc
    return value


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise Top10BridgeError(f"{label} must be a finite number")
    number = float(value)
    if positive and number <= 0:
        raise Top10BridgeError(f"{label} must be positive")
    return number


def _proxy_direction(indicator_id: str, row: dict[str, Any]) -> str:
    above20 = bool(row["above20"])
    above50 = bool(row["above50"])
    change20 = float(row["change20"])
    if indicator_id == "tenYear":
        return "10년물이 20·50일선 위: 성장주 할인율 역풍 후보"
    if indicator_id == "vix":
        return "VIX가 20·50일선 아래: 단기 변동성 압력 완화 후보"
    if indicator_id == "qqq":
        return "QQQ가 20·50일선 위: 성장주 위험선호 양호 후보"
    if indicator_id == "rsp":
        return (
            "RSP가 20일선 위·50일선 아래: 시장 폭 단기 개선, 중기 확산은 미확인"
            if above20 and not above50
            else "RSP 추세: 시장 폭 프록시이며 공식 경기지표가 아님"
        )
    if indicator_id == "hyg":
        return "HYG가 20·50일선 아래: 신용 위험선호 약화 후보"
    if indicator_id == "oil":
        if above20 and above50 and change20 < 0:
            return "WTI는 20·50일선 위지만 20일 변화는 하락: 비용·물가 방향 혼재"
        return "WTI 추세: 업종 비용·인플레이션 프록시이며 공식 물가지표가 아님"
    raise Top10BridgeError(f"unsupported proxy indicator: {indicator_id}")


def load_market_environment_proxy(
    path: str | Path,
    *,
    expected_sha256: str | None = APPROVED_MACRO_SHA256,
) -> dict[str, Any]:
    """Load the approved cross-project market proxy without changing PER rules."""

    payload, source_hash = _load_snapshot(path, expected_sha256=expected_sha256)
    generated_at = _iso_timestamp(payload.get("generatedAt"), "generatedAt")
    indicators = payload.get("indicators")
    if not isinstance(indicators, dict) or not REQUIRED_PROXY_INDICATORS.issubset(indicators):
        raise Top10BridgeError("macro snapshot is missing required proxy indicators")

    normalized: dict[str, dict[str, Any]] = {}
    rule_trace: list[str] = []
    for indicator_id in sorted(REQUIRED_PROXY_INDICATORS):
        raw = indicators.get(indicator_id)
        if not isinstance(raw, dict):
            raise Top10BridgeError(f"{indicator_id} must be an object")
        as_of = _iso_date(raw.get("asOf"), f"{indicator_id}.asOf")
        for key in ("value", "change5", "change20", "ma20", "ma50"):
            _finite_number(raw.get(key), f"{indicator_id}.{key}")
        if not isinstance(raw.get("above20"), bool) or not isinstance(raw.get("above50"), bool):
            raise Top10BridgeError(f"{indicator_id} moving-average flags must be booleans")
        if not isinstance(raw.get("unit"), str) or not isinstance(raw.get("source"), str):
            raise Top10BridgeError(f"{indicator_id} unit/source must be strings")
        normalized[indicator_id] = {
            "source": raw["source"],
            "source_hash": source_hash,
            "as_of": as_of,
            "available_at": generated_at,
            "unit": raw["unit"],
            "calculation_basis": "cross-project confirmed daily proxy and 20/50-day trend fields",
            "status": "cross_project_market_proxy",
            "operational_eligible": False,
            "label": str(raw.get("label") or indicator_id),
            "value": float(raw["value"]),
            "change5": float(raw["change5"]),
            "change20": float(raw["change20"]),
            "ma20": float(raw["ma20"]),
            "ma50": float(raw["ma50"]),
            "above20": raw["above20"],
            "above50": raw["above50"],
        }
        rule_trace.append(_proxy_direction(indicator_id, raw))

    return {
        "source": "Nasdaq Top10 local macro snapshot · cross-project market proxy",
        "source_hash": source_hash,
        "as_of": max(row["as_of"] for row in normalized.values()),
        "available_at": generated_at,
        "unit": "mixed market proxy units",
        "calculation_basis": str(payload.get("methodology") or ""),
        "status": "cross_project_market_proxy",
        "operational_eligible": False,
        "state": str(payload.get("state") or "unavailable"),
        "score": _finite_number(payload.get("score"), "score"),
        "interpretation": str(payload.get("interpretation") or ""),
        "indicators": normalized,
        "rule_trace": rule_trace,
        "missing_official_inputs": [
            "official policy rate / Treasury vintage",
            "official CPI/PCE inflation release",
            "official labor and credit-spread release",
        ],
        "per_band_policy": "no automatic PER-band change",
    }


def load_forward_consensus_snapshot(
    path: str | Path,
    *,
    expected_sha256: str | None = APPROVED_CONSENSUS_SHA256,
) -> dict[str, Any]:
    """Load AVGO/NVDA street consensus as an unverified research input."""

    payload, source_hash = _load_snapshot(path, expected_sha256=expected_sha256)
    generated_at = _iso_timestamp(payload.get("generatedAt"), "generatedAt")
    records = payload.get("records")
    if not isinstance(records, dict):
        raise Top10BridgeError("consensus records must be an object")

    normalized: dict[str, dict[str, Any]] = {}
    for ticker in SUPPORTED_FORWARD_TICKERS:
        raw = records.get(ticker)
        if not isinstance(raw, dict) or raw.get("ticker") != ticker:
            raise Top10BridgeError(f"consensus record is missing or mismatched for {ticker}")
        collected_at = _iso_timestamp(raw.get("collectedAt"), f"{ticker}.collectedAt")
        low = _finite_number(raw.get("lowEps"), f"{ticker}.lowEps", positive=True)
        base = _finite_number(raw.get("consensusEps"), f"{ticker}.consensusEps", positive=True)
        high = _finite_number(raw.get("highEps"), f"{ticker}.highEps", positive=True)
        if not low <= base <= high:
            raise Top10BridgeError(f"{ticker} EPS range must satisfy low <= consensus <= high")
        analyst_count = int(_finite_number(raw.get("analystCount"), f"{ticker}.analystCount", positive=True))
        up = int(_finite_number(raw.get("revisionsUp4w"), f"{ticker}.revisionsUp4w"))
        down = int(_finite_number(raw.get("revisionsDown4w"), f"{ticker}.revisionsDown4w"))
        if up < 0 or down < 0:
            raise Top10BridgeError(f"{ticker} revisions cannot be negative")
        source_url = raw.get("sourceUrl")
        if not isinstance(source_url, str) or not source_url.startswith("https://api.nasdaq.com/api/analyst/"):
            raise Top10BridgeError(f"{ticker}.sourceUrl is not an approved Nasdaq analyst endpoint")
        normalized[ticker] = {
            "source": f"{payload.get('source')} · {source_url}",
            "source_url": source_url,
            "source_hash": source_hash,
            "as_of": str(raw.get("fiscalEnd") or ""),
            "available_at": collected_at,
            "unit": "USD EPS per share",
            "calculation_basis": "Nasdaq annual analyst EPS aggregate; GAAP/non-GAAP basis is not verified",
            "basis": "unverified_street_basis",
            "status": "research_only",
            "operational_eligible": False,
            "ticker": ticker,
            "fiscal_end": str(raw.get("fiscalEnd") or ""),
            "eps_low": low,
            "eps_base": base,
            "eps_high": high,
            "analyst_count": analyst_count,
            "revisions_up_4w": up,
            "revisions_down_4w": down,
            "revision_state": str(raw.get("revisionState") or "unknown"),
            "next_fiscal_end": str(raw.get("nextFiscalEnd") or ""),
            "next_consensus_eps": _finite_number(
                raw.get("nextConsensusEps"), f"{ticker}.nextConsensusEps", positive=True
            ),
        }

    return {
        "source": str(payload.get("source") or ""),
        "source_hash": source_hash,
        "generated_at": generated_at,
        "collected_at": generated_at,
        "methodology": str(payload.get("methodology") or ""),
        "basis": "unverified_street_basis",
        "status": "research_only",
        "operational_eligible": False,
        "tickers": normalized,
    }


def build_forward_consensus_candidate(
    consensus_record: dict[str, Any],
    *,
    current_price: float,
    current_price_as_of: str,
    self_history: dict[str, Any],
) -> dict[str, Any]:
    """Combine unverified forward EPS with approved historical P35/P50.

    The output is deliberately separate from the TTM candidate and is never
    eligible for an operating valuation or signal.
    """

    if consensus_record.get("basis") != "unverified_street_basis":
        raise Top10BridgeError("forward EPS basis must remain unverified_street_basis")
    price = _finite_number(current_price, "current_price", positive=True)
    price_as_of = _iso_date(current_price_as_of, "current_price_as_of")
    percentiles = self_history.get("full_history_percentiles")
    if not isinstance(percentiles, dict):
        raise Top10BridgeError("self-history percentiles are unavailable")
    p35 = _finite_number(percentiles.get("P35"), "P35", positive=True)
    p50 = _finite_number(percentiles.get("P50"), "P50", positive=True)
    eps_values = {
        "low": _finite_number(consensus_record.get("eps_low"), "eps_low", positive=True),
        "base": _finite_number(consensus_record.get("eps_base"), "eps_base", positive=True),
        "high": _finite_number(consensus_record.get("eps_high"), "eps_high", positive=True),
    }
    matrix = [
        {
            "eps_case": eps_case,
            "eps": eps,
            "per_percentile": percentile,
            "per": multiple,
            "price_candidate": eps * multiple,
        }
        for eps_case, eps in eps_values.items()
        for percentile, multiple in (("P35", p35), ("P50", p50))
    ]
    forward_per = {case: price / eps for case, eps in eps_values.items()}
    dispersion = (eps_values["high"] - eps_values["low"]) / eps_values["base"]
    analyst_count = int(consensus_record["analyst_count"])
    confidence = "low_research_confidence"
    if analyst_count >= 10 and dispersion <= 0.25:
        confidence = "moderate_input_confidence"
    confidence_explanation = (
        f"애널리스트 {analyst_count}명, EPS 고저 분산 {dispersion:.1%}, "
        f"4주 수정 {consensus_record['revisions_up_4w']}↑/{consensus_record['revisions_down_4w']}↓. "
        "집계의 GAAP/non-GAAP 정의와 시점별 원장은 검증되지 않아 운영 신뢰도가 아니다."
    )
    return {
        "source": consensus_record["source"],
        "source_hash": consensus_record["source_hash"],
        "as_of": consensus_record["fiscal_end"],
        "available_at": consensus_record["available_at"],
        "unit": "USD per share; PER multiple",
        "calculation_basis": (
            "unverified street EPS low/base/high × cleaned reconstructed PIT historical PER P35/P50; "
            "forward PER = v23 snapshot close / each EPS case"
        ),
        "basis": "unverified_street_basis",
        "status": "forward_consensus_candidate",
        "operational_eligible": False,
        "ticker": consensus_record["ticker"],
        "price": price,
        "price_as_of": price_as_of,
        "consensus_collected_at": consensus_record["available_at"],
        "fiscal_end": consensus_record["fiscal_end"],
        "eps": eps_values,
        "forward_per": forward_per,
        "per_percentiles": {"P35": p35, "P50": p50},
        "matrix": matrix,
        "range_low": min(row["price_candidate"] for row in matrix),
        "range_high": max(row["price_candidate"] for row in matrix),
        "analyst_count": analyst_count,
        "dispersion": dispersion,
        "revision_state": consensus_record["revision_state"],
        "revisions_up_4w": consensus_record["revisions_up_4w"],
        "revisions_down_4w": consensus_record["revisions_down_4w"],
        "next_fiscal_end": consensus_record["next_fiscal_end"],
        "next_consensus_eps": consensus_record["next_consensus_eps"],
        "input_confidence": confidence,
        "confidence_explanation": confidence_explanation,
        "date_mismatch": True,
        "limitations": [
            "price date and consensus collection timestamp are different and remain separately displayed",
            "street EPS GAAP/non-GAAP basis and contemporaneous revision history are unverified",
            "historical PER percentiles are reconstructed PIT research evidence, not a target multiple",
            "the matrix does not overwrite the TTM candidate or create a fair value, buy range, or signal",
        ],
    }
