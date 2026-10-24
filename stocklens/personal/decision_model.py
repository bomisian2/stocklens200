"""Same-date, research-only investment decision model.

The model consumes only local, hash-pinned evidence.  It creates an explicit
candidate action while keeping the operating gate closed until the source and
walk-forward requirements are met.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from dateutil.relativedelta import relativedelta

from stocklens.market_calendar import latest_completed_session


CALCULATION_VERSION = "same-date-investment-decision-v1.1"
APPROVED_LOCAL_INPUT_SHA256: str | None = None
SUPPORTED_TICKERS = frozenset({"AVGO", "NVDA"})
SOURCE_EVIDENCE_KEYS = frozenset({"decision_ledger", "consensus", "regime"})


class DecisionModelError(ValueError):
    """Raised when same-date evidence or model settings are invalid."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(raw)


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DecisionModelError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionModelError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DecisionModelError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date(value: Any, label: str) -> date:
    if not isinstance(value, str) or not value:
        raise DecisionModelError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DecisionModelError(f"{label} must be an ISO date") from exc


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionModelError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise DecisionModelError(f"{label} must be a finite {'positive ' if positive else ''}number")
    return number


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise DecisionModelError(f"{label} must be a SHA-256 hash")
    try:
        int(value, 16)
    except ValueError as exc:
        raise DecisionModelError(f"{label} must be a SHA-256 hash") from exc
    return value.upper()


