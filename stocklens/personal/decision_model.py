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


CALCULATION_VERSION = "same-date-investment-decision-v1"
APPROVED_LOCAL_INPUT_SHA256 = "AD1E6768C5A4AF89FA5547FABFCAA0C01421334E10BD55C52946CAC3DBCA4FA1"
SUPPORTED_TICKERS = frozenset({"AVGO", "NVDA"})


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
) -> dict[str, Any]:
    """Load the local normalized evidence snapshot without modifying it."""

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
    _timestamp(value.get("decision_time"), "decision_time")
    prices = value.get("prices")
    consensus = value.get("consensus")
    regime = value.get("regime")
    if not isinstance(prices, dict) or not isinstance(consensus, dict) or not isinstance(regime, dict):
        raise DecisionModelError("same-date evidence is missing prices, consensus, or regime")
    _hash(consensus.get("source_hash"), "consensus.source_hash")
    _hash(regime.get("source_hash"), "regime.source_hash")
    for ticker in SUPPORTED_TICKERS:
        price = prices.get(ticker)
        record = consensus.get("records", {}).get(ticker) if isinstance(consensus.get("records"), dict) else None
        if not isinstance(price, dict) or not isinstance(record, dict):
            raise DecisionModelError(f"same-date evidence is missing {ticker}")
        _number(price.get("value"), f"{ticker}.price", positive=True)
        _date(price.get("market_session"), f"{ticker}.market_session")
        _date(price.get("price_as_of"), f"{ticker}.price_as_of")
        _timestamp(price.get("available_at"), f"{ticker}.price.available_at")
        _hash(price.get("source_hash"), f"{ticker}.price.source_hash")
        if record.get("ticker") != ticker:
            raise DecisionModelError(f"same-date consensus ticker mismatch for {ticker}")
        for field in ("eps_low", "eps_base", "eps_high", "next_consensus_eps"):
            _number(record.get(field), f"{ticker}.{field}", positive=True)
        if not record["eps_low"] <= record["eps_base"] <= record["eps_high"]:
            raise DecisionModelError(f"{ticker} EPS scenarios are not ordered")
    value["snapshot_hash"] = digest
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


