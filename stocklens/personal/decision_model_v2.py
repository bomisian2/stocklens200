"""Offline Decision Model v2 for a low-confidence provider-homogeneous proxy.

This module is deliberately separate from the frozen P5 diagnostics.  It never
performs network I/O, never creates an operational signal, and verifies every
source file again when a persisted decision is loaded.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .storage import atomic_write_json


MODEL_VERSION = "stocklens-decision-model-v2.0.0"
POLICY_VERSION = "provider-homogeneous-proxy-v2.0.0"
LEDGER_VERSION = "decision-model-v2-signal-ledger-v1"
EXPECTED_PROVIDER = "nasdaq-analyst-earnings-forecast"
EXPECTED_TICKER = "NVDA"


class DecisionModelV2Error(ValueError):
    """Raised when a Decision Model v2 input or artifact is unsafe."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _json_object(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionModelV2Error(f"invalid JSON source: {path.name}") from exc
    if not isinstance(value, dict):
        raise DecisionModelV2Error(f"JSON source must be an object: {path.name}")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DecisionModelV2Error(f"{label} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DecisionModelV2Error(f"{label} is not ISO-8601") from exc
    if result.tzinfo is None:
        raise DecisionModelV2Error(f"{label} must include timezone")
    return result.astimezone(timezone.utc)


def _exact_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise DecisionModelV2Error(f"{label} is missing")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise DecisionModelV2Error(f"{label} must be YYYY-MM-DD") from exc
    if result.isoformat() != value:
        raise DecisionModelV2Error(f"{label} must be canonical YYYY-MM-DD")
    return result


def _period_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise DecisionModelV2Error(f"{label} is missing")
    try:
        parsed = datetime.strptime(value, "%b %Y")
    except ValueError as exc:
        raise DecisionModelV2Error(f"{label} must be 'Mon YYYY'") from exc
    if parsed.month == 12:
        next_month = date(parsed.year + 1, 1, 1)
    else:
        next_month = date(parsed.year, parsed.month + 1, 1)
    return date.fromordinal(next_month.toordinal() - 1)


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise DecisionModelV2Error(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DecisionModelV2Error(f"{label} must be numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise DecisionModelV2Error(f"{label} is outside the allowed range")
    return result


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _file_sha256(path)}


def _generated_before(value: dict[str, Any], decision_time: datetime, label: str) -> str:
    generated = value.get("generatedAt")
    if _timestamp(generated, f"{label}.generatedAt") > decision_time:
        raise DecisionModelV2Error(f"{label} contains future data")
    return str(generated)


def load_config(path: str | Path) -> dict[str, Any]:
    config = _json_object(path)
    if config.get("model_version") != MODEL_VERSION or config.get("policy_version") != POLICY_VERSION:
        raise DecisionModelV2Error("Decision Model v2 config version mismatch")
    allowed = {
        "model_version", "policy_version", "ticker", "minimum_peers",
        "forward_period_tolerance_days", "tukey_iqr_multiplier", "growth_revision",
        "regime", "buy_ladder_multipliers", "reduction_review_multiplier",
    }
    if set(config) != allowed or config.get("ticker") != EXPECTED_TICKER:
        raise DecisionModelV2Error("Decision Model v2 config shape mismatch")
    if config.get("minimum_peers") != 5 or config.get("forward_period_tolerance_days") != 90:
        raise DecisionModelV2Error("Decision Model v2 peer gate policy mismatch")
    ladder = config.get("buy_ladder_multipliers")
    if ladder != [0.97, 0.93, 0.89]:
        raise DecisionModelV2Error("Decision Model v2 buy ladder policy mismatch")
    if _number(config.get("reduction_review_multiplier"), "reduction multiplier") != 1.15:
        raise DecisionModelV2Error("Decision Model v2 reduction policy mismatch")
    return config


def _latest_price(price_data: dict[str, Any], ticker: str, decision_time: datetime) -> dict[str, Any]:
    rows = (price_data.get("records") or {}).get(ticker)
    if not isinstance(rows, list) or not rows:
        raise DecisionModelV2Error(f"{ticker} price history is missing")
    usable: list[tuple[date, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _exact_date(row.get("date"), f"{ticker} price date")
        if day <= decision_time.date():
            usable.append((day, _number(row.get("close"), f"{ticker} close", positive=True)))
    if not usable:
        raise DecisionModelV2Error(f"{ticker} has no price before decision_time")
    day, close = max(usable, key=lambda item: item[0])
    return {"value": close, "as_of": day.isoformat()}


def _semiconductor_universe(industry_data: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    records = industry_data.get("records") or {}
    if not isinstance(records, dict):
        raise DecisionModelV2Error("industry records are invalid")
    for ticker, row in records.items():
        if not isinstance(ticker, str) or not isinstance(row, dict):
            continue
        sector = str(row.get("sector") or "")
        if row.get("etf") == "SOXX" or "반도체" in sector or "포토닉스" in sector:
            result.add(ticker)
    return result


def _latest_valuation(
    rows: Any, *, market_session: date, expected_period: str
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(rows, list):
        return None, "valuation_history_missing"
    same_session: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            as_of = _exact_date(row.get("asOf"), "valuation asOf")
        except DecisionModelV2Error:
            continue
        if as_of > market_session:
            continue
        if as_of == market_session:
            same_session.append(row)
    if not same_session:
        return None, "same_session_valuation_missing"
    exact = [row for row in same_session if row.get("estimatePeriod") == expected_period]
    if not exact:
        return None, "valuation_period_mismatch"
    row = exact[-1]
    try:
        _number(row.get("expectedPe"), "expectedPe", positive=True)
    except DecisionModelV2Error:
        return None, "forward_multiple_invalid"
    return row, None


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _tukey(peers: list[dict[str, Any]], multiplier: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    values = [float(row["forward_per"]) for row in peers]
    q1 = _percentile(values, 0.25)
    q3 = _percentile(values, 0.75)
    iqr = q3 - q1
    low = q1 - multiplier * iqr
    high = q3 + multiplier * iqr
    kept = [row for row in peers if low <= float(row["forward_per"]) <= high]
    removed = [dict(row, exclusion_reason="tukey_1_5_iqr_outlier") for row in peers if row not in kept]
    return kept, removed, {"q1": q1, "q3": q3, "iqr": iqr, "lower_fence": low, "upper_fence": high}


def _growth_and_revision(row: dict[str, Any], label: str) -> tuple[float, float]:
    current = _number(row.get("consensusEps"), f"{label} consensus EPS", positive=True)
    next_eps = _number(row.get("nextConsensusEps"), f"{label} next consensus EPS", positive=True)
    analysts = int(_number(row.get("analystCount"), f"{label} analyst count", positive=True))
    up = _number(row.get("revisionsUp4w", 0), f"{label} revisions up")
    down = _number(row.get("revisionsDown4w", 0), f"{label} revisions down")
    return next_eps / current - 1.0, (up - down) / analysts


def _regime_adjustment(macro: dict[str, Any], config: dict[str, Any]) -> tuple[float, list[str]]:
    score = macro.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise DecisionModelV2Error("macro score is invalid")
    rules = config["regime"]
    bounded_score = max(-int(rules["maximum_absolute_score"]), min(int(rules["maximum_absolute_score"]), int(score)))
    adjustment = _clamp(bounded_score * float(rules["score_step"]), float(rules["maximum_adjustment"]))
    reasons = macro.get("reasons")
    return adjustment, [str(value) for value in reasons] if isinstance(reasons, list) else []


def _decision_calculation_sha256(decision: dict[str, Any]) -> str:
    return _canonical_sha256({key: value for key, value in decision.items() if key != "calculation_sha256"})


def build_proxy_decision(
    *,
    price_history_path: str | Path,
    consensus_path: str | Path,
    valuation_history_path: str | Path,
    industry_map_path: str | Path,
    macro_path: str | Path,
    config_path: str | Path,
    decision_time: str,
) -> dict[str, Any]:
    paths = {
        "price_history": Path(price_history_path),
        "consensus": Path(consensus_path),
        "valuation_history": Path(valuation_history_path),
        "industry_map": Path(industry_map_path),
        "macro": Path(macro_path),
        "config": Path(config_path),
    }
    config = load_config(paths["config"])
    decision_at = _timestamp(decision_time, "decision_time")
    price_data = _json_object(paths["price_history"])
    consensus_data = _json_object(paths["consensus"])
    valuation_data = _json_object(paths["valuation_history"])
    industry_data = _json_object(paths["industry_map"])
    macro_data = _json_object(paths["macro"])
    generated_at = {
        role: _generated_before(data, decision_at, role)
        for role, data in {
            "price_history": price_data,
            "consensus": consensus_data,
            "valuation_history": valuation_data,
            "industry_map": industry_data,
            "macro": macro_data,
        }.items()
    }
    price = _latest_price(price_data, EXPECTED_TICKER, decision_at)
    market_session = _exact_date(price["as_of"], "market_session")
    records = consensus_data.get("records") or {}
    target = records.get(EXPECTED_TICKER) if isinstance(records, dict) else None
    if not isinstance(target, dict) or target.get("collectionStatus") != "success":
        raise DecisionModelV2Error("NVDA consensus is unavailable")
    if target.get("providerId") != EXPECTED_PROVIDER:
        raise DecisionModelV2Error("NVDA consensus provider mismatch")
    collected_at = _timestamp(target.get("collectedAt"), "NVDA consensus available_at")
    if collected_at > decision_at:
        raise DecisionModelV2Error("NVDA consensus is later than decision_time")
    target_period = _period_date(target.get("fiscalEnd"), "NVDA fiscalEnd")
    target_growth, target_revision = _growth_and_revision(target, EXPECTED_TICKER)
    eps = {
        "low": _number(target.get("lowEps"), "NVDA low EPS", positive=True),
        "base": _number(target.get("consensusEps"), "NVDA consensus EPS", positive=True),
        "high": _number(target.get("highEps"), "NVDA high EPS", positive=True),
    }
    if not eps["low"] <= eps["base"] <= eps["high"]:
        raise DecisionModelV2Error("NVDA EPS range is invalid")

    sector = _semiconductor_universe(industry_data)
    pre: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    valuation_records = valuation_data.get("records") or {}
    tolerance = int(config["forward_period_tolerance_days"])
    for ticker in sorted(sector):
        if ticker == EXPECTED_TICKER:
            excluded.append({"ticker": ticker, "reason": "target_ticker_excluded"})
            continue
        row = records.get(ticker) if isinstance(records, dict) else None
        if not isinstance(row, dict):
            excluded.append({"ticker": ticker, "reason": "consensus_missing"})
            continue
        if row.get("collectionStatus") != "success" or row.get("providerId") != EXPECTED_PROVIDER:
            excluded.append({"ticker": ticker, "reason": "provider_or_collection_mismatch"})
            continue
        try:
            available = _timestamp(row.get("collectedAt"), f"{ticker} collectedAt")
            period = _period_date(row.get("fiscalEnd"), f"{ticker} fiscalEnd")
        except DecisionModelV2Error as exc:
            excluded.append({"ticker": ticker, "reason": str(exc)})
            continue
        if available > decision_at:
            excluded.append({"ticker": ticker, "reason": "future_consensus"})
            continue
        if row.get("collectedAt") != target.get("collectedAt"):
            excluded.append({"ticker": ticker, "reason": "consensus_as_of_mismatch"})
            continue
        difference = abs((period - target_period).days)
        if difference > tolerance:
            excluded.append({"ticker": ticker, "reason": "forward_period_outside_90_days", "period_gap_days": difference})
            continue
        if row.get("currency") != target.get("currency"):
            excluded.append({"ticker": ticker, "reason": "currency_mismatch"})
            continue
        valuation, reason = _latest_valuation(
            valuation_records.get(ticker) if isinstance(valuation_records, dict) else None,
            market_session=market_session,
            expected_period=str(row.get("fiscalEnd")),
        )
        if valuation is None:
            excluded.append({"ticker": ticker, "reason": reason})
            continue
        try:
            growth, revision = _growth_and_revision(row, ticker)
        except DecisionModelV2Error as exc:
            excluded.append({"ticker": ticker, "reason": str(exc)})
            continue
        pre.append({
            "ticker": ticker,
            "forward_per": _number(valuation.get("expectedPe"), f"{ticker} expectedPe", positive=True),
            "price_as_of": str(valuation.get("asOf")),
            "forward_period": str(row.get("fiscalEnd")),
            "period_gap_days": difference,
            "eps_growth": growth,
            "revision_score": revision,
            "accounting_basis": row.get("accountingBasis"),
            "eps_basis": row.get("epsBasis"),
            "point_in_time_verified": False,
        })
    minimum = int(config["minimum_peers"])
    if len(pre) < minimum:
        raise DecisionModelV2Error(f"proxy peer gate blocked before outlier filter: {len(pre)}/{minimum}")
    post, outliers, fences = _tukey(pre, float(config["tukey_iqr_multiplier"]))
    excluded.extend({"ticker": row["ticker"], "reason": row["exclusion_reason"], "forward_per": row["forward_per"]} for row in outliers)
    if len(post) < minimum:
        raise DecisionModelV2Error(f"proxy peer gate blocked after outlier filter: {len(post)}/{minimum}")

    peer_median = statistics.median(float(row["forward_per"]) for row in post)
    peer_growth = statistics.median(float(row["eps_growth"]) for row in post)
    peer_revision = statistics.median(float(row["revision_score"]) for row in post)
    rules = config["growth_revision"]
    growth_component = _clamp(
        (target_growth - peer_growth) * float(rules["growth_coefficient"]),
        float(rules["growth_component_limit"]),
    )
    revision_component = _clamp(
        (target_revision - peer_revision) * float(rules["revision_coefficient"]),
        float(rules["revision_component_limit"]),
    )
    combined = _clamp(growth_component + revision_component, float(rules["combined_limit"]))
    after_growth = peer_median * (1.0 + combined)
    regime_adjustment, regime_reasons = _regime_adjustment(macro_data, config)
    target_multiple = after_growth * (1.0 + regime_adjustment)
    values = {key: value * target_multiple for key, value in eps.items()}
    ladder = [values["base"] * float(multiplier) for multiplier in config["buy_ladder_multipliers"]]
    reduction = values["base"] * float(config["reduction_review_multiplier"])
    current = float(price["value"])
    if current <= ladder[0]:
        action = "분할매수 시작"
        action_text = f"현재 가격에서 1차 매수를 시작 · ${ladder[0]:,.2f}/${ladder[1]:,.2f}/${ladder[2]:,.2f} 3회 분할"
    elif current < values["base"]:
        action = "대기"
        action_text = f"현재는 매수하지 말고 ${ladder[0]:,.2f}까지 기다림"
    elif current >= reduction:
        action = "신규 매수 중단·비중 축소 검토"
        action_text = f"${reduction:,.2f} 이상에서는 신규 매수 중단·비중 축소 검토"
    else:
        action = "대기"
        action_text = f"현재는 기준 연구값 아래 ${ladder[0]:,.2f}까지 기다림"
    first_buy_multiplier = float(config["buy_ladder_multipliers"][0])
    invalidation_eps = current / (target_multiple * first_buy_multiplier)
    sources = {role: _source(path) for role, path in paths.items()}
    decision: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "policy_version": POLICY_VERSION,
        "ticker": EXPECTED_TICKER,
        "decision_time": decision_at.isoformat().replace("+00:00", "Z"),
        "generated_at": decision_at.isoformat().replace("+00:00", "Z"),
        "decision_status": "proxy_action",
        "proxy_classification": "provider_homogeneous_proxy",
        "confidence": {"label": "낮음", "score": 35, "reason": "회계 basis와 원본 PIT가 미검증된 동일 공급자 프록시"},
        "operational_eligible": False,
        "verified_action": None,
        "current_price": {**price, "available_at": generated_at["price_history"]},
        "consensus": {
            "period": str(target.get("fiscalEnd")), "available_at": str(target.get("collectedAt")),
            "provider": EXPECTED_PROVIDER, "accounting_basis": target.get("accountingBasis"),
            "eps_basis": target.get("epsBasis"), "eps": eps,
        },
        "peer_gate": {
            "minimum": minimum, "period_tolerance_days": tolerance,
            "pre_outlier_count": len(pre), "post_outlier_count": len(post),
            "passed": True, "pre_outlier_peers": pre, "post_outlier_peers": post,
            "excluded_peers": excluded, "tukey": fences,
            "point_in_time_verified": False,
        },
        "calculation": {
            "current_price_used_in_target_multiple": False,
            "peer_median_before_adjustment": peer_median,
            "target_eps_growth": target_growth, "peer_median_eps_growth": peer_growth,
            "growth_adjustment": growth_component,
            "target_revision_score": target_revision, "peer_median_revision_score": peer_revision,
            "revision_adjustment": revision_component,
            "combined_growth_revision_adjustment": combined,
            "multiple_after_growth_revision": after_growth,
            "regime": {"state": macro_data.get("state"), "score": macro_data.get("score"), "reasons": regime_reasons,
                       "adjustment": regime_adjustment, "available_at": generated_at["macro"]},
            "target_forward_multiple": target_multiple,
        },
        "scenario_values": values,
        "buy_prices": ladder,
        "reduction_review_price": reduction,
        "current_implied_forward_multiple": current / eps["base"],
        "action": action,
        "action_text": action_text,
        "invalidation_conditions": [
            f"기준 Forward EPS가 ${invalidation_eps:,.2f} 아래로 하향되면 현재가 기준 1차 분할 조건 무효",
            "동일 공급자 피어가 5개 미만이거나 기준일·기간 허용 범위를 벗어나면 proxy_action 무효",
            "시장 국면 조정이 바뀌면 새 버전 신호로 재계산",
        ],
        "performance_tracking": {
            "status": "pending", "return_6m": None, "return_12m": None,
            "excess_return_qqq_6m": None, "excess_return_soxx_6m": None,
            "maximum_drawdown": None, "transaction_cost": None,
            "hit": None, "decision_change_count": 0, "no_lookahead": True,
        },
        "source_generated_at": generated_at,
        "sources": sources,
    }
    decision["calculation_sha256"] = _decision_calculation_sha256(decision)
    return decision


def validate_decision(decision: dict[str, Any]) -> dict[str, Any]:
    if decision.get("model_version") != MODEL_VERSION or decision.get("policy_version") != POLICY_VERSION:
        raise DecisionModelV2Error("Decision Model v2 artifact version mismatch")
    if decision.get("decision_status") != "proxy_action" or decision.get("proxy_classification") != "provider_homogeneous_proxy":
        raise DecisionModelV2Error("Decision Model v2 proxy state mismatch")
    if decision.get("operational_eligible") is not False or decision.get("verified_action") is not None:
        raise DecisionModelV2Error("Decision Model v2 cannot be operational")
    if decision.get("calculation_sha256") != _decision_calculation_sha256(decision):
        raise DecisionModelV2Error("Decision Model v2 calculation SHA-256 mismatch")
    sources = decision.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"price_history", "consensus", "valuation_history", "industry_map", "macro", "config"}:
        raise DecisionModelV2Error("Decision Model v2 source manifest mismatch")
    for role, source in sources.items():
        if not isinstance(source, dict):
            raise DecisionModelV2Error(f"{role} source is invalid")
        path = Path(str(source.get("path")))
        if not path.is_file() or _file_sha256(path) != source.get("sha256"):
            raise DecisionModelV2Error(f"{role} source hash mismatch")
    rebuilt = build_proxy_decision(
        price_history_path=sources["price_history"]["path"],
        consensus_path=sources["consensus"]["path"],
        valuation_history_path=sources["valuation_history"]["path"],
        industry_map_path=sources["industry_map"]["path"],
        macro_path=sources["macro"]["path"],
        config_path=sources["config"]["path"],
        decision_time=str(decision.get("decision_time") or ""),
    )
    if rebuilt != decision:
        raise DecisionModelV2Error("Decision Model v2 artifact does not reproduce from pinned sources")
    if decision.get("calculation", {}).get("current_price_used_in_target_multiple") is not False:
        raise DecisionModelV2Error("current price contaminated target multiple")
    peers = decision.get("peer_gate", {}).get("post_outlier_peers")
    if not isinstance(peers, list) or len(peers) < 5 or decision.get("peer_gate", {}).get("passed") is not True:
        raise DecisionModelV2Error("Decision Model v2 peer gate mismatch")
    base = _number(decision.get("scenario_values", {}).get("base"), "base value", positive=True)
    ladder = decision.get("buy_prices")
    if not isinstance(ladder, list) or len(ladder) != 3:
        raise DecisionModelV2Error("Decision Model v2 buy ladder mismatch")
    expected = [base * 0.97, base * 0.93, base * 0.89]
    if any(not math.isclose(_number(actual, "buy price"), value, rel_tol=1e-12) for actual, value in zip(ladder, expected)):
        raise DecisionModelV2Error("Decision Model v2 buy ladder arithmetic mismatch")
    if not math.isclose(_number(decision.get("reduction_review_price"), "reduction price"), base * 1.15, rel_tol=1e-12):
        raise DecisionModelV2Error("Decision Model v2 reduction arithmetic mismatch")
    return decision


def load_decision(path: str | Path) -> dict[str, Any]:
    return validate_decision(_json_object(path))


def write_decision_and_ledger(
    *, decision: dict[str, Any], decision_path: str | Path, ledger_path: str | Path
) -> tuple[Path, Path]:
    validate_decision(decision)
    decision_path = Path(decision_path)
    ledger_path = Path(ledger_path)
    atomic_write_json(decision_path, decision)
    signal_id = _canonical_sha256({
        "model_version": MODEL_VERSION,
        "decision_time": decision["decision_time"],
        "calculation_sha256": decision["calculation_sha256"],
        "source_hashes": {key: value["sha256"] for key, value in decision["sources"].items()},
    })
    record = {
        "signal_id": signal_id, "ticker": EXPECTED_TICKER,
        "decision_time": decision["decision_time"], "model_version": MODEL_VERSION,
        "decision_status": "proxy_action", "action": decision["action"],
        "current_price": decision["current_price"], "scenario_values": decision["scenario_values"],
        "buy_prices": decision["buy_prices"], "reduction_review_price": decision["reduction_review_price"],
        "input_sha256": {key: value["sha256"] for key, value in decision["sources"].items()},
        "calculation_sha256": decision["calculation_sha256"],
        "performance": decision["performance_tracking"], "operational_eligible": False,
    }
    if ledger_path.exists():
        ledger = _json_object(ledger_path)
        if ledger.get("ledger_version") != LEDGER_VERSION or not isinstance(ledger.get("records"), list):
            raise DecisionModelV2Error("Decision Model v2 ledger is invalid")
    else:
        ledger = {"ledger_version": LEDGER_VERSION, "records": []}
    existing = next((row for row in ledger["records"] if row.get("signal_id") == signal_id), None)
    if existing is not None and existing != record:
        raise DecisionModelV2Error("Decision Model v2 ledger signal collision")
    if existing is None:
        ledger["records"].append(record)
        atomic_write_json(ledger_path, ledger)
    return decision_path, ledger_path