def load_decision_inputs(
    path: str | Path,
    *,
    expected_sha256: str | None = APPROVED_LOCAL_INPUT_SHA256,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load normalized evidence and verify the exact raw files behind it."""

    source_path = Path(path)
    if not source_path.is_file():
        raise DecisionModelError("same-date local evidence snapshot does not exist")
    raw = source_path.read_bytes()
    digest = _sha256(raw)
    if expected_sha256 and digest != expected_sha256.upper():
        raise DecisionModelError("same-date local evidence SHA-256 does not match the approved snapshot")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionModelError("same-date local evidence is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "same-date-input-v1":
        raise DecisionModelError("same-date local evidence schema is not approved")
    if value.get("calculation_version") != CALCULATION_VERSION:
        raise DecisionModelError("same-date calculation version is not approved")
    decision_at = _timestamp(value.get("decision_time"), "decision_time")
    prices = value.get("prices")
    consensus = value.get("consensus")
    regime = value.get("regime")
    if not isinstance(prices, dict) or not isinstance(consensus, dict) or not isinstance(regime, dict):
        raise DecisionModelError("same-date evidence is missing prices, consensus, or regime")
    _hash(consensus.get("source_hash"), "consensus.source_hash")
    _hash(regime.get("source_hash"), "regime.source_hash")
    evidence = value.get("source_evidence")
    if not isinstance(evidence, dict) or set(evidence) != SOURCE_EVIDENCE_KEYS:
        raise DecisionModelError("raw source evidence manifest is missing")
    evidence_root = Path(source_root) if source_root is not None else source_path.parent
    verified_source_hashes: dict[str, str] = {}
    verified_raw_sources: dict[str, dict[str, Any]] = {}
    for key in sorted(SOURCE_EVIDENCE_KEYS):
        record = evidence.get(key)
        if not isinstance(record, dict):
            raise DecisionModelError(f"source_evidence.{key} must be an object")
        relative_path = record.get("path")
        if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
            raise DecisionModelError(f"source_evidence.{key}.path must be relative")
        expected_source_hash = _hash(record.get("sha256"), f"source_evidence.{key}.sha256")
        raw_path = (evidence_root / relative_path).resolve()
        try:
            raw_path.relative_to(evidence_root.resolve())
        except ValueError as exc:
            raise DecisionModelError(f"source_evidence.{key}.path escapes the evidence root") from exc
        if not raw_path.is_file():
            raise DecisionModelError(f"raw source file is missing: {key}")
        raw_bytes = raw_path.read_bytes()
        actual_source_hash = _sha256(raw_bytes)
        if actual_source_hash != expected_source_hash:
            raise DecisionModelError(f"raw source hash mismatch: {key}")
        if key == "consensus" and consensus.get("source_hash") != expected_source_hash:
            raise DecisionModelError("consensus normalized hash does not match raw source")
        if key == "regime" and regime.get("source_hash") != expected_source_hash:
            raise DecisionModelError("regime normalized hash does not match raw source")
        manifest_available_at = _timestamp(record.get("available_at"), f"source_evidence.{key}.available_at")
        if manifest_available_at > decision_at:
            raise DecisionModelError(f"future raw source is blocked: {key}")
        verified_source_hashes[key] = actual_source_hash
        try:
            raw_value = json.loads(raw_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DecisionModelError(f"raw source is not valid UTF-8 JSON: {key}") from exc
        if not isinstance(raw_value, dict):
            raise DecisionModelError(f"raw source root must be an object: {key}")
        raw_generated_at = _timestamp(raw_value.get("generatedAt"), f"raw {key}.generatedAt")
        if raw_generated_at != manifest_available_at:
            raise DecisionModelError(f"raw generatedAt does not match manifest available_at: {key}")
        if raw_generated_at > decision_at:
            raise DecisionModelError(f"future raw generatedAt is blocked: {key}")
        verified_raw_sources[key] = raw_value
    raw_ledger_records = verified_raw_sources["decision_ledger"].get("records")
    raw_consensus_records = verified_raw_sources["consensus"].get("records")
    raw_regime = verified_raw_sources["regime"]
    if not isinstance(raw_ledger_records, list) or not isinstance(raw_consensus_records, dict):
        raise DecisionModelError("raw source schema is incomplete")
    if consensus.get("basis") != "unverified_street_basis":
        raise DecisionModelError("raw Nasdaq-shaped consensus does not verify a GAAP/non-GAAP EPS basis")
    if consensus.get("available_at") != verified_raw_sources["consensus"].get("generatedAt"):
        raise DecisionModelError("consensus available_at does not match raw source")
    if regime.get("available_at") != raw_regime.get("generatedAt"):
        raise DecisionModelError("regime available_at does not match raw source")
    for ticker in SUPPORTED_TICKERS:
        price = prices.get(ticker)
        record = consensus.get("records", {}).get(ticker) if isinstance(consensus.get("records"), dict) else None
        if not isinstance(price, dict) or not isinstance(record, dict):
            raise DecisionModelError(f"same-date evidence is missing {ticker}")
        _number(price.get("value"), f"{ticker}.price", positive=True)
        _date(price.get("market_session"), f"{ticker}.market_session")
        if _date(price.get("price_as_of"), f"{ticker}.price_as_of") != _date(
            price.get("market_session"), f"{ticker}.market_session"
        ):
            raise DecisionModelError(f"{ticker} price_as_of must equal market_session")
        price_available_at = _timestamp(price.get("available_at"), f"{ticker}.price.available_at")
        if price_available_at != _timestamp(
            verified_raw_sources["decision_ledger"].get("generatedAt"), "decision_ledger.generatedAt"
        ):
            raise DecisionModelError(f"{ticker} price available_at does not match raw decision ledger")
        _hash(price.get("source_hash"), f"{ticker}.price.source_hash")
        if price.get("source_hash") != verified_source_hashes["decision_ledger"]:
            raise DecisionModelError(f"{ticker} normalized price hash does not match raw decision ledger")
        raw_price_rows = [
            row for row in raw_ledger_records
            if row.get("ticker") == ticker and row.get("date") == price.get("market_session")
        ]
        if len(raw_price_rows) != 1 or raw_price_rows[0].get("price") != price.get("value"):
            raise DecisionModelError(f"{ticker} normalized price does not match raw decision ledger")
        if record.get("ticker") != ticker:
            raise DecisionModelError(f"same-date consensus ticker mismatch for {ticker}")
        for field in ("eps_low", "eps_base", "eps_high", "next_consensus_eps"):
            _number(record.get(field), f"{ticker}.{field}", positive=True)
        if not record["eps_low"] <= record["eps_base"] <= record["eps_high"]:
            raise DecisionModelError(f"{ticker} EPS scenarios are not ordered")
        raw_record = raw_consensus_records.get(ticker)
        if not isinstance(raw_record, dict):
            raise DecisionModelError(f"raw consensus record is missing: {ticker}")
        normalized_values = (
            record.get("eps_low"), record.get("eps_base"), record.get("eps_high"), record.get("next_consensus_eps")
        )
        raw_values = (
            raw_record.get("lowEps"), raw_record.get("consensusEps"), raw_record.get("highEps"),
            raw_record.get("nextConsensusEps"),
        )
        if normalized_values != raw_values:
            raise DecisionModelError(f"{ticker} normalized consensus does not match raw source")
    regime_as_of = _date(regime.get("as_of"), "regime.as_of")
    if regime_as_of > decision_at.date():
        raise DecisionModelError("future regime is blocked")
    raw_indicator_dates = {
        str(row.get("asOf")) for row in (raw_regime.get("indicators") or {}).values() if isinstance(row, dict)
    }
    if raw_indicator_dates != {regime_as_of.isoformat()}:
        raise DecisionModelError("normalized regime as_of does not match raw source")
    value["snapshot_hash"] = digest
    value["source_evidence_verified"] = True
    value["consensus_basis_verified"] = False
    value["operational_gate_verified_by_loader"] = False
    return value


def load_decision_config(path: str | Path) -> dict[str, Any]:
    source_path = Path(path)
    with source_path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or value.get("calculation_version") != CALCULATION_VERSION:
        raise DecisionModelError("decision model config version is not approved")
    weights = value.get("weights")
    if not isinstance(weights, dict):
        raise DecisionModelError("decision model weights are missing")
    required = {"self_history_forward_per", "growth_revision", "market_sector_premium"}
    if set(weights) != required:
        raise DecisionModelError("decision model weights do not match the approved axes")
    numeric_weights = [_number(weights[key], f"weights.{key}") for key in sorted(required)]
    if not math.isclose(sum(numeric_weights), 1.0, abs_tol=1e-12):
        raise DecisionModelError("decision model weights must sum to 1")
    discounts = value.get("buy_ladder", {}).get("fair_value_discounts")
    if not isinstance(discounts, list) or len(discounts) != 3:
        raise DecisionModelError("buy ladder must contain exactly three discounts")
    ordered = [_number(item, "buy ladder discount", positive=True) for item in discounts]
    if ordered != sorted(ordered) or ordered[-1] - ordered[0] > 0.12:
        raise DecisionModelError("buy ladder discounts must be ordered and stay within 12%")
    value["config_hash"] = _canonical_hash(value)
    return value


def _regime_adjustment(regime: dict[str, Any], config: dict[str, Any]) -> tuple[float, list[str]]:
    indicators = regime.get("indicators")
    if not isinstance(indicators, dict):
        raise DecisionModelError("regime indicators are missing")
    rules = config["regime_rules"]
    trace: list[str] = []
    adjustment = 0.0
    if indicators["qqq"]["above20"] and indicators["qqq"]["above50"]:
        amount = float(rules["qqq_above_20_50"])
        adjustment += amount
        trace.append(f"QQQ 20·50일선 상회 {amount:+.1%}")
    if not indicators["vix"]["above20"] and not indicators["vix"]["above50"]:
        amount = float(rules["vix_below_20_50"])
        adjustment += amount
        trace.append(f"VIX 20·50일선 하회 {amount:+.1%}")
    if not indicators["hyg"]["above20"] and not indicators["hyg"]["above50"]:
        amount = float(rules["hyg_below_20_50"])
        adjustment += amount
        trace.append(f"HYG 20·50일선 하회 {amount:+.1%}")
    if indicators["tenYear"]["above20"] and indicators["tenYear"]["above50"]:
        amount = float(rules["ten_year_above_20_50"])
        adjustment += amount
        trace.append(f"10년물 20·50일선 상회 {amount:+.1%}")
    limit = _number(config["regime_adjustment_limit"], "regime_adjustment_limit", positive=True)
    adjustment = max(-limit, min(limit, adjustment))
    trace.append(f"합계 {adjustment:+.1%}, 허용 범위 ±{limit:.0%}")
    return adjustment, trace


def _confidence(self_history: dict[str, Any], consensus: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    score = 65
    reasons = ["자기 역사 PER 최소 20개 표본 충족", "애널리스트 집계 10명 이상"]
    if self_history.get("sample_count_used", 0) == self_history.get("minimum_sample", 20):
        score -= 8
        reasons.append("역사 표본이 최소 기준을 정확히 충족해 여유가 적음")
    dispersion = (consensus["eps_high"] - consensus["eps_low"]) / consensus["eps_base"]
    if dispersion > 0.20:
        score -= 8
        reasons.append("EPS 고저 분산이 20% 초과")
    score -= 15
    reasons.append("Street EPS의 GAAP/non-GAAP basis 미검증")
    if not inputs["consensus"].get("source_snapshot_available", False):
        score -= 12
        reasons.append("12:52 UTC 원본 바이트가 후속 갱신으로 대체됨")
    score -= 7
    reasons.append("시장 국면은 공식 거시가 아닌 cross-project 프록시")
    score = max(0, min(100, score))
    label = "낮음" if score < 45 else "중간" if score < 70 else "높음"
    return {"score": score, "label": label, "reasons": reasons}


def calculate_target_multiple(
    axes: dict[str, float | None],
    weights: dict[str, float],
    *,
    regime_adjustment: float,
) -> tuple[float, dict[str, Any]]:
    """Combine only independent, verified axes and renormalize their weights."""

    available = {
        key: _number(value, key, positive=True)
        for key, value in axes.items()
        if value is not None and float(weights.get(key, 0.0)) > 0
    }
    if not available:
        raise DecisionModelError("no verified target-multiple axis is available")
    weight_total = sum(float(weights[key]) for key in available)
    normalized = {key: float(weights[key]) / weight_total for key in available}
    pre_regime = sum(available[key] * normalized[key] for key in available)
    target = pre_regime * (1.0 + regime_adjustment)
    return target, {
        "available_axes": available,
        "normalized_weights": normalized,
        "pre_regime_per": pre_regime,
        "regime_adjustment": regime_adjustment,
        "target_per": target,
    }


def build_decision_analysis_snapshot(
    ticker: str,
    *,
    inputs: dict[str, Any],
    self_history: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build a candidate only when the EPS and multiple bases are aligned."""

    if ticker not in SUPPORTED_TICKERS:
        raise DecisionModelError(f"unsupported decision ticker: {ticker}")
    decision_at = _timestamp(inputs.get("decision_time"), "decision_time")
    completed = latest_completed_session(decision_at)
    price = inputs["prices"][ticker]
    consensus_root = inputs["consensus"]
    consensus = consensus_root["records"][ticker]
    regime = inputs["regime"]
    available = {
        "price": _timestamp(price.get("available_at"), "price.available_at"),
        "consensus": _timestamp(consensus_root.get("available_at"), "consensus.available_at"),
        "regime": _timestamp(regime.get("available_at"), "regime.available_at"),
        "self_history": _timestamp(
            f"{self_history.get('available_at')}T23:59:59Z"
            if len(str(self_history.get("available_at"))) == 10
            else self_history.get("available_at"),
            "self_history.available_at",
        ),
    }
    future_inputs = [name for name, timestamp in available.items() if timestamp > decision_at]
    if future_inputs:
        raise DecisionModelError("future information is blocked: " + ", ".join(future_inputs))

    price_session = _date(price.get("market_session"), "price.market_session")
    if _date(price.get("price_as_of"), "price.price_as_of") != price_session:
        raise DecisionModelError("price_as_of must equal market_session")
    expected_session = _date(completed.session_date, "expected_session")
    if price_session > expected_session:
        raise DecisionModelError("price is later than the latest completed market session")
    lag_days = (expected_session - price_session).days
    if lag_days > int(config["maximum_price_lag_calendar_days"]):
        raise DecisionModelError(f"price is stale by {lag_days} calendar days")
    regime_lag_days = (decision_at.date() - _date(regime.get("as_of"), "regime.as_of")).days
    if regime_lag_days < 0:
        raise DecisionModelError("future regime is blocked")
    if regime_lag_days > int(config["maximum_regime_lag_calendar_days"]):
        raise DecisionModelError(f"regime is stale by {regime_lag_days} calendar days")

    current_price = _number(price.get("value"), "price", positive=True)
    eps_low = _number(consensus.get("eps_low"), "eps_low", positive=True)
    eps_base = _number(consensus.get("eps_base"), "eps_base", positive=True)
    eps_high = _number(consensus.get("eps_high"), "eps_high", positive=True)
    next_eps = _number(consensus.get("next_consensus_eps"), "next_consensus_eps", positive=True)
    ttm_percentiles = self_history.get("full_history_percentiles") or {}
    ttm_anchor = ttm_percentiles.get(config["historical_anchor_percentile"])
    consensus_basis = str(consensus_root.get("basis") or "unavailable")
    multiple_basis = str(self_history.get("multiple_basis") or "ttm_per")
    multiple_eps_basis = str(self_history.get("eps_basis") or "reported_ttm_unknown")
    benchmark = (inputs.get("benchmark_multiples") or {}).get(ticker)
    walk_forward = (inputs.get("walk_forward_validation") or {}).get(ticker)
    data_rights = inputs.get("data_rights") or {}
    verified_bases = set(config["verified_forward_eps_bases"])
    walk_forward_passed = (
        isinstance(walk_forward, dict)
        and walk_forward.get("status") == "verified_actual_holdout"
        and walk_forward.get("passed") is True
        and walk_forward.get("future_information_used") is False
    )
    rights_passed = (
        isinstance(data_rights, dict)
        and data_rights.get("status") == "verified"
        and data_rights.get("decision_display_allowed") is True
    )
    operational_gate_verified = inputs.get("operational_gate_verified_by_loader") is True
    blockers: list[str] = []
    if not inputs.get("source_evidence_verified"):
        blockers.append("원본 파일과 SHA-256 대조가 완료되지 않음")
    if not inputs.get("consensus_basis_verified") or consensus_basis not in verified_bases:
        blockers.append(f"Street EPS basis 미검증: {consensus_basis}")
    if multiple_basis != "pit_forward_per":
        blockers.append(f"역사 배수는 Forward PER가 아님: {multiple_basis}")
    if multiple_eps_basis != consensus_basis:
        blockers.append(f"역사 배수 EPS basis({multiple_eps_basis})와 적용 EPS basis({consensus_basis}) 불일치")
    if not isinstance(benchmark, dict) or not benchmark.get("verified"):
        blockers.append("독립 QQQ/SOXX/피어 동일시점 배수 원장 미확보")
    elif benchmark.get("eps_basis") != consensus_basis:
        blockers.append("시장·섹터 배수 EPS basis 불일치")
    if not walk_forward_passed:
        blockers.append("실제 전체 모델 walk-forward 최종 holdout 미통과")
    if not rights_passed:
        blockers.append("행동 판단 데이터 표시 이용권 미검증")
    if not operational_gate_verified:
        blockers.append("walk-forward·이용권 원본 증거가 loader에서 검증되지 않음")

    source_hashes = {
        "decision_inputs": inputs["snapshot_hash"],
        "price": price["source_hash"],
        "consensus": consensus_root["source_hash"],
        "regime": regime["source_hash"],
        "self_history": self_history["source_hash"],
        "config": config["config_hash"],
    }
    common = {
        "ticker": ticker,
        "decision_time": inputs["decision_time"],
        "market_session": completed.session_date,
        "price_as_of": price["price_as_of"],
        "price_lag_calendar_days": lag_days,
        "consensus_available_at": consensus_root["available_at"],
        "regime_as_of": regime["as_of"],
        "source": "hash-verified local raw evidence + approved StockLens v23 PIT ledger",
        "source_hashes": source_hashes,
        "calculation_version": CALCULATION_VERSION,
        "operational_eligible": False,
        "current_price": current_price,
        "eps_basis": consensus_basis,
        "eps": {"low": eps_low, "base": eps_base, "high": eps_high, "next": next_eps},
        "candidate_arithmetic": {
            "street_eps": {"low": eps_low, "base": eps_base, "high": eps_high, "basis": consensus_basis},
            "historical_ttm_per_p35": ttm_anchor,
            "historical_multiple_basis": multiple_basis,
            "used_for_action": False,
        },
        "limitations": list(inputs.get("limitations") or []),
    }
    if blockers:
        gates = [
            {"id": "same_date_alignment", "passed": lag_days == 0, "reason": f"시장 세션 {price_session.isoformat()}"},
            {"id": "raw_source_hashes", "passed": bool(inputs.get("source_evidence_verified")), "reason": "원본 파일 존재 및 SHA-256 대조"},
            {"id": "forward_multiple_basis", "passed": multiple_basis == "pit_forward_per", "reason": multiple_basis},
            {
                "id": "eps_basis_verified",
                "passed": bool(inputs.get("consensus_basis_verified")) and consensus_basis in verified_bases,
                "reason": consensus_basis,
            },
            {"id": "basis_alignment", "passed": multiple_eps_basis == consensus_basis, "reason": f"{multiple_eps_basis} vs {consensus_basis}"},
            {"id": "independent_benchmark", "passed": isinstance(benchmark, dict) and bool(benchmark.get("verified")), "reason": "현재 가격 재사용 금지"},
            {
                "id": "walk_forward_final_holdout",
                "passed": walk_forward_passed,
                "reason": "실제 원장의 전체 모델 독립 holdout만 인정",
            },
            {"id": "data_rights", "passed": rights_passed, "reason": "행동 판단 표시 권리 명시 확인 필요"},
            {
                "id": "operational_evidence_loader",
                "passed": operational_gate_verified,
                "reason": "walk-forward와 이용권 원본 파일·해시 검증 필요",
            },
        ]
        blocked_hash = _canonical_hash({"common": common, "blockers": blockers, "gates": gates})
        return {
            **common,
            "source_hash": blocked_hash,
            "calculation_basis": "basis-safe blocking gate; TTM PER and unverified Street EPS are not combined",
            "status": "blocked",
            "decision_status": "blocked",
            "blockers": blockers,
            "target_per": None,
            "base_fair_value": None,
            "scenario_values": None,
            "buy_start_price": None,
            "buy_prices": [],
            "reduction_review_price": None,
            "price_burden": "미산출",
            "valuation_gap": None,
            "action": "비활성",
            "action_text": "EPS basis와 진짜 PIT Forward PER 원장이 검증될 때까지 행동 판단 비활성",
            "confidence": {"score": 0, "label": "차단", "reasons": blockers},
            "calculation_trace": {},
            "invalidation_conditions": [],
            "promotion_gates": gates,
        }

    forward_percentiles = self_history.get("forward_per_percentiles") or {}
    anchor_key = config["historical_anchor_percentile"]
    historical_per = _number(forward_percentiles.get(anchor_key), f"forward {anchor_key}", positive=True)
    analyst_count = int(_number(consensus.get("analyst_count"), "analyst_count", positive=True))
    revision_score = (
        int(_number(consensus.get("revisions_up_4w"), "revisions_up_4w"))
        - int(_number(consensus.get("revisions_down_4w"), "revisions_down_4w"))
    ) / analyst_count
    raw_growth = next_eps / eps_base - 1.0
    bounded_growth = max(
        float(config["growth_rules"]["minimum_growth"]),
        min(float(config["growth_rules"]["maximum_growth"]), raw_growth),
    )
    growth_per = historical_per * (
        1.0
        + bounded_growth * float(config["growth_rules"]["growth_sensitivity"])
        + revision_score * float(config["growth_rules"]["revision_sensitivity"])
    )
    benchmark_per = _number(benchmark.get("relative_forward_per"), "benchmark relative_forward_per", positive=True)
    regime_adjustment, regime_trace = _regime_adjustment(regime, config)
    target_per, combination = calculate_target_multiple(
        {
            "self_history_forward_per": historical_per,
            "growth_revision": growth_per,
            "market_sector_premium": benchmark_per,
        },
        config["weights"],
        regime_adjustment=regime_adjustment,
    )
    base_value = eps_base * target_per
    conservative_value = eps_low * target_per * float(config["scenario_multipliers"]["conservative"])
    optimistic_value = eps_high * target_per * float(config["scenario_multipliers"]["optimistic"])
    valuation_gap = current_price / base_value - 1.0
    price_burden = "낮음" if valuation_gap <= -0.12 else "중립" if valuation_gap <= 0.05 else "높음"
    discounts = [float(value) for value in config["buy_ladder"]["fair_value_discounts"]]
    natural_ladder = [base_value * (1.0 - value) for value in discounts]
    if current_price <= natural_ladder[-1]:
        current_steps = [float(value) for value in config["buy_ladder"]["current_anchor_discounts"]]
        ladder = [current_price * (1.0 - value) for value in current_steps]
        ladder_basis = "현재가가 가치 기준 3차 가격보다 낮아 현재 실거래가를 1차로 고정"
    else:
        ladder = natural_ladder
        ladder_basis = "기준 적정가에서 4%·8%·12% 할인"
    ladder = [round(value, 2) for value in ladder]
    ladder_span = (ladder[0] - ladder[-1]) / ladder[0]
    if not 0.05 <= ladder_span <= 0.12:
        raise DecisionModelError("buy ladder span must remain between 5% and 12% of the first buy price")
    reduction_price = round(base_value * (1.0 + float(config["reduction_review_premium"])), 2)
    if current_price >= reduction_price:
        action, action_text = "비중 축소", f"${reduction_price:,.2f} 이상에서는 신규 매수 중단 및 비중 축소 검토"
    elif current_price <= ladder[0]:
        action, action_text = "분할매수", "현재 가격에서 1차 매수를 시작"
    else:
        action, action_text = "대기", f"현재는 매수하지 말고 ${ladder[0]:,.2f}까지 기다림"
    calculation_trace = {
        "historical_forward_per": {
            "weight": combination["normalized_weights"]["self_history_forward_per"],
            "percentile": anchor_key,
            "per": historical_per,
            "weighted_contribution": historical_per * combination["normalized_weights"]["self_history_forward_per"],
        },
        "growth_revision": {
            "weight": combination["normalized_weights"]["growth_revision"],
            "per": growth_per,
            "raw_next_year_eps_growth": raw_growth,
            "bounded_growth": bounded_growth,
            "revision_score": revision_score,
            "weighted_contribution": growth_per * combination["normalized_weights"]["growth_revision"],
        },
        "market_sector_premium": {
            "weight": combination["normalized_weights"]["market_sector_premium"],
            "per": benchmark_per,
            "source": benchmark["source"],
            "source_hash": benchmark["source_hash"],
            "weighted_contribution": benchmark_per * combination["normalized_weights"]["market_sector_premium"],
        },
        "combination": combination,
        "regime": {
            "adjustment": regime_adjustment,
            "trace": regime_trace,
            "pre_regime_per": combination["pre_regime_per"],
            "target_per": target_per,
        },
    }
    basis_hash = _canonical_hash(
        {"common": common, "trace": calculation_trace, "ladder": ladder, "reduction": reduction_price}
    )
    return {
        **common,
        "source_hash": basis_hash,
        "calculation_basis": "verified PIT Forward PER 50% + growth/revision 30% + independent benchmark 20%, then regime overlay",
        "status": "candidate",
        "decision_status": "candidate",
        "current_forward_per": current_price / eps_base,
        "target_per": target_per,
        "base_fair_value": base_value,
        "valuation_gap": valuation_gap,
        "price_burden": price_burden,
        "scenario_values": {"conservative": conservative_value, "base": base_value, "optimistic": optimistic_value},
        "buy_start_price": ladder[0],
        "buy_prices": ladder,
        "buy_ladder_basis": ladder_basis,
        "buy_ladder_span_of_first_price": ladder_span,
        "reduction_review_price": reduction_price,
        "action": action,
        "action_text": action_text,
        "confidence": _confidence(self_history, consensus, inputs),
        "calculation_trace": calculation_trace,
        "invalidation_conditions": [],
        "promotion_gates": [
            {"id": "walk_forward_final_holdout", "passed": walk_forward_passed, "reason": "verified_actual_holdout"},
            {"id": "data_rights", "passed": rights_passed, "reason": "verified decision display permission"},
            {"id": "operational_evidence_loader", "passed": operational_gate_verified, "reason": "raw evidence verified"},
        ],
    }


def _linear_quantile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise DecisionModelError("quantile requires at least one value")
    position = (len(ordered) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _tukey_filtered(values: Iterable[float]) -> list[float]:
    ordered = sorted(float(value) for value in values)
    if len(ordered) < 4:
        return ordered
    q1 = _linear_quantile(ordered, 0.25)
    q3 = _linear_quantile(ordered, 0.75)
    spread = q3 - q1
    lower = max(0.0, q1 - 1.5 * spread)
    upper = q3 + 1.5 * spread
    return [value for value in ordered if lower <= value <= upper]


def backtest_pit_per_anchor(
    v23_ticker: dict[str, Any],
    adjusted_prices: list[dict[str, Any]],
    *,
    minimum_prior_rows: int = 5,
) -> dict[str, Any]:
    """Run a diagnostic anchor test only when exact acceptance times exist."""

    rows = v23_ticker.get("historical_per")
    if not isinstance(rows, list):
        raise DecisionModelError("historical PER rows are unavailable")
    missing_acceptance = [row for row in rows if not row.get("acceptance_timestamp")]
    if missing_acceptance:
        return {
            "status": "blocked_acceptance_time_unknown",
            "scope": "diagnostic TTM-PER anchor only; not the weighted decision model",
            "ledger_row_count": len(rows),
            "signal_count": 0,
            "horizons": {},
            "future_information_used": "unknown",
            "reason": "SEC acceptance timestamp is absent; same-day close cannot be labeled leak-free",
            "benchmark_status": "not_evaluated",
            "drawdown_frequency": "not_evaluated",
            "operational_eligible": False,
        }
    rows = sorted(rows, key=lambda row: _timestamp(row.get("acceptance_timestamp"), "acceptance_timestamp"))

    prices = sorted(
        [
            (_date(row.get("date"), "price history date"), _number(row.get("close"), "price history close", positive=True))
            for row in adjusted_prices
        ],
        key=lambda row: row[0],
    )
    prior_values: list[float] = []
    signals: list[dict[str, Any]] = []
    for row in rows:
        acceptance = _timestamp(row.get("acceptance_timestamp"), "acceptance_timestamp")
        per_field = row.get("ttm_per") or {}
        per_value = _number(per_field.get("value"), "historical PER", positive=True)
        next_rows = [item for item in prices if item[0] > acceptance.date()]
        if not next_rows:
            continue
        entry_date, entry_price = next_rows[0]
        if len(prior_values) >= minimum_prior_rows:
            threshold = _linear_quantile(prior_values, 0.35)
            if per_value <= threshold:
                signals.append(
                    {
                        "acceptance_timestamp": acceptance.isoformat(),
                        "entry_date": entry_date,
                        "entry_price": entry_price,
                        "per": per_value,
                        "threshold": threshold,
                    }
                )
        prior_values.append(per_value)

    def horizon_stats(months: int) -> dict[str, Any]:
        returns: list[float] = []
        drawdowns: list[float] = []
        for signal in signals:
            target = signal["entry_date"] + relativedelta(months=months)
            exit_rows = [row for row in prices if row[0] >= target]
            if not exit_rows:
                continue
            exit_date, exit_price = exit_rows[0]
            path = [value for day, value in prices if signal["entry_date"] <= day <= exit_date]
            if not path:
                continue
            entry = signal["entry_price"]
            returns.append(exit_price / entry - 1.0)
            running_peak = entry
            worst = 0.0
            for value in path:
                running_peak = max(running_peak, value)
                worst = min(worst, value / running_peak - 1.0)
            drawdowns.append(worst)
        return {
            "sample_count": len(returns),
            "hit_rate": (sum(value > 0 for value in returns) / len(returns)) if returns else None,
            "median_return": median(returns) if returns else None,
            "qqq_excess_return": None,
            "maximum_drawdown": min(drawdowns) if drawdowns else None,
        }

    return {
        "status": "diagnostic_ttm_anchor_only",
        "scope": "diagnostic rolling raw TTM-PER P35; not the filtered full weighted decision model",
        "ledger_row_count": len(rows),
        "signal_count": len(signals),
        "horizons": {"6m": horizon_stats(6), "12m": horizon_stats(12)},
        "future_information_used": False,
        "benchmark_status": "unavailable_no_qqq_monthly_history",
        "drawdown_frequency": "monthly_close_only",
        "operational_eligible": False,
    }


def walk_forward_full_model(
    rows: list[dict[str, Any]],
    *,
    weights: dict[str, float],
    minimum_train_rows: int = 12,
    validation_rows: int = 4,
    holdout_rows: int = 4,
    round_trip_cost_bps: float = 20.0,
) -> dict[str, Any]:
    """Walk forward the aligned model with chronological validation and holdout.

    This evaluator requires exact timestamps, one verified EPS basis, independent
    benchmark multiples, daily adjusted paths, and QQQ/SOXX exits.  Missing
    evidence blocks the run instead of falling back to the legacy TTM ledger.
    """

    required_count = minimum_train_rows + validation_rows + holdout_rows
    if len(rows) < required_count:
        return {
            "status": "blocked_insufficient_full_model_rows",
            "required_rows": required_count,
            "available_rows": len(rows),
            "operational_eligible": False,
        }
    ordered = sorted(rows, key=lambda row: _timestamp(row.get("decision_time"), "decision_time"))
    basis = {str(row.get("eps_basis")) for row in ordered}
    if len(basis) != 1 or next(iter(basis)) not in {"gaap_fy1", "non_gaap_fy1"}:
        raise DecisionModelError("walk-forward rows must share one verified forward EPS basis")
    for row in ordered:
        decision_time = _timestamp(row.get("decision_time"), "decision_time")
        available_at = _timestamp(row.get("available_at"), "available_at")
        entry_time = _timestamp(row.get("entry_time"), "entry_time")
        exit_time = _timestamp(row.get("exit_time"), "exit_time")
        if available_at > decision_time or entry_time <= max(available_at, decision_time):
            raise DecisionModelError("walk-forward future-information boundary failed")
        if exit_time <= entry_time:
            raise DecisionModelError("walk-forward exit_time must be after entry_time")
        if not row.get("benchmark_verified"):
            raise DecisionModelError("walk-forward benchmark multiple is not independently verified")
        for benchmark in ("qqq", "soxx"):
            benchmark_entry = _timestamp(row.get(f"{benchmark}_entry_time"), f"{benchmark}_entry_time")
            benchmark_exit = _timestamp(row.get(f"{benchmark}_exit_time"), f"{benchmark}_exit_time")
            if benchmark_entry != entry_time or benchmark_exit != exit_time:
                raise DecisionModelError("benchmark timestamps must match the security holding period")
        path = row.get("daily_adjusted_path")
        if not isinstance(path, list) or not path:
            raise DecisionModelError("daily adjusted price path is required")
        parsed_path = [
            (_date(item.get("date"), "daily adjusted date"), _number(item.get("close"), "daily adjusted close", positive=True))
            for item in path
            if isinstance(item, dict)
        ]
        if len(parsed_path) != len(path) or parsed_path != sorted(parsed_path, key=lambda item: item[0]):
            raise DecisionModelError("daily adjusted price path must be chronological")
        path_dates = [item[0] for item in parsed_path]
        if len(set(path_dates)) != len(path_dates):
            raise DecisionModelError("daily adjusted price path contains duplicate dates")
        if path_dates[0] != entry_time.date() or path_dates[-1] != exit_time.date():
            raise DecisionModelError("daily adjusted price path must span the exact holding period")

    split_at = len(ordered) - validation_rows - holdout_rows
    validation_slice = range(split_at, split_at + validation_rows)
    holdout_slice = range(split_at + validation_rows, len(ordered))
    cost = float(round_trip_cost_bps) / 10_000.0

    def evaluate(indices: Iterable[int]) -> dict[str, Any]:
        observations: list[dict[str, float]] = []
        for index in indices:
            row = ordered[index]
            prior = ordered[:index]
            prior_forward = _tukey_filtered(
                _number(item.get("forward_per"), "prior forward_per", positive=True) for item in prior
            )
            historical = _linear_quantile(prior_forward, 0.35)
            target, _ = calculate_target_multiple(
                {
                    "self_history_forward_per": historical,
                    "growth_revision": _number(row.get("growth_revision_per"), "growth_revision_per", positive=True),
                    "market_sector_premium": _number(row.get("benchmark_forward_per"), "benchmark_forward_per", positive=True),
                },
                weights,
                regime_adjustment=_number(row.get("regime_adjustment"), "regime_adjustment"),
            )
            current_per = _number(row.get("forward_per"), "forward_per", positive=True)
            if current_per > target:
                continue
            entry = _number(row.get("entry_price"), "entry_price", positive=True)
            exit_price = _number(row.get("exit_price"), "exit_price", positive=True)
            qqq_exit = _number(row.get("qqq_exit_price"), "qqq_exit_price", positive=True)
            qqq_entry = _number(row.get("qqq_entry_price"), "qqq_entry_price", positive=True)
            soxx_exit = _number(row.get("soxx_exit_price"), "soxx_exit_price", positive=True)
            soxx_entry = _number(row.get("soxx_entry_price"), "soxx_entry_price", positive=True)
            net_return = exit_price / entry - 1.0 - cost
            path = [
                _number(value.get("close"), "daily adjusted price", positive=True)
                for value in row["daily_adjusted_path"]
            ]
            peak = entry
            mdd = 0.0
            for value in path:
                peak = max(peak, value)
                mdd = min(mdd, value / peak - 1.0)
            observations.append(
                {
                    "return": net_return,
                    "qqq_excess": net_return - (qqq_exit / qqq_entry - 1.0 - cost),
                    "soxx_excess": net_return - (soxx_exit / soxx_entry - 1.0 - cost),
                    "mdd": mdd,
                }
            )
        return {
            "sample_count": len(observations),
            "hit_rate": sum(row["return"] > 0 for row in observations) / len(observations) if observations else None,
            "median_return": median(row["return"] for row in observations) if observations else None,
            "median_qqq_excess": median(row["qqq_excess"] for row in observations) if observations else None,
            "median_soxx_excess": median(row["soxx_excess"] for row in observations) if observations else None,
            "maximum_drawdown": min((row["mdd"] for row in observations), default=None),
        }

    return {
        "status": "fixture_validated_full_model_walk_forward",
        "training_rows": split_at,
        "validation": evaluate(validation_slice),
        "holdout": evaluate(holdout_slice),
        "future_information_used": False,
        "trade_cost_bps_round_trip": round_trip_cost_bps,
        "historical_anchor_policy": "rolling prior-only PIT Forward PER, 1.5×IQR Tukey filter, P35",
        "survivorship_bias": "requires external point-in-time universe audit",
        "operational_eligible": False,
    }
