"""P5.1.2 provider-homogeneous peer diagnostics for NVDA.

Alpha Vantage evidence is used only for an internal integrity and row-quality
audit until an explicit rights approval receipt exists.  Nasdaq Top10 data can
produce descriptive implied-multiple sensitivity, but a target multiple and
action are emitted only after the stricter accounting-basis, period, point-in-
time, same-session, and peer-count gates all pass.  The current local evidence
does not pass those gates.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .nvda_research import (
    ADJUDICATION_VERSION as P51_ADJUDICATION_VERSION,
    READ_MODEL_VERSION as P51_READ_MODEL_VERSION,
    RESEARCH_DECISION_VERSION as P51_DECISION_VERSION,
    NvdaResearchError,
)
from .storage import atomic_write_json


SUPERSESSION_VERSION = "nvda-p5.1.1-decision-supersession-p5.1.2-v1"
PEER_PROXY_VERSION = "nvda-provider-homogeneous-proxy-p5.1.2-v1"
RESEARCH_DECISION_VERSION = "nvda-non-action-research-state-p5.1.2-v1"
TARGET_MULTIPLE_POLICY_VERSION = "nvda-provider-homogeneous-multiple-policy-p5.1.2-v1"
EXPECTED_TICKER = "NVDA"
MINIMUM_PEERS = 5
FALLBACK_MULTIPLES = (20.0, 22.0, 24.0)
WITHDRAWN_FIELDS = (
    "provisional_research_base_value",
    "provisional_research_buy_prices",
    "research_action_text",
    "first_review_rule",
)
UNVERIFIED_BASIS_VALUES = {None, "", "unknown", "unspecified", "provider_adjusted_unspecified"}


class NvdaResearchP511Error(ValueError):
    """Raised when P5.1.2 evidence cannot be used safely."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(payload)