def build_decision_analysis_snapshot(
    ticker: str,
    *,
    inputs: dict[str, Any],
    self_history: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Create a deterministic candidate action from evidence aligned to one time."""

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
    expected_session = _date(completed.session_date, "expected_session")
    if price_session > expected_session:
        raise DecisionModelError("price is later than the latest completed market session")
    lag_days = (expected_session - price_session).days
    if lag_days > int(config["maximum_price_lag_calendar_days"]):
        raise DecisionModelError(f"price is stale by {lag_days} calendar days")
    if consensus_root.get("basis") != "unverified_street_basis":
        raise DecisionModelError("Street EPS must remain on the unverified_street_basis axis")
    if not self_history.get("sample_gate_passed"):
        raise DecisionModelError("self-history PER sample gate did not pass")

    percentiles = self_history.get("full_history_percentiles", {})
    anchor_key = config["historical_anchor_percentile"]
    historical_per = _number(percentiles.get(anchor_key), f"historical {anchor_key}", positive=True)
    eps_low = _number(consensus.get("eps_low"), "eps_low", positive=True)
    eps_base = _number(consensus.get("eps_base"), "eps_base", positive=True)
    eps_high = _number(consensus.get("eps_high"), "eps_high", positive=True)
    next_eps = _number(consensus.get("next_consensus_eps"), "next_consensus_eps", positive=True)
    analyst_count = int(_number(consensus.get("analyst_count"), "analyst_count", positive=True))
    revisions_up = int(_number(consensus.get("revisions_up_4w"), "revisions_up_4w"))
    revisions_down = int(_number(consensus.get("revisions_down_4w"), "revisions_down_4w"))
    revision_score = (revisions_up - revisions_down) / analyst_count
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

    current_price = _number(price.get("value"), "price", positive=True)
    current_forward_per = current_price / eps_base
    sector_state = str(price.get("sector_state") or "unavailable")
    sector_adjustment = float(config["market_sector_rules"]["sector_state_adjustments"].get(sector_state, 0.0))
    theme_adjustment = float(config["market_sector_rules"]["ai_semiconductor_leadership_premium"])
    market_sector_adjustment = sector_adjustment + theme_adjustment
    market_sector_per = current_forward_per * (1.0 + market_sector_adjustment)

    weights = config["weights"]
    pre_regime_per = (
        historical_per * float(weights["self_history_forward_per"])
        + growth_per * float(weights["growth_revision"])
        + market_sector_per * float(weights["market_sector_premium"])
    )
    regime_adjustment, regime_trace = _regime_adjustment(regime, config)
    target_per = pre_regime_per * (1.0 + regime_adjustment)
    base_value = eps_base * target_per
    conservative_value = eps_low * target_per * float(config["scenario_multipliers"]["conservative"])
    optimistic_value = eps_high * target_per * float(config["scenario_multipliers"]["optimistic"])
    valuation_gap = current_price / base_value - 1.0
    if valuation_gap <= -0.12:
        price_burden = "낮음"
    elif valuation_gap <= 0.05:
        price_burden = "중립"
    else:
        price_burden = "높음"

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
        action = "비중 축소"
        action_text = f"${reduction_price:,.2f} 이상에서는 신규 매수 중단 및 비중 축소 검토"
    elif current_price <= ladder[0]:
        action = "분할매수"
        action_text = "현재 가격에서 1차 매수를 시작"
    else:
        action = "대기"
        action_text = f"현재는 매수하지 말고 ${ladder[0]:,.2f}까지 기다림"

    confidence = _confidence(self_history, consensus, inputs)
    eps_invalidation = round(eps_base * (1.0 - float(config["invalidation_rules"]["eps_cut"])), 2)
    gates = [
        {"id": "same_date_alignment", "passed": lag_days == 0, "reason": f"예상 세션 {completed.session_date}, 가격 세션 {price_session.isoformat()}"},
        {"id": "future_information_block", "passed": not future_inputs, "reason": "모든 available_at이 decision_time 이하"},
        {"id": "historical_sample", "passed": bool(self_history.get("sample_gate_passed")), "reason": f"사용 {self_history.get('sample_count_used')}건"},
        {"id": "original_consensus_bytes", "passed": bool(consensus_root.get("source_snapshot_available")), "reason": "12:52 UTC 원본 바이트 보존 필요"},
        {"id": "eps_basis_verified", "passed": False, "reason": "Street EPS GAAP/non-GAAP basis 미검증"},
        {"id": "official_macro_regime", "passed": False, "reason": "cross_project_market_proxy 사용"},
        {"id": "walk_forward_final_holdout", "passed": False, "reason": "동일 모델의 독립 최종 검증 미완료"},
        {"id": "data_rights", "passed": False, "reason": "외부 가격·컨센서스 운영 표시 권리 미확정"},
    ]

    calculation_trace = {
        "historical_forward_per": {
            "weight": float(weights["self_history_forward_per"]),
            "percentile": anchor_key,
            "per": historical_per,
            "weighted_contribution": historical_per * float(weights["self_history_forward_per"]),
        },
        "growth_revision": {
            "weight": float(weights["growth_revision"]),
            "raw_next_year_eps_growth": raw_growth,
            "bounded_growth": bounded_growth,
            "revision_score": revision_score,
            "per": growth_per,
            "weighted_contribution": growth_per * float(weights["growth_revision"]),
        },
        "market_sector_premium": {
            "weight": float(weights["market_sector_premium"]),
            "market_clearing_forward_per": current_forward_per,
            "sector_adjustment": sector_adjustment,
            "ai_semiconductor_premium": theme_adjustment,
            "net_adjustment": market_sector_adjustment,
            "per": market_sector_per,
            "weighted_contribution": market_sector_per * float(weights["market_sector_premium"]),
        },
        "regime": {
            "adjustment": regime_adjustment,
            "trace": regime_trace,
            "pre_regime_per": pre_regime_per,
            "target_per": target_per,
        },
    }
    source_hashes = {
        "decision_inputs": inputs["snapshot_hash"],
        "price": price["source_hash"],
        "consensus": consensus_root["source_hash"],
        "regime": regime["source_hash"],
        "self_history": self_history["source_hash"],
        "config": config["config_hash"],
    }
    basis_hash = _canonical_hash(
        {
            "calculation_version": CALCULATION_VERSION,
            "ticker": ticker,
            "decision_time": inputs["decision_time"],
            "source_hashes": source_hashes,
            "trace": calculation_trace,
            "prices": {"current": current_price, "ladder": ladder, "reduction": reduction_price},
        }
    )
    return {
        "ticker": ticker,
        "decision_time": inputs["decision_time"],
        "market_session": completed.session_date,
        "price_as_of": price["price_as_of"],
        "price_lag_calendar_days": lag_days,
        "consensus_available_at": consensus_root["available_at"],
        "regime_as_of": regime["as_of"],
        "source": "local Nasdaq Top10 normalized evidence + approved StockLens v23 PIT ledger",
        "source_hash": basis_hash,
        "source_hashes": source_hashes,
        "calculation_version": CALCULATION_VERSION,
        "calculation_basis": "50% self-history P35 + 30% bounded EPS growth/revision + 20% market-clearing sector/theme PER, followed by a capped regime overlay",
        "status": "candidate",
        "decision_status": "candidate",
        "operational_eligible": False,
        "current_price": current_price,
        "current_forward_per": current_forward_per,
        "target_per": target_per,
        "base_fair_value": base_value,
        "valuation_gap": valuation_gap,
        "price_burden": price_burden,
        "scenario_values": {
            "conservative": conservative_value,
            "base": base_value,
            "optimistic": optimistic_value,
        },
        "buy_start_price": ladder[0],
        "buy_prices": ladder,
        "buy_ladder_basis": ladder_basis,
        "buy_ladder_span_of_first_price": ladder_span,
        "reduction_review_price": reduction_price,
        "action": action,
        "action_text": action_text,
        "confidence": confidence,
        "eps_basis": "unverified_street_basis",
        "eps": {"low": eps_low, "base": eps_base, "high": eps_high, "next": next_eps},
        "calculation_trace": calculation_trace,
        "invalidation_conditions": [
            f"{consensus['fiscal_end']} EPS 기준값이 ${eps_invalidation:.2f} 이하로 하향되면 기존 판단 무효",
            "VIX가 25를 상회하고 QQQ가 20·50일선을 모두 하회하면 목표 PER 재산정",
            f"{price.get('sector_etf', 'SOXX')} 약세가 지속되고 다음 실적에서 매출·마진 가이던스가 하향되면 신규 매수 중단",
        ],
        "promotion_gates": gates,
        "limitations": list(inputs.get("limitations") or []),
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


def backtest_pit_per_anchor(
    v23_ticker: dict[str, Any],
    monthly_prices: list[dict[str, Any]],
    *,
    minimum_prior_rows: int = 5,
) -> dict[str, Any]:
    """Evaluate only the PIT P35 anchor; the full decision model is not reconstructed.

    Monthly closes support 6/12-month return and month-end drawdown checks.  No
    QQQ history is substituted when it is absent.
    """

    prices = sorted(
        [
            (_date(row.get("date"), "price history date"), _number(row.get("close"), "price history close", positive=True))
            for row in monthly_prices
        ],
        key=lambda row: row[0],
    )
    rows = v23_ticker.get("historical_per")
    if not isinstance(rows, list):
        raise DecisionModelError("historical PER rows are unavailable")
    prior_values: list[float] = []
    signals: list[dict[str, Any]] = []
    future_information_used = False
    for row in rows:
        filing = _date(row.get("filing_date"), "filing_date")
        price_field = row.get("price") or {}
        per_field = row.get("ttm_per") or {}
        price_date = _date(price_field.get("as_of"), "historical price date")
        per_value = _number(per_field.get("value"), "historical PER", positive=True)
        entry_price = _number(price_field.get("value"), "historical entry price", positive=True)
        split_basis_factor = _number(row.get("split_basis_factor", 1.0), "split_basis_factor", positive=True)
        adjusted_entry_price = entry_price / split_basis_factor
        if price_date > filing:
            future_information_used = True
            continue
        if len(prior_values) >= minimum_prior_rows:
            threshold = _linear_quantile(prior_values, 0.35)
            if per_value <= threshold:
                signals.append(
                    {
                        "filing_date": filing,
                        "entry_price": adjusted_entry_price,
                        "source_entry_price": entry_price,
                        "split_basis_factor": split_basis_factor,
                        "per": per_value,
                        "threshold": threshold,
                    }
                )
        prior_values.append(per_value)

    def horizon_stats(months: int) -> dict[str, Any]:
        returns: list[float] = []
        drawdowns: list[float] = []
        for signal in signals:
            target = signal["filing_date"] + relativedelta(months=months)
            exit_rows = [row for row in prices if row[0] >= target]
            if not exit_rows:
                continue
            exit_date, exit_price = exit_rows[0]
            path = [value for day, value in prices if signal["filing_date"] <= day <= exit_date]
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
        "status": "partial_pit_anchor_validation",
        "scope": "self-history P35 entry anchor only; not the full weighted decision model",
        "ledger_row_count": len(rows),
        "signal_count": len(signals),
        "horizons": {"6m": horizon_stats(6), "12m": horizon_stats(12)},
        "future_information_used": future_information_used,
        "benchmark_status": "unavailable_no_qqq_monthly_history",
        "drawdown_frequency": "monthly_close_only",
        "operational_eligible": False,
    }
