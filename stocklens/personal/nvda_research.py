"""Offline P5.1 recovery for partially valid NVDA estimate evidence.

The source bytes and their capture manifests remain immutable.  This module
creates a separate row-level adjudication, a research-only read model, and a
provisional research decision.  None of these outputs are operational inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .alpha_evidence_validation import validate_dataset_bytes
from .decision_model import calculate_target_multiple, load_decision_config
from .storage import atomic_write_json


ADJUDICATION_VERSION = "alpha-vantage-partial-estimates-adjudication-p5.1-v1"
READ_MODEL_VERSION = "nvda-forward-consensus-read-model-p5.1-v1"
RESEARCH_DECISION_VERSION = "nvda-provisional-research-decision-p5.1-v1"
PROVIDER_SCHEMA_VERSION = "alpha-vantage-estimates-array-v1"
EXPECTED_TICKER = "NVDA"
EXPECTED_FUNCTION = "EARNINGS_ESTIMATES"
EXPECTED_REQUEST_ID = "nvda-estimates"
HISTORICAL_CLASSIFICATION = "current_snapshot_records_for_past_periods"
FORWARD_EVIDENCE_CLASS = "current_forward_consensus_snapshot"


class NvdaResearchError(ValueError):
    """Raised when immutable evidence or a research-only input is invalid."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return _sha256(_canonical_bytes(value))


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NvdaResearchError(f"invalid JSON evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise NvdaResearchError(f"JSON evidence is not an object: {path.name}")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise NvdaResearchError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NvdaResearchError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise NvdaResearchError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _exact_date(value: Any, label: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise NvdaResearchError(f"{label} is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise NvdaResearchError(f"{label} is invalid") from exc
    if parsed.isoformat() != value:
        raise NvdaResearchError(f"{label} is invalid")
    return parsed


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or value is None:
        raise NvdaResearchError(f"{label} is not numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NvdaResearchError(f"{label} is not numeric") from exc
    if not math.isfinite(parsed) or (positive and parsed <= 0):
        raise NvdaResearchError(f"{label} is outside the accepted range")
    return parsed


def _optional_nonnegative_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    parsed = _number(value, label)
    if parsed < 0 or not parsed.is_integer():
        raise NvdaResearchError(f"{label} is not a nonnegative integer")
    return int(parsed)


def _manifest_and_ledger(
    raw_path: Path,
    manifest_path: Path,
    ledger_path: Path,
) -> tuple[bytes, dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw_bytes = raw_path.read_bytes()
    raw = json.loads(raw_bytes.decode("utf-8-sig"))
    manifest = _json_object(manifest_path)
    ledger = _json_object(ledger_path)
    if not isinstance(raw, dict):
        raise NvdaResearchError("provider response root is not an object")
    raw_hash = _sha256(raw_bytes)
    if manifest.get("response_sha256") != raw_hash:
        raise NvdaResearchError("capture manifest does not match provider bytes")
    if manifest.get("byte_length") != len(raw_bytes):
        raise NvdaResearchError("capture manifest byte length mismatch")
    if manifest.get("request_id") != EXPECTED_REQUEST_ID:
        raise NvdaResearchError("capture manifest request_id mismatch")
    if manifest.get("function") != EXPECTED_FUNCTION or manifest.get("ticker") != EXPECTED_TICKER:
        raise NvdaResearchError("capture manifest function/ticker mismatch")
    results = ledger.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise NvdaResearchError("continuation ledger must contain exactly one result")
    result = results[0]
    linked = (
        "request_id", "function", "ticker", "response_sha256", "byte_length",
        "content_type", "response_completed_at",
    )
    if any(result.get(key) != manifest.get(key) for key in linked):
        raise NvdaResearchError("continuation ledger does not match capture manifest")
    if ledger.get("calls_attempted") != 1 or ledger.get("calls_completed") != 1:
        raise NvdaResearchError("continuation ledger call counts are not exact")
    if ledger.get("automatic_retries") != 0 or ledger.get("redirects_allowed") is not False:
        raise NvdaResearchError("continuation ledger retry/redirect policy mismatch")
    if any(bool(ledger.get(key)) for key in (
        "parser_attempted", "candidate_created", "snapshot_created",
        "decision_created", "operational_eligible",
    )):
        raise NvdaResearchError("continuation ledger contains a forbidden promotion")
    return raw_bytes, raw, manifest, ledger


def _row_validation(
    row: Any,
    *,
    available_at: str,
    duplicate: bool,
) -> tuple[bool, str | None]:
    if duplicate:
        return False, "estimate_period_duplicate"
    if not isinstance(row, dict):
        return False, "estimate_row_not_object"
    payload = _canonical_bytes({"symbol": EXPECTED_TICKER, "estimates": [row]})
    result = validate_dataset_bytes(
        payload,
        "application/json",
        function=EXPECTED_FUNCTION,
        ticker=EXPECTED_TICKER,
        available_at=available_at,
    )
    return bool(result.get("valid_provider_dataset")), result.get("validation_code")


def adjudicate_partial_estimates(
    *,
    raw_path: str | Path,
    manifest_path: str | Path,
    ledger_path: str | Path,
    output_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Adjudicate each immutable row without editing or silently dropping it."""

    raw_path = Path(raw_path)
    manifest_path = Path(manifest_path)
    ledger_path = Path(ledger_path)
    raw_before = raw_path.read_bytes()
    manifest_before = manifest_path.read_bytes()
    ledger_before = ledger_path.read_bytes()
    raw_bytes, raw, manifest, _ledger = _manifest_and_ledger(
        raw_path, manifest_path, ledger_path,
    )
    if raw.get("symbol") != EXPECTED_TICKER:
        raise NvdaResearchError("provider symbol mismatch")
    if set(raw) != {"symbol", "estimates"} or not isinstance(raw.get("estimates"), list):
        raise NvdaResearchError("provider schema is not symbol + estimates[]")
    rows = raw["estimates"]
    if not rows:
        raise NvdaResearchError("provider estimate rows are empty")
    available_at = str(manifest.get("response_completed_at") or "")
    available_date = _timestamp(available_at, "available_at").date()

    keys: list[tuple[str | None, str | None]] = []
    for row in rows:
        if isinstance(row, dict):
            keys.append((row.get("date"), row.get("horizon")))
        else:
            keys.append((None, None))
    duplicates = {key for key in keys if keys.count(key) > 1}

    valid_records: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    future_valid = 0
    future_quarantined = 0
    for position, row in enumerate(rows, start=1):
        row_hash = _canonical_sha256(row)
        valid, reason = _row_validation(
            row,
            available_at=available_at,
            duplicate=keys[position - 1] in duplicates,
        )
        period_date: date | None = None
        if isinstance(row, dict):
            try:
                period_date = _exact_date(row.get("date"), f"row {position} date")
            except NvdaResearchError:
                period_date = None
        is_forward = bool(period_date and period_date > available_date)
        base = {
            "position": position,
            "date": row.get("date") if isinstance(row, dict) else None,
            "horizon": row.get("horizon") if isinstance(row, dict) else None,
            "canonical_sha256": row_hash,
            "is_forward_period": is_forward,
        }
        if valid:
            valid_records.append(base)
            future_valid += int(is_forward)
        else:
            quarantined.append({**base, "reason": reason or "estimate_row_invalid"})
            future_quarantined += int(is_forward)

    data_quality_status = "valid" if not quarantined else "partial_valid" if valid_records else "invalid"
    report = {
        "adjudication_version": ADJUDICATION_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ticker": EXPECTED_TICKER,
        "provider": "Alpha Vantage",
        "provider_schema_version": PROVIDER_SCHEMA_VERSION,
        "available_at": available_at,
        "source": {
            "raw_path": str(raw_path),
            "raw_sha256": _sha256(raw_bytes),
            "manifest_path": str(manifest_path),
            "manifest_sha256": _sha256(manifest_before),
            "continuation_ledger_path": str(ledger_path),
            "continuation_ledger_sha256": _sha256(ledger_before),
        },
        "capture_status": "captured",
        "schema_status": "valid",
        "data_quality_status": data_quality_status,
        "usage_status": "research_only",
        "row_counts": {
            "total": len(rows),
            "valid": len(valid_records),
            "quarantined": len(quarantined),
            "future_valid": future_valid,
            "future_quarantined": future_quarantined,
        },
        "valid_records": valid_records,
        "quarantined_records": quarantined,
        "source_bytes_modified": False,
        "accounting_basis": "unknown",
        "historical_availability_verified": False,
        "parser_candidate": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    if raw_path.read_bytes() != raw_before or manifest_path.read_bytes() != manifest_before or ledger_path.read_bytes() != ledger_before:
        raise NvdaResearchError("immutable source evidence changed during adjudication")
    if output_path is not None:
        atomic_write_json(Path(output_path), report)
    return report


def build_consensus_read_model(
    *,
    raw_path: str | Path,
    adjudication: dict[str, Any],
    adjudication_sha256: str,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Transform only adjudicated valid rows into a research-only read model."""

    raw_path = Path(raw_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8-sig"))
    if _file_sha256(raw_path) != adjudication.get("source", {}).get("raw_sha256"):
        raise NvdaResearchError("read model raw hash mismatch")
    valid_positions = {
        int(row["position"]): row
        for row in adjudication.get("valid_records", [])
        if isinstance(row, dict) and isinstance(row.get("position"), int)
    }
    available_at = str(adjudication.get("available_at") or "")
    available_date = _timestamp(available_at, "available_at").date()
    records: list[dict[str, Any]] = []
    for position, row in enumerate(raw["estimates"], start=1):
        if position not in valid_positions:
            continue
        period_date = _exact_date(row.get("date"), f"row {position} date")
        is_forward = period_date > available_date
        records.append({
            "position": position,
            "period_date": row["date"],
            "horizon": row["horizon"],
            "eps": {
                "low": _number(row["eps_estimate_low"], "eps low"),
                "average": _number(row["eps_estimate_average"], "eps average"),
                "high": _number(row["eps_estimate_high"], "eps high"),
                "analyst_count": int(_number(row["eps_estimate_analyst_count"], "eps analyst count")),
                "revision_up_7d": _optional_nonnegative_integer(row.get("eps_estimate_revision_up_trailing_7_days"), "revision up 7d"),
                "revision_down_7d": _optional_nonnegative_integer(row.get("eps_estimate_revision_down_trailing_7_days"), "revision down 7d"),
                "revision_up_30d": _optional_nonnegative_integer(row.get("eps_estimate_revision_up_trailing_30_days"), "revision up 30d"),
                "revision_down_30d": _optional_nonnegative_integer(row.get("eps_estimate_revision_down_trailing_30_days"), "revision down 30d"),
            },
            "revenue": {
                "low": _number(row["revenue_estimate_low"], "revenue low"),
                "average": _number(row["revenue_estimate_average"], "revenue average"),
                "high": _number(row["revenue_estimate_high"], "revenue high"),
                "analyst_count": int(_number(row["revenue_estimate_analyst_count"], "revenue analyst count")),
            },
            "source": "Alpha Vantage",
            "source_hash": adjudication["source"]["raw_sha256"],
            "available_at": available_at,
            "accounting_basis": "unknown",
            "evidence_class": FORWARD_EVIDENCE_CLASS if is_forward else HISTORICAL_CLASSIFICATION,
            "historical_record_classification": None if is_forward else HISTORICAL_CLASSIFICATION,
            "historical_availability_verified": False,
            "research_eligible": is_forward,
            "backtest_eligible": False,
            "historical_per_distribution_eligible": False,
            "operational_eligible": False,
            "canonical_sha256": valid_positions[position]["canonical_sha256"],
        })
    forward_records = [row for row in records if row["research_eligible"]]
    model = {
        "read_model_version": READ_MODEL_VERSION,
        "ticker": EXPECTED_TICKER,
        "provider": "Alpha Vantage",
        "source_hash": adjudication["source"]["raw_sha256"],
        "adjudication_sha256": adjudication_sha256,
        "available_at": available_at,
        "capture_status": "captured",
        "schema_status": "valid",
        "data_quality_status": adjudication["data_quality_status"],
        "usage_status": "research_only",
        "accounting_basis": "unknown",
        "historical_availability_verified": False,
        "fy1_ntm_mapping_eligible": False,
        "records": records,
        "forward_records": forward_records,
        "row_counts": adjudication["row_counts"],
        "quarantined_records": adjudication["quarantined_records"],
        "source_bytes_modified": False,
        "parser_candidate": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    if len(records) != adjudication["row_counts"]["valid"] or len(forward_records) != adjudication["row_counts"]["future_valid"]:
        raise NvdaResearchError("read model row counts do not match adjudication")
    if output_path is not None:
        atomic_write_json(Path(output_path), model)
    return model


def _latest_unadjusted_price(raw_path: Path, manifest_path: Path) -> dict[str, Any]:
    raw_bytes = raw_path.read_bytes()
    manifest = _json_object(manifest_path)
    if manifest.get("response_sha256") != _sha256(raw_bytes):
        raise NvdaResearchError("daily price manifest hash mismatch")
    if manifest.get("validation_code") != "valid_unadjusted_daily":
        raise NvdaResearchError("daily price is not validated unadjusted data")
    value = json.loads(raw_bytes.decode("utf-8-sig"))
    metadata = value.get("Meta Data")
    series = value.get("Time Series (Daily)")
    if not isinstance(metadata, dict) or not isinstance(series, dict) or not series:
        raise NvdaResearchError("daily price structure is incomplete")
    symbol = metadata.get("2. Symbol") or metadata.get("Symbol")
    if str(symbol).upper() != EXPECTED_TICKER:
        raise NvdaResearchError("daily price ticker mismatch")
    latest_date = max(series, key=lambda item: _exact_date(item, "daily price date"))
    latest = series[latest_date]
    return {
        "ticker": EXPECTED_TICKER,
        "value": _number(latest.get("4. close"), "latest close", positive=True),
        "as_of": latest_date,
        "available_at": manifest.get("available_at"),
        "record_count": len(series),
        "adjustment_policy": "as_traded_unadjusted",
        "source": "Alpha Vantage TIME_SERIES_DAILY",
        "source_hash": _sha256(raw_bytes),
        "manifest_sha256": _file_sha256(manifest_path),
        "research_eligible": True,
        "backtest_eligible": False,
        "operational_eligible": False,
    }


def _regime_adjustment(macro: dict[str, Any], config: dict[str, Any]) -> tuple[float, list[str]]:
    indicators = macro.get("indicators")
    if not isinstance(indicators, dict):
        raise NvdaResearchError("market proxy indicators are missing")
    rules = config["regime_rules"]
    trace: list[str] = []
    adjustment = 0.0
    qqq = indicators.get("qqq") or {}
    vix = indicators.get("vix") or {}
    hyg = indicators.get("hyg") or {}
    ten_year = indicators.get("tenYear") or {}
    if qqq.get("above20") is True and qqq.get("above50") is True:
        amount = float(rules["qqq_above_20_50"])
        adjustment += amount
        trace.append(f"QQQ 20·50일선 상회 {amount:+.1%}")
    if vix.get("above20") is False and vix.get("above50") is False:
        amount = float(rules["vix_below_20_50"])
        adjustment += amount
        trace.append(f"VIX 20·50일선 하회 {amount:+.1%}")
    if hyg.get("above20") is False and hyg.get("above50") is False:
        amount = float(rules["hyg_below_20_50"])
        adjustment += amount
        trace.append(f"HYG 20·50일선 하회 {amount:+.1%}")
    if ten_year.get("above20") is True and ten_year.get("above50") is True:
        amount = float(rules["ten_year_above_20_50"])
        adjustment += amount
        trace.append(f"10년물 20·50일선 상회 {amount:+.1%}")
    limit = float(config["regime_adjustment_limit"])
    adjustment = max(-limit, min(limit, adjustment))
    trace.append(f"합계 {adjustment:+.1%}, 허용 범위 ±{limit:.0%}")
    return adjustment, trace


def _research_multiple_proxy(value: dict[str, Any]) -> dict[str, Any]:
    records = value.get("records")
    if isinstance(records, dict):
        records = records.get(EXPECTED_TICKER)
    if not isinstance(records, list):
        raise NvdaResearchError("valuation history records are missing")
    accepted: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        if row.get("ticker") not in (None, EXPECTED_TICKER):
            continue
        if "컨센서스 EPS" not in str(row.get("estimateKind") or ""):
            continue
        expected_pe = row.get("expectedPe")
        try:
            parsed = _number(expected_pe, "expectedPe", positive=True)
            _exact_date(row.get("asOf"), "valuation asOf")
        except NvdaResearchError:
            continue
        accepted.append({"as_of": row["asOf"], "expected_pe": parsed})
    if not accepted:
        raise NvdaResearchError("no local cross-project multiple proxy is available")
    accepted.sort(key=lambda row: row["as_of"])
    return {
        "value": statistics.median(row["expected_pe"] for row in accepted),
        "sample_count": len(accepted),
        "period_start": accepted[0]["as_of"],
        "period_end": accepted[-1]["as_of"],
        "status": "cross_project_research_proxy",
        "point_in_time_verified": False,
        "independent_benchmark_verified": False,
        "records": accepted,
    }


def build_provisional_research_decision(
    *,
    read_model: dict[str, Any],
    read_model_sha256: str,
    price_raw_path: str | Path,
    price_manifest_path: str | Path,
    macro_proxy_path: str | Path,
    valuation_history_path: str | Path,
    config_path: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build an actionable but non-operational research decision."""

    if read_model.get("ticker") != EXPECTED_TICKER or read_model.get("usage_status") != "research_only":
        raise NvdaResearchError("research read model identity/status mismatch")
    if read_model.get("accounting_basis") != "unknown" or read_model.get("operational_eligible") is not False:
        raise NvdaResearchError("research read model must retain unknown basis and operational block")
    forward = read_model.get("forward_records")
    if not isinstance(forward, list) or not forward:
        raise NvdaResearchError("no valid forward research records are available")
    annual = sorted(
        (row for row in forward if row.get("horizon") == "fiscal year"),
        key=lambda row: row.get("period_date", ""),
    )
    if not annual:
        raise NvdaResearchError("no future fiscal-year record is available")
    selected = annual[0]
    price = _latest_unadjusted_price(Path(price_raw_path), Path(price_manifest_path))
    macro_path = Path(macro_proxy_path)
    valuation_path = Path(valuation_history_path)
    macro = _json_object(macro_path)
    valuation = _json_object(valuation_path)
    config = load_decision_config(config_path)
    proxy = _research_multiple_proxy(valuation)
    regime_adjustment, regime_trace = _regime_adjustment(macro, config)
    axes = {
        "self_history_forward_per": None,
        "growth_revision": None,
        "market_sector_premium": proxy["value"],
    }
    target_per, combination = calculate_target_multiple(
        axes,
        config["weights"],
        regime_adjustment=regime_adjustment,
    )
    eps = selected["eps"]
    eps_low = _number(eps.get("low"), "selected EPS low", positive=True)
    eps_base = _number(eps.get("average"), "selected EPS average", positive=True)
    eps_high = _number(eps.get("high"), "selected EPS high", positive=True)
    current_price = price["value"]
    pre_regime_value = eps_base * combination["pre_regime_per"]
    values = {
        "low": eps_low * target_per,
        "base": eps_base * target_per,
        "high": eps_high * target_per,
    }
    current_forward_multiple = current_price / eps_base
    base_gap = current_price / values["base"] - 1.0
    discounts = (0.05, 0.085, 0.12)
    wait_ladder = [round(current_price * (1.0 - value), 2) for value in discounts]
    if current_price >= values["base"] * 1.15:
        action = "비중축소"
        action_text = (
            f"잠정 연구 기준값 대비 15% 이상 높습니다. 신규 매수를 중단하고 비중 축소를 검토하며 "
            f"${wait_ladder[0]:,.2f} 이하에서만 다시 평가합니다."
        )
        buy_prices = wait_ladder
    elif current_price <= values["base"] * 0.95:
        action = "분할매수"
        buy_prices = [round(current_price * (1.0 - value), 2) for value in (0.0, 0.04, 0.08)]
        action_text = (
            f"잠정 연구 기준상 현재 가격에서 1차를 시작하고 "
            f"${buy_prices[0]:,.2f}/${buy_prices[1]:,.2f}/${buy_prices[2]:,.2f}에서 3회 분할합니다."
        )
    else:
        action = "대기"
        buy_prices = wait_ladder
        action_text = (
            f"현재는 대기하고 ${buy_prices[0]:,.2f}부터 분할을 시작합니다. "
            f"잠정 연구 분할구간은 ${buy_prices[0]:,.2f}/${buy_prices[1]:,.2f}/${buy_prices[2]:,.2f}입니다."
        )
    span = (buy_prices[0] - buy_prices[-1]) / current_price
    if not 0.05 <= span <= 0.12:
        raise NvdaResearchError("research buy ladder span is outside 5%-12% of current price")
    eps_cut = eps_base * (1.0 - float(config["invalidation_rules"]["eps_cut"]))
    result = {
        "research_decision_version": RESEARCH_DECISION_VERSION,
        "ticker": EXPECTED_TICKER,
        "decision_time": read_model["available_at"],
        "decision_status": "provisional_research",
        "confidence": "낮음",
        "confidence_reasons": [
            "Alpha Vantage accounting basis가 unknown",
            "독립 Forward PER 원장이 없어 현재 시장가격에서 파생된 cross-project 자기 배수 프록시 한 축만 사용",
            "가격은 비조정 일봉이며 장기 성과·배당 수익률에 사용할 수 없음",
        ],
        "current_price": price,
        "selected_forward_period": {
            "period_date": selected["period_date"],
            "horizon": selected["horizon"],
            "available_at": selected["available_at"],
            "accounting_basis": "unknown",
            "eps": eps,
        },
        "current_implied_forward_multiple": current_forward_multiple,
        "multiple_proxy": proxy,
        "axis_availability": {
            "self_history_forward_per": {"value": None, "included": False, "reason": "verified PIT Forward PER ledger unavailable"},
            "growth_revision": {"value": None, "included": False, "reason": "unknown accounting basis blocks a growth-derived multiple"},
            "market_sector_premium": {
                "value": proxy["value"],
                "included": True,
                "reason": "local Top10 current-implied self-multiple proxy; not an independent sector benchmark",
            },
        },
        "weight_trace": {
            "configured": config["weights"],
            "normalized_available_only": combination["normalized_weights"],
        },
        "regime": {
            "source": "Nasdaq Top10 cross_project_market_proxy",
            "source_hash": _file_sha256(macro_path),
            "as_of": macro.get("generatedAt"),
            "state": macro.get("state"),
            "score": macro.get("score"),
            "adjustment": regime_adjustment,
            "trace": regime_trace,
        },
        "pre_regime_target_per": combination["pre_regime_per"],
        "target_per_after_regime": target_per,
        "pre_regime_base_value": pre_regime_value,
        "provisional_research_values": values,
        "provisional_research_base_value": values["base"],
        "current_price_vs_base": base_gap,
        "research_action": action,
        "research_action_text": action_text,
        "provisional_research_buy_prices": buy_prices,
        "buy_ladder_span_of_current_price": span,
        "invalidation_condition": {
            "eps_average_below": eps_cut,
            "rule": "selected fiscal-year average EPS falls 10% below the captured base estimate",
        },
        "accounting_basis": "unknown",
        "accounting_basis_impact": (
            "GAAP/Non-GAAP 불확실성을 가격 할인으로 임의 환산하지 않았습니다. 대신 신뢰도를 낮음으로 제한하고 "
            "운영 판단을 차단했습니다."
        ),
        "circularity_notice": (
            "사용 가능한 배수 축은 현재 가격과 같은 컨센서스에서 파생된 자기 배수 프록시입니다. 따라서 기준값은 "
            "독립 적정가가 아니라 현재 가격과 EPS 시나리오의 조건부 일관성 점검입니다."
        ),
        "calculation_basis": (
            "nearest future fiscal-year EPS low/base/high × available local current-implied self-multiple proxy; "
            "unavailable axes are excluded and remaining configured weight is renormalized, followed by the "
            "existing regime overlay; this is not an independent fair-value estimate"
        ),
        "source_hashes": {
            "consensus_read_model": read_model_sha256,
            "consensus_raw": read_model["source_hash"],
            "price_raw": price["source_hash"],
            "price_manifest": price["manifest_sha256"],
            "macro_proxy": _file_sha256(macro_path),
            "valuation_history_proxy": _file_sha256(valuation_path),
            "decision_config": config["config_hash"],
            "decision_config_file": _file_sha256(Path(config_path)),
        },
        "source_paths": {
            "price_raw": str(Path(price_raw_path)),
            "price_manifest": str(Path(price_manifest_path)),
            "macro_proxy": str(macro_path),
            "valuation_history_proxy": str(valuation_path),
            "decision_config": str(Path(config_path)),
        },
        "research_eligible": True,
        "backtest_eligible": False,
        "operational_decision": None,
        "operational_eligible": False,
    }
    result["calculation_sha256"] = _canonical_sha256(result)
    if output_path is not None:
        atomic_write_json(Path(output_path), result)
    return result


def load_research_artifacts(
    *,
    adjudication_path: str | Path,
    read_model_path: str | Path,
    decision_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load and re-link all P5.1 artifacts before rendering them."""

    adjudication_path = Path(adjudication_path)
    read_model_path = Path(read_model_path)
    decision_path = Path(decision_path)
    adjudication = _json_object(adjudication_path)
    read_model = _json_object(read_model_path)
    decision = _json_object(decision_path)
    if adjudication.get("adjudication_version") != ADJUDICATION_VERSION:
        raise NvdaResearchError("P5.1 adjudication version mismatch")
    if read_model.get("read_model_version") != READ_MODEL_VERSION:
        raise NvdaResearchError("P5.1 read model version mismatch")
    if decision.get("research_decision_version") != RESEARCH_DECISION_VERSION:
        raise NvdaResearchError("P5.1 research decision version mismatch")
    read_hash = _file_sha256(read_model_path)
    if read_model.get("adjudication_sha256") != _file_sha256(adjudication_path):
        raise NvdaResearchError("P5.1 read model/adjudication hash mismatch")
    if decision.get("source_hashes", {}).get("consensus_read_model") != read_hash:
        raise NvdaResearchError("P5.1 decision/read model hash mismatch")
    source = adjudication.get("source") or {}
    for path_key, hash_key in (
        ("raw_path", "raw_sha256"),
        ("manifest_path", "manifest_sha256"),
        ("continuation_ledger_path", "continuation_ledger_sha256"),
    ):
        source_path = Path(str(source.get(path_key) or ""))
        if not source_path.is_file() or _file_sha256(source_path) != source.get(hash_key):
            raise NvdaResearchError(f"P5.1 immutable source mismatch: {path_key}")
    decision_paths = decision.get("source_paths") or {}
    decision_hashes = decision.get("source_hashes") or {}
    for path_key, hash_key in (
        ("price_raw", "price_raw"),
        ("price_manifest", "price_manifest"),
        ("macro_proxy", "macro_proxy"),
        ("valuation_history_proxy", "valuation_history_proxy"),
        ("decision_config", "decision_config_file"),
    ):
        source_path = Path(str(decision_paths.get(path_key) or ""))
        if not source_path.is_file() or _file_sha256(source_path) != decision_hashes.get(hash_key):
            raise NvdaResearchError(f"P5.1 research decision source mismatch: {path_key}")
    if any(bool(value.get("operational_eligible")) for value in (adjudication, read_model, decision)):
        raise NvdaResearchError("P5.1 artifact attempted an operational promotion")
    if decision.get("operational_decision") is not None:
        raise NvdaResearchError("P5.1 operational decision must remain absent")
    return adjudication, read_model, decision