def _json_object(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NvdaResearchP511Error(f"invalid JSON evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise NvdaResearchP511Error(f"JSON evidence must be an object: {path.name}")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise NvdaResearchP511Error(f"{label} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise NvdaResearchP511Error(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise NvdaResearchP511Error(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc)


def _exact_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise NvdaResearchP511Error(f"{label} is missing")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise NvdaResearchP511Error(f"{label} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise NvdaResearchP511Error(f"{label} must be canonical YYYY-MM-DD")
    return parsed


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise NvdaResearchP511Error(f"{label} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NvdaResearchP511Error(f"{label} must be numeric") from exc
    if not math.isfinite(parsed) or (positive and parsed <= 0):
        raise NvdaResearchP511Error(f"{label} is outside the allowed range")
    return parsed


def _require_before(value: Any, decision_time: datetime, label: str) -> str:
    parsed = _timestamp(value, label)
    if parsed > decision_time:
        raise NvdaResearchP511Error(f"{label} is later than decision_time")
    return str(value)


def _resolve_linked(private_root: Path, relative: Any, expected_hash: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise NvdaResearchP511Error(f"{label} relative path is missing")
    candidate = (private_root / relative).resolve()
    root = private_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise NvdaResearchP511Error(f"{label} path escapes private_data")
    if not candidate.is_file() or _file_sha256(candidate) != expected_hash:
        raise NvdaResearchP511Error(f"{label} hash mismatch")
    return candidate


def _validate_alpha_policy(
    *,
    private_root: Path,
    terms_review_path: Path,
    supplemental_adjudication_path: Path,
) -> dict[str, Any]:
    terms = _json_object(terms_review_path)
    supplemental = _json_object(supplemental_adjudication_path)
    if terms.get("review_version") != "alpha-vantage-terms-review-p4.3.1-v1":
        raise NvdaResearchP511Error("Alpha Vantage terms review version mismatch")
    if terms.get("operational_eligible") is not False:
        raise NvdaResearchP511Error("Alpha Vantage terms review must remain non-operational")
    scopes = terms.get("scope_assessments") or {}
    derived_status = (scopes.get("derived_analysis") or {}).get("status")
    display_status = (scopes.get("local_display") or {}).get("status")
    if derived_status != "manual_review_required":
        raise NvdaResearchP511Error("Alpha Vantage derived-analysis policy changed")
    if display_status != "manual_review_required":
        raise NvdaResearchP511Error("Alpha Vantage local-display policy changed")
    if terms.get("public_display_permission") != "not_authorized":
        raise NvdaResearchP511Error("Alpha Vantage public-display policy must remain not_authorized")
    receipt = terms.get("approval_receipt")
    receipt_scopes = set(receipt.get("approved_scopes") or []) if isinstance(receipt, dict) else set()
    receipt_valid = bool(
        isinstance(receipt, dict)
        and receipt.get("status") == "approved"
        and isinstance(receipt.get("approved_by"), str)
        and receipt.get("approved_by").strip()
        and isinstance(receipt.get("approved_at"), str)
        and receipt.get("approved_at").strip()
    )
    derived_authorized = receipt_valid and "derived_analysis" in receipt_scopes
    display_authorized = receipt_valid and "local_display" in receipt_scopes
    document = terms.get("terms_document") or {}
    _resolve_linked(
        private_root,
        document.get("relative_path"),
        document.get("pdf_sha256"),
        "Alpha Vantage terms document",
    )

    if supplemental.get("adjudication_version") != "alpha-vantage-supplemental-adjudication-p5.0.2-v1":
        raise NvdaResearchP511Error("supplemental adjudication version mismatch")
    if supplemental.get("operational_eligible") is not False:
        raise NvdaResearchP511Error("supplemental adjudication must remain non-operational")
    requests = supplemental.get("requests")
    if not isinstance(requests, list):
        raise NvdaResearchP511Error("supplemental request evidence is missing")
    daily = next(
        (
            row
            for row in requests
            if isinstance(row, dict)
            and row.get("request_id") == "nvda-daily"
            and row.get("ticker") == EXPECTED_TICKER
            and row.get("function") == "TIME_SERIES_DAILY"
        ),
        None,
    )
    if daily is None:
        raise NvdaResearchP511Error("NVDA daily supplemental adjudication is missing")
    required_prohibitions = {"forward_per", "valuation", "trading_decision"}
    if not required_prohibitions.issubset(set(daily.get("prohibited_uses") or [])):
        raise NvdaResearchP511Error("NVDA daily prohibited-use contract is incomplete")
    if daily.get("allowed_use") != "recent_price_flow_and_integrity_research_only":
        raise NvdaResearchP511Error("NVDA daily allowed-use contract changed")
    _resolve_linked(
        private_root,
        daily.get("source_relative_path"),
        daily.get("source_hash"),
        "NVDA daily raw",
    )
    _resolve_linked(
        private_root,
        daily.get("capture_manifest_relative_path"),
        daily.get("capture_manifest_hash"),
        "NVDA daily capture manifest",
    )
    return {
        "terms_review_sha256": _file_sha256(terms_review_path),
        "supplemental_adjudication_sha256": _file_sha256(supplemental_adjudication_path),
        "alpha_vantage_usage": "internal_integrity_and_row_quality_audit_only",
        "prohibited_uses": sorted(required_prohibitions),
        "approval_receipt_present": receipt_valid,
        "derived_analysis_authorized": derived_authorized,
        "local_display_authorized": display_authorized,
        "derived_analysis_status": derived_status,
        "local_display_status": display_status,
        "public_display_permission": "not_authorized",
        "operational_eligible": False,
    }


def _validate_p51_chain(
    *,
    p51_adjudication_path: Path,
    p51_read_model_path: Path,
    p51_decision_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    adjudication = _json_object(p51_adjudication_path)
    read_model = _json_object(p51_read_model_path)
    old_decision = _json_object(p51_decision_path)
    if adjudication.get("adjudication_version") != P51_ADJUDICATION_VERSION:
        raise NvdaResearchP511Error("P5.1 adjudication version mismatch")
    if read_model.get("read_model_version") != P51_READ_MODEL_VERSION:
        raise NvdaResearchP511Error("P5.1 read model version mismatch")
    if old_decision.get("research_decision_version") != P51_DECISION_VERSION:
        raise NvdaResearchP511Error("P5.1 decision version mismatch")
    if read_model.get("adjudication_sha256") != _file_sha256(p51_adjudication_path):
        raise NvdaResearchP511Error("P5.1 read model/adjudication hash mismatch")
    if (old_decision.get("source_hashes") or {}).get("consensus_read_model") != _file_sha256(p51_read_model_path):
        raise NvdaResearchP511Error("P5.1 decision/read model hash mismatch")
    counts = adjudication.get("row_counts") or {}
    if counts != {"total": 41, "valid": 40, "quarantined": 1, "future_valid": 4, "future_quarantined": 0}:
        raise NvdaResearchP511Error("P5.1 row-count contract mismatch")
    if read_model.get("accounting_basis") != "unknown":
        raise NvdaResearchP511Error("P5.1 accounting basis must remain unknown")
    if any(item.get("operational_eligible") is not False for item in (adjudication, read_model, old_decision)):
        raise NvdaResearchP511Error("P5.1 chain attempted operational promotion")
    return adjudication, read_model, old_decision


def _latest_top10_price(price_history: dict[str, Any], decision_time: datetime) -> dict[str, Any]:
    generated = _require_before(price_history.get("generatedAt"), decision_time, "price.available_at")
    records = (price_history.get("records") or {}).get(EXPECTED_TICKER)
    if not isinstance(records, list):
        raise NvdaResearchP511Error("Top10 NVDA price history is missing")
    allowed: list[tuple[date, float]] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        day = _exact_date(row.get("date"), "price date")
        if day <= decision_time.date():
            allowed.append((day, _number(row.get("close"), "price close", positive=True)))
    if not allowed:
        raise NvdaResearchP511Error("no Top10 price is available before decision_time")
    day, close = max(allowed, key=lambda item: item[0])
    return {
        "value": close,
        "as_of": day.isoformat(),
        "available_at": generated,
        "source": "Nasdaq Top10 local price-history snapshot",
        "source_role": "cross_project_price_proxy",
        "operational_eligible": False,
    }


def _top10_consensus_input(consensus: dict[str, Any], decision_time: datetime) -> dict[str, Any]:
    generated = _require_before(consensus.get("generatedAt"), decision_time, "consensus.available_at")
    row = (consensus.get("records") or {}).get(EXPECTED_TICKER)
    if not isinstance(row, dict) or row.get("collectionStatus") != "success":
        raise NvdaResearchP511Error("Top10 NVDA consensus is unavailable")
    _require_before(row.get("collectedAt"), decision_time, "NVDA consensus collectedAt")
    if row.get("fiscalEnd") != "Jan 2027":
        raise NvdaResearchP511Error("NVDA consensus fiscal period mismatch")
    return {
        "period": row["fiscalEnd"],
        "low": _number(row.get("lowEps"), "NVDA consensus low EPS", positive=True),
        "average": _number(row.get("consensusEps"), "NVDA consensus average EPS", positive=True),
        "high": _number(row.get("highEps"), "NVDA consensus high EPS", positive=True),
        "analyst_count": int(_number(row.get("analystCount"), "NVDA consensus analyst count")),
        "revisions_up_4w": int(_number(row.get("revisionsUp4w"), "NVDA revisions up")),
        "revisions_down_4w": int(_number(row.get("revisionsDown4w"), "NVDA revisions down")),
        "accounting_basis": row.get("accountingBasis"),
        "eps_basis": row.get("epsBasis"),
        "available_at": generated,
        "source": "Nasdaq Top10 local consensus snapshot",
        "operational_eligible": False,
    }


def _semiconductor_tickers(industry_map: dict[str, Any]) -> list[str]:
    records = industry_map.get("records")
    if not isinstance(records, dict):
        raise NvdaResearchP511Error("industry map records are missing")
    tickers = []
    for ticker, row in records.items():
        if not isinstance(row, dict):
            continue
        if row.get("etf") == "SOXX" or "반도체" in str(row.get("sector") or ""):
            tickers.append(str(ticker))
    return sorted(set(tickers))


def _latest_valuation_row(rows: Any, session: date) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(rows, list):
        return None, []
    candidates: list[tuple[date, dict[str, Any]]] = []
    excluded_future: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            as_of = _exact_date(row.get("asOf"), "valuation row asOf")
        except NvdaResearchP511Error:
            continue
        if as_of > session:
            excluded_future.append({"as_of": as_of.isoformat(), "reason": "future_valuation_row"})
            continue
        candidates.append((as_of, row))
    if not candidates:
        return None, excluded_future
    exact = [item for item in candidates if item[0] == session]
    selected = max(exact or candidates, key=lambda item: item[0])[1]
    return selected, excluded_future


def _tukey_filter(values: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float] | None]:
    if len(values) < MINIMUM_PEERS:
        return values[:], [], None
    ordered = sorted(float(row["expected_pe"]) for row in values)
    midpoint = len(ordered) // 2
    lower = ordered[:midpoint]
    upper = ordered[-midpoint:]
    q1 = statistics.median(lower)
    q3 = statistics.median(upper)
    iqr = q3 - q1
    low = q1 - 1.5 * iqr
    high = q3 + 1.5 * iqr
    kept = [row for row in values if low <= float(row["expected_pe"]) <= high]
    removed = [row for row in values if row not in kept]
    return kept, removed, {"q1": q1, "q3": q3, "low": low, "high": high}


def _explicit_basis(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in UNVERIFIED_BASIS_VALUES


def build_peer_proxy(
    *,
    consensus: dict[str, Any],
    valuation_history: dict[str, Any],
    industry_map: dict[str, Any],
    decision_time: datetime,
    market_session: date,
) -> dict[str, Any]:
    """Build a price-independent peer multiple or an explicit blocked result."""

    _require_before(consensus.get("generatedAt"), decision_time, "consensus.generatedAt")
    valuation_generated = _require_before(
        valuation_history.get("generatedAt"), decision_time, "valuation-history.generatedAt"
    )
    _require_before(industry_map.get("generatedAt"), decision_time, "industry-map.generatedAt")
    consensus_records = consensus.get("records") or {}
    valuation_records = valuation_history.get("records") or {}
    nvda = consensus_records.get(EXPECTED_TICKER) or {}
    target_period = nvda.get("fiscalEnd")
    target_basis = nvda.get("accountingBasis")
    target_eps_basis = nvda.get("epsBasis")
    target_basis_explicit = _explicit_basis(target_basis) and _explicit_basis(target_eps_basis)
    target_pit_verified = nvda.get("providerTimestampVerified") is True
    accepted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    future_rows: list[dict[str, Any]] = []

    for ticker in _semiconductor_tickers(industry_map):
        if ticker == EXPECTED_TICKER:
            excluded.append({"ticker": ticker, "reason": "target_company_excluded"})
            continue
        peer = consensus_records.get(ticker)
        if not isinstance(peer, dict) or peer.get("collectionStatus") != "success":
            excluded.append({"ticker": ticker, "reason": "consensus_unavailable"})
            continue
        try:
            _require_before(peer.get("collectedAt"), decision_time, f"{ticker} collectedAt")
        except NvdaResearchP511Error as exc:
            excluded.append({"ticker": ticker, "reason": str(exc)})
            continue
        if peer.get("fiscalEnd") != target_period:
            excluded.append({
                "ticker": ticker,
                "reason": "fiscal_period_mismatch",
                "peer_period": peer.get("fiscalEnd"),
                "required_period": target_period,
            })
            continue
        if not target_basis_explicit:
            excluded.append({"ticker": ticker, "reason": "target_forward_basis_unverified"})
            continue
        if not _explicit_basis(peer.get("accountingBasis")) or not _explicit_basis(peer.get("epsBasis")):
            excluded.append({"ticker": ticker, "reason": "peer_forward_basis_unverified"})
            continue
        if peer.get("accountingBasis") != target_basis or peer.get("epsBasis") != target_eps_basis:
            excluded.append({"ticker": ticker, "reason": "forward_eps_basis_mismatch"})
            continue
        row, row_future = _latest_valuation_row(valuation_records.get(ticker), market_session)
        future_rows.extend({"ticker": ticker, **item} for item in row_future)
        if row is None:
            excluded.append({"ticker": ticker, "reason": "valuation_row_unavailable"})
            continue
        if row.get("asOf") != market_session.isoformat():
            excluded.append({"ticker": ticker, "reason": "same_session_valuation_missing"})
            continue
        if not target_pit_verified or peer.get("providerTimestampVerified") is not True:
            excluded.append({"ticker": ticker, "reason": "consensus_point_in_time_unverified"})
            continue
        if row.get("pointInTimeVerified") is not True:
            excluded.append({"ticker": ticker, "reason": "valuation_point_in_time_unverified"})
            continue
        if row.get("estimatePeriod") != target_period or "Nasdaq 컨센서스 EPS" not in str(row.get("estimateKind") or ""):
            excluded.append({"ticker": ticker, "reason": "valuation_basis_or_period_mismatch"})
            continue
        try:
            multiple = _number(row.get("expectedPe"), f"{ticker} expectedPe", positive=True)
        except NvdaResearchP511Error:
            excluded.append({"ticker": ticker, "reason": "forward_multiple_invalid"})
            continue
        accepted.append({
            "ticker": ticker,
            "expected_pe": multiple,
            "as_of": row["asOf"],
            "estimate_period": row["estimatePeriod"],
            "accounting_basis": peer.get("accountingBasis"),
            "eps_basis": peer.get("epsBasis"),
        })

    kept, outliers, bounds = _tukey_filter(accepted)
    gate_checks = {
        "target_basis_explicit": target_basis_explicit,
        "target_point_in_time_verified": target_pit_verified,
        "minimum_pre_outlier_peers": len(accepted) >= MINIMUM_PEERS,
        "minimum_post_outlier_peers": len(kept) >= MINIMUM_PEERS,
        "no_future_valuation_rows": not future_rows,
        "same_price_session": all(row.get("as_of") == market_session.isoformat() for row in kept),
        "comparable_forward_period": all(row.get("estimate_period") == target_period for row in kept),
        "current_price_excluded_from_target": True,
    }
    eligible = all(gate_checks.values())
    failed_gate_reasons = [name for name, passed in gate_checks.items() if not passed]
    return {
        "peer_proxy_version": PEER_PROXY_VERSION,
        "status": "provider_homogeneous_proxy",
        "ticker": EXPECTED_TICKER,
        "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
        "market_session": market_session.isoformat(),
        "valuation_generated_at": valuation_generated,
        "required_forward_period": target_period,
        "required_accounting_basis": target_basis,
        "required_eps_basis": target_eps_basis,
        "target_basis_explicit": target_basis_explicit,
        "target_point_in_time_verified": target_pit_verified,
        "minimum_peer_count": MINIMUM_PEERS,
        "pre_outlier_peers": accepted,
        "pre_outlier_median": statistics.median(row["expected_pe"] for row in accepted) if accepted else None,
        "outlier_rule": "Tukey 1.5×IQR; applied only when at least five basis-matched peers exist",
        "outlier_bounds": bounds,
        "outliers_removed": outliers,
        "post_outlier_peers": kept,
        "post_outlier_median": statistics.median(row["expected_pe"] for row in kept) if eligible else None,
        "excluded_peers": excluded,
        "excluded_future_valuation_rows": future_rows,
        "peer_gate_passed": eligible,
        "gate_checks": gate_checks,
        "failed_gate_reasons": failed_gate_reasons,
        "block_reason": None if eligible else (
            "explicit-basis, point-in-time, same-session peers "
            f"{len(kept)}/{MINIMUM_PEERS}; failed={','.join(failed_gate_reasons)}"
        ),
        "point_in_time_verified": eligible,
        "current_price_used_in_target_multiple": False,
        "research_eligible": True,
        "operational_eligible": False,
    }


def _regime_adjustment(macro: dict[str, Any]) -> tuple[float, list[str]]:
    indicators = macro.get("indicators") or {}
    trace: list[str] = []
    adjustment = 0.0
    hyg = indicators.get("hyg") or {}
    if hyg.get("above20") is False and hyg.get("above50") is False:
        adjustment -= 0.03
        trace.append("HYG가 20·50일선 아래인 cross-project risk proxy: -3%")
    adjustment = max(-0.10, min(0.10, adjustment))
    trace.append(f"총 국면 조정 {adjustment:+.1%}; 연구 허용 범위 ±10%")
    return adjustment, trace


def build_p511_artifacts(
    *,
    private_root: str | Path,
    p51_adjudication_path: str | Path,
    p51_read_model_path: str | Path,
    p51_decision_path: str | Path,
    terms_review_path: str | Path,
    supplemental_adjudication_path: str | Path,
    price_history_path: str | Path,
    consensus_path: str | Path,
    valuation_history_path: str | Path,
    industry_map_path: str | Path,
    macro_path: str | Path,
    supersession_output_path: str | Path | None = None,
    peer_output_path: str | Path | None = None,
    decision_output_path: str | Path | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build P5.1.2 artifacts without changing P5.1/P5.1.1 evidence."""

    private_root = Path(private_root)
    p51_adjudication_path = Path(p51_adjudication_path)
    p51_read_model_path = Path(p51_read_model_path)
    p51_decision_path = Path(p51_decision_path)
    adjudication, read_model, old_decision = _validate_p51_chain(
        p51_adjudication_path=p51_adjudication_path,
        p51_read_model_path=p51_read_model_path,
        p51_decision_path=p51_decision_path,
    )
    alpha_policy = _validate_alpha_policy(
        private_root=private_root,
        terms_review_path=Path(terms_review_path),
        supplemental_adjudication_path=Path(supplemental_adjudication_path),
    )
    decision_time = _timestamp(read_model.get("available_at"), "decision_time")
    price_history = _json_object(price_history_path)
    consensus = _json_object(consensus_path)
    valuation = _json_object(valuation_history_path)
    industry = _json_object(industry_map_path)
    macro = _json_object(macro_path)
    _require_before(macro.get("generatedAt"), decision_time, "macro.generatedAt")
    price = _latest_top10_price(price_history, decision_time)
    market_session = _exact_date(price["as_of"], "market_session")
    top10_eps = _top10_consensus_input(consensus, decision_time)
    peer_proxy = build_peer_proxy(
        consensus=consensus,
        valuation_history=valuation,
        industry_map=industry,
        decision_time=decision_time,
        market_session=market_session,
    )
    regime_adjustment, regime_trace = _regime_adjustment(macro)
    current_implied = price["value"] / top10_eps["average"]
    scenario_table = [
        {
            "multiple": multiple,
            "price": top10_eps["average"] * multiple,
            "label": "explicit_multiple_sensitivity_only",
        }
        for multiple in FALLBACK_MULTIPLES
    ]

    target_multiple = peer_proxy["post_outlier_median"]
    pre_regime_target = target_multiple
    base_value = None
    sensitivity = None
    ladder = None
    action = None
    action_text = None
    valuation_signal_status = "not_generated"
    control_action = "no_new_position_signal"
    if peer_proxy["peer_gate_passed"]:
        target_multiple = float(target_multiple) * (1.0 + regime_adjustment)
        sensitivity = {
            "low": top10_eps["low"] * target_multiple,
            "base": top10_eps["average"] * target_multiple,
            "high": top10_eps["high"] * target_multiple,
        }
        base_value = sensitivity["base"]
        ladder = [round(base_value * factor, 2) for factor in (0.97, 0.93, 0.89)]
        span = (ladder[0] - ladder[-1]) / base_value
        if not 0.05 <= span <= 0.12:
            raise NvdaResearchP511Error("independent-base ladder is outside the 5%-12% range")
        if price["value"] <= ladder[0]:
            action = "분할매수"
            action_text = f"독립 피어 기준을 통과해 ${ladder[0]:,.2f}부터 3회 분할매수를 시작합니다."
        elif price["value"] >= base_value * 1.15:
            action = "비중축소"
            action_text = f"독립 피어 기준값보다 15% 이상 높아 신규 매수를 중단하고 비중 축소를 검토합니다."
        else:
            action = "대기"
            action_text = f"현재는 대기하고 ${ladder[0]:,.2f}부터 3회 분할매수를 시작합니다."
        valuation_signal_status = "provider_homogeneous_proxy_generated"
        control_action = "research_signal_available"

    generated_at = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    supersession = {
        "supersession_version": SUPERSESSION_VERSION,
        "generated_at": generated_at,
        "ticker": EXPECTED_TICKER,
        "status": "superseded_circular_price_anchor",
        "superseded_artifact": {
            "path": str(p51_decision_path),
            "sha256": _file_sha256(p51_decision_path),
            "version": old_decision["research_decision_version"],
        },
        "withdrawn_fields": list(WITHDRAWN_FIELDS),
        "superseded_versions": [P51_DECISION_VERSION, "nvda-conditional-research-decision-p5.1.1-v1"],
        "reason": (
            "P5.1 used a current-price-derived self multiple and P5.1.1 selected an unvalidated "
            "22x action threshold after the peer gate failed"
        ),
        "display_eligible": False,
        "operational_eligible": False,
    }
    if supersession_output_path is not None:
        atomic_write_json(Path(supersession_output_path), supersession)

    peer_proxy.update({
        "generated_at": generated_at,
        "source_hashes": {
            "consensus": _file_sha256(Path(consensus_path)),
            "valuation_history": _file_sha256(Path(valuation_history_path)),
            "industry_map": _file_sha256(Path(industry_map_path)),
        },
    })
    if peer_output_path is not None:
        atomic_write_json(Path(peer_output_path), peer_proxy)

    decision = {
        "research_decision_version": RESEARCH_DECISION_VERSION,
        "generated_at": generated_at,
        "ticker": EXPECTED_TICKER,
        "decision_time": read_model["available_at"],
        "decision_status": "research_inputs_incomplete",
        "current_price": price,
        "consensus_input": top10_eps,
        "alpha_vantage_internal_audit": {
            "available_at": read_model.get("available_at"),
            "capture_status": adjudication.get("capture_status"),
            "schema_status": adjudication.get("schema_status"),
            "data_quality_status": adjudication.get("data_quality_status"),
            "usage_status": adjudication.get("usage_status"),
            "row_counts": adjudication.get("row_counts"),
            "accounting_basis": read_model.get("accounting_basis"),
            "historical_availability_verified": read_model.get(
                "historical_availability_verified"
            ),
            "raw_unchanged": adjudication.get("source_bytes_modified") is False,
            "display_authorized": alpha_policy["local_display_authorized"],
            "derived_analysis_authorized": alpha_policy["derived_analysis_authorized"],
            "valuation_input": False,
            "trading_decision_input": False,
        },
        "alpha_vantage_policy": alpha_policy,
        "current_implied_forward_multiple": current_implied,
        "peer_proxy_status": peer_proxy["status"],
        "target_multiple_policy": {
            "policy_version": TARGET_MULTIPLE_POLICY_VERSION,
            "data_as_of": market_session.isoformat(),
            "peer_tickers": [row["ticker"] for row in peer_proxy["post_outlier_peers"]],
            "excluded_peers": peer_proxy["excluded_peers"],
            "period_normalization": "exact provider fiscalEnd match; no FY1/NTM conversion",
            "growth_revision_adjustment": "not applied unless comparable explicit basis is verified",
            "regime_adjustment_before_after": {
                "before": pre_regime_target,
                "adjustment": regime_adjustment,
                "after": target_multiple if peer_proxy["peer_gate_passed"] else None,
            },
            "current_price_used_in_target": False,
            "performance_measurement_plan": (
                "versioned signal cohorts; 6m/12m total return, QQQ/SOXX excess return, "
                "maximum drawdown, costs, and no look-ahead"
            ),
        },
        "peer_gate_passed": peer_proxy["peer_gate_passed"],
        "valuation_signal_status": valuation_signal_status,
        "control_action": control_action,
        "pre_regime_target_multiple": pre_regime_target,
        "growth_revision_adjustment": {
            "included": False,
            "value": None,
            "reason": "accounting basis is unknown; revisions are disclosed but not converted into a multiple",
        },
        "regime": {
            "source": "Nasdaq Top10 cross_project_market_proxy",
            "source_hash": _file_sha256(Path(macro_path)),
            "as_of": macro.get("generatedAt"),
            "adjustment": regime_adjustment,
            "applied_to_target": peer_proxy["peer_gate_passed"],
            "trace": regime_trace,
        },
        "independent_target_multiple": target_multiple if peer_proxy["peer_gate_passed"] else None,
        "independent_research_base_value": base_value,
        "fair_value": base_value,
        "eps_sensitivity": sensitivity,
        "conditional_multiple_table": scenario_table,
        "first_review_rule": None,
        "research_action": action,
        "research_action_text": action_text,
        "provisional_research_buy_prices": ladder,
        "buy_range": ladder,
        "sell_range": None,
        "buy_ladder_status": "available_from_independent_base" if ladder else "not_created_peer_gate_failed",
        "accounting_basis": "unknown",
        "pit_limit": "cross-project peer data are not independently point-in-time verified",
        "superseded_p51_status": "superseded_circular_price_anchor",
        "operational_decision": None,
        "operational_eligible": False,
        "source_hashes": {
            "p51_adjudication": _file_sha256(p51_adjudication_path),
            "p51_read_model": _file_sha256(p51_read_model_path),
            "p51_decision": _file_sha256(p51_decision_path),
            "p512_supersession": _file_sha256(Path(supersession_output_path)) if supersession_output_path else None,
            "p512_peer_proxy": _file_sha256(Path(peer_output_path)) if peer_output_path else None,
            "price_history": _file_sha256(Path(price_history_path)),
            "consensus": _file_sha256(Path(consensus_path)),
            "valuation_history": _file_sha256(Path(valuation_history_path)),
            "industry_map": _file_sha256(Path(industry_map_path)),
            "macro": _file_sha256(Path(macro_path)),
            "terms_review": _file_sha256(Path(terms_review_path)),
            "supplemental_adjudication": _file_sha256(Path(supplemental_adjudication_path)),
        },
        "source_paths": {
            "p51_adjudication": str(p51_adjudication_path),
            "p51_read_model": str(p51_read_model_path),
            "p51_decision": str(p51_decision_path),
            "p512_supersession": str(Path(supersession_output_path)) if supersession_output_path else None,
            "p512_peer_proxy": str(Path(peer_output_path)) if peer_output_path else None,
            "terms_review": str(Path(terms_review_path)),
            "supplemental_adjudication": str(Path(supplemental_adjudication_path)),
            "price_history": str(Path(price_history_path)),
            "consensus": str(Path(consensus_path)),
            "valuation_history": str(Path(valuation_history_path)),
            "industry_map": str(Path(industry_map_path)),
            "macro": str(Path(macro_path)),
        },
    }
    decision["calculation_sha256"] = _canonical_sha256(decision)
    if decision_output_path is not None:
        atomic_write_json(Path(decision_output_path), decision)
    return supersession, peer_proxy, decision


def load_p511_artifacts(
    *,
    supersession_path: str | Path,
    peer_proxy_path: str | Path,
    decision_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load P5.1.2 only; P5.1/P5.1.1 decision versions fail closed."""

    supersession_path = Path(supersession_path)
    peer_proxy_path = Path(peer_proxy_path)
    decision_path = Path(decision_path)
    supersession = _json_object(supersession_path)
    peer = _json_object(peer_proxy_path)
    decision = _json_object(decision_path)
    if supersession.get("supersession_version") != SUPERSESSION_VERSION:
        raise NvdaResearchP511Error("P5.1.2 supersession version mismatch")
    if supersession.get("status") != "superseded_circular_price_anchor":
        raise NvdaResearchP511Error("P5.1 circular decision was not superseded")
    if peer.get("peer_proxy_version") != PEER_PROXY_VERSION:
        raise NvdaResearchP511Error("P5.1.2 peer proxy version mismatch")
    if decision.get("research_decision_version") != RESEARCH_DECISION_VERSION:
        raise NvdaResearchP511Error("P5.1.2 decision version mismatch")
    hashes = decision.get("source_hashes") or {}
    if hashes.get("p512_supersession") != _file_sha256(supersession_path):
        raise NvdaResearchP511Error("P5.1.2 supersession hash mismatch")
    if hashes.get("p512_peer_proxy") != _file_sha256(peer_proxy_path):
        raise NvdaResearchP511Error("P5.1.2 peer proxy hash mismatch")
    paths = decision.get("source_paths") or {}
    for path_key, hash_key in (
        ("p51_adjudication", "p51_adjudication"),
        ("p51_read_model", "p51_read_model"),
        ("p51_decision", "p51_decision"),
        ("price_history", "price_history"),
        ("consensus", "consensus"),
        ("valuation_history", "valuation_history"),
        ("industry_map", "industry_map"),
        ("macro", "macro"),
        ("terms_review", "terms_review"),
        ("supplemental_adjudication", "supplemental_adjudication"),
    ):
        path = Path(str(paths.get(path_key) or ""))
        if not path.is_file() or _file_sha256(path) != hashes.get(hash_key):
            raise NvdaResearchP511Error(f"P5.1.2 source mismatch: {path_key}")
    if decision.get("superseded_p51_status") != "superseded_circular_price_anchor":
        raise NvdaResearchP511Error("P5.1.2 decision did not withdraw superseded action anchors")
    if decision.get("operational_decision") is not None or decision.get("operational_eligible") is not False:
        raise NvdaResearchP511Error("P5.1.2 attempted operational promotion")
    alpha_policy = decision.get("alpha_vantage_policy") or {}
    if alpha_policy.get("public_display_permission") != "not_authorized":
        raise NvdaResearchP511Error("P5.1.2 Alpha Vantage public-display policy mismatch")
    if alpha_policy.get("approval_receipt_present") is not True:
        if alpha_policy.get("derived_analysis_authorized") is not False:
            raise NvdaResearchP511Error("manual review was treated as derived-analysis approval")
        if alpha_policy.get("local_display_authorized") is not False:
            raise NvdaResearchP511Error("manual review was treated as local-display approval")
    if decision.get("peer_gate_passed") is not True:
        if any(
            decision.get(key) is not None
            for key in (
                "research_action",
                "first_review_rule",
                "fair_value",
                "buy_range",
                "sell_range",
                "independent_target_multiple",
            )
        ):
            raise NvdaResearchP511Error("P5.1.2 gate failure leaked a valuation or action")
        if decision.get("valuation_signal_status") != "not_generated":
            raise NvdaResearchP511Error("P5.1.2 gate failure status mismatch")
        if decision.get("control_action") != "no_new_position_signal":
            raise NvdaResearchP511Error("P5.1.2 gate failure control action mismatch")
    return supersession, peer, decision
