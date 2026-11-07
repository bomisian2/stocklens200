"""Offline evidence audit for Decision Model v2.1 consensus inputs.

The audit deliberately separates a research proxy from a verified price map.
Numerical agreement between two providers can increase proxy confidence, but it
cannot establish accounting basis, per-share normalization, point-in-time
history, or usage rights.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .storage import atomic_write_json


AUDIT_VERSION = "decision-model-v2.1-consensus-basis-audit-v1"
CONTRACT_VERSION = "decision-model-v2-consensus-source-contract-v1"
EXPECTED_TICKER = "NVDA"
EXPECTED_NASDAQ_PROVIDER = "nasdaq-analyst-earnings-forecast"
EXPECTED_ALPHA_SCHEMA = "alpha-vantage-estimates-array-v1"

REQUIRED_PROVIDER_FIELDS = (
    "provider_native_raw_bytes",
    "source_url",
    "source_hash",
    "captured_at",
    "available_at",
    "fiscal_period",
    "accounting_basis",
    "eps_share_basis",
    "split_adjustment",
    "adr_share_conversion",
    "currency",
    "usage_rights",
)

ACCOUNTING_BASES = {"gaap", "non_gaap", "adjusted", "unknown"}
EPS_SHARE_BASES = {"basic", "diluted", "unknown"}
SPLIT_POLICIES = {"adjusted", "unadjusted", "not_applicable", "unknown"}
SECURITY_FORMS = {"common_stock", "ordinary_share", "adr", "ads", "unknown"}
RIGHTS_STATES = {"authorized", "not_authorized", "manual_review_required", "unknown"}


class ConsensusBasisAuditError(ValueError):
    """Raised when offline evidence cannot be bound or classified safely."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    return _sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _read_json(path: str | Path, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    source = Path(path).resolve()
    if not source.is_file():
        raise ConsensusBasisAuditError(f"{label} is missing")
    raw = source.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConsensusBasisAuditError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ConsensusBasisAuditError(f"{label} root must be an object")
    return source, raw, value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ConsensusBasisAuditError(f"{label} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConsensusBasisAuditError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ConsensusBasisAuditError(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ConsensusBasisAuditError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConsensusBasisAuditError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ConsensusBasisAuditError(f"{label} is outside the allowed range")
    return number


def _official_nasdaq_forecast_url(value: Any, ticker: str) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.nasdaq.com"
        and parsed.path == f"/api/analyst/{ticker}/earnings-forecast"
        and not parsed.query
    )


def validate_consensus_source_contract(
    contract: dict[str, Any], *, require_verified: bool
) -> dict[str, Any]:
    """Validate the provider-neutral source contract.

    This validator does not turn descriptive strings into evidence.  Verified
    use requires the provider-native bytes and every unit/right field to be
    explicit and hash-bound.
    """

    allowed = {
        "contract_version",
        "provider",
        "ticker",
        "evidence_kind",
        "provider_native_raw_bytes",
        "source_url",
        "source_hash",
        "captured_at",
        "available_at",
        "fiscal_period",
        "accounting_basis",
        "eps_share_basis",
        "split_adjustment",
        "security_form",
        "adr_share_conversion",
        "currency",
        "usage_rights",
    }
    if set(contract) != allowed:
        raise ConsensusBasisAuditError("consensus source contract shape mismatch")
    if contract.get("contract_version") != CONTRACT_VERSION:
        raise ConsensusBasisAuditError("consensus source contract version mismatch")
    if contract.get("ticker") != EXPECTED_TICKER:
        raise ConsensusBasisAuditError("consensus source ticker mismatch")
    if contract.get("evidence_kind") != "street_consensus":
        raise ConsensusBasisAuditError(
            "issuer actuals or guidance cannot substitute for Street consensus"
        )
    if contract.get("accounting_basis") not in ACCOUNTING_BASES:
        raise ConsensusBasisAuditError("accounting_basis is invalid")
    if contract.get("eps_share_basis") not in EPS_SHARE_BASES:
        raise ConsensusBasisAuditError("eps_share_basis is invalid")
    if contract.get("split_adjustment") not in SPLIT_POLICIES:
        raise ConsensusBasisAuditError("split_adjustment is invalid")
    if contract.get("security_form") not in SECURITY_FORMS:
        raise ConsensusBasisAuditError("security_form is invalid")
    _timestamp(contract.get("captured_at"), "captured_at")
    _timestamp(contract.get("available_at"), "available_at")
    if not isinstance(contract.get("fiscal_period"), str) or not contract["fiscal_period"].strip():
        raise ConsensusBasisAuditError("fiscal_period is missing")
    if not isinstance(contract.get("currency"), str) or len(contract["currency"]) != 3:
        raise ConsensusBasisAuditError("currency must be an ISO-style three-letter code")
    if not isinstance(contract.get("source_hash"), str) or len(contract["source_hash"]) != 64:
        raise ConsensusBasisAuditError("source_hash is invalid")
    try:
        bytes.fromhex(contract["source_hash"])
    except ValueError as exc:
        raise ConsensusBasisAuditError("source_hash is invalid") from exc

    rights = contract.get("usage_rights")
    if not isinstance(rights, dict) or set(rights) != {
        "storage",
        "derived_analysis",
        "local_display",
        "public_display",
    }:
        raise ConsensusBasisAuditError("usage_rights shape mismatch")
    if any(value not in RIGHTS_STATES for value in rights.values()):
        raise ConsensusBasisAuditError("usage_rights contains an invalid state")

    conversion = contract.get("adr_share_conversion")
    if not isinstance(conversion, dict) or set(conversion) != {
        "status",
        "ratio",
        "source_hash",
    }:
        raise ConsensusBasisAuditError("adr_share_conversion shape mismatch")
    if conversion["status"] not in {"verified", "not_applicable", "unknown"}:
        raise ConsensusBasisAuditError("adr_share_conversion status is invalid")

    blockers: list[str] = []
    if contract.get("provider_native_raw_bytes") is not True:
        blockers.append("provider_native_raw_bytes_missing")
    if contract.get("accounting_basis") == "unknown":
        blockers.append("accounting_basis_unknown")
    if contract.get("eps_share_basis") == "unknown":
        blockers.append("eps_share_basis_unknown")
    if contract.get("split_adjustment") == "unknown":
        blockers.append("split_adjustment_unknown")
    if contract.get("security_form") == "unknown":
        blockers.append("security_form_unknown")
    if conversion["status"] == "unknown":
        blockers.append("adr_share_conversion_unknown")
    if any(rights[key] != "authorized" for key in ("storage", "derived_analysis", "local_display")):
        blockers.append("usage_rights_not_approved")
    if require_verified and blockers:
        raise ConsensusBasisAuditError(
            "verified consensus contract blocked: " + ", ".join(blockers)
        )
    return {"verified_eligible": not blockers, "blockers": blockers}


def _nasdaq_contract(record: dict[str, Any], source_hash: str) -> dict[str, Any]:
    collected = record.get("collectedAt")
    return {
        "contract_version": CONTRACT_VERSION,
        "provider": "Nasdaq Analyst Research",
        "ticker": EXPECTED_TICKER,
        "evidence_kind": "street_consensus",
        "provider_native_raw_bytes": False,
        "source_url": record.get("sourceUrl"),
        "source_hash": source_hash,
        "captured_at": collected,
        "available_at": collected,
        "fiscal_period": record.get("fiscalEnd"),
        "accounting_basis": "unknown",
        "eps_share_basis": "unknown",
        "split_adjustment": "unknown",
        "security_form": "unknown",
        "adr_share_conversion": {"status": "unknown", "ratio": None, "source_hash": None},
        "currency": record.get("currency"),
        "usage_rights": {
            "storage": "unknown",
            "derived_analysis": "unknown",
            "local_display": "unknown",
            "public_display": "not_authorized",
        },
    }


def audit_nvda_consensus_basis(
    *,
    nasdaq_snapshot_path: str | Path,
    nasdaq_collector_path: str | Path,
    alpha_raw_path: str | Path,
    alpha_manifest_path: str | Path,
    alpha_adjudication_path: str | Path,
    eps_unit_evidence_path: str | Path,
    generated_at: str,
) -> dict[str, Any]:
    """Build a hash-bound offline audit without creating a trading decision."""

    generated = _timestamp(generated_at, "generated_at")
    nasdaq_path, nasdaq_raw, nasdaq = _read_json(nasdaq_snapshot_path, "Nasdaq snapshot")
    alpha_path, alpha_raw, alpha = _read_json(alpha_raw_path, "Alpha Vantage raw response")
    alpha_manifest_file, alpha_manifest_raw, alpha_manifest = _read_json(
        alpha_manifest_path, "Alpha Vantage manifest"
    )
    alpha_adjudication_file, alpha_adjudication_raw, alpha_adjudication = _read_json(
        alpha_adjudication_path, "Alpha Vantage adjudication"
    )
    unit_path, unit_raw, unit_evidence = _read_json(eps_unit_evidence_path, "EPS unit evidence")
    collector_path = Path(nasdaq_collector_path).resolve()
    if not collector_path.is_file():
        raise ConsensusBasisAuditError("Nasdaq collector source is missing")
    collector_raw = collector_path.read_bytes()

    records = nasdaq.get("records")
    if not isinstance(records, dict) or not isinstance(records.get(EXPECTED_TICKER), dict):
        raise ConsensusBasisAuditError("NVDA Nasdaq consensus record is missing")
    nasdaq_record = records[EXPECTED_TICKER]
    if nasdaq_record.get("providerId") != EXPECTED_NASDAQ_PROVIDER:
        raise ConsensusBasisAuditError("NVDA Nasdaq provider mismatch")
    if not _official_nasdaq_forecast_url(nasdaq_record.get("sourceUrl"), EXPECTED_TICKER):
        raise ConsensusBasisAuditError("NVDA Nasdaq source URL is not approved")
    nasdaq_eps = _finite_number(
        nasdaq_record.get("consensusEps"), "NVDA Nasdaq consensus EPS", positive=True
    )
    _timestamp(nasdaq_record.get("collectedAt"), "NVDA Nasdaq collectedAt")
    nasdaq_hash = _sha256(nasdaq_raw)
    nasdaq_contract = _nasdaq_contract(nasdaq_record, nasdaq_hash)
    nasdaq_contract_result = validate_consensus_source_contract(
        nasdaq_contract, require_verified=False
    )

    collector_text = collector_raw.decode("utf-8", errors="replace")
    collector_declares_unknown = all(
        marker in collector_text
        for marker in (
            'epsBasis:"unknown"',
            'accountingBasis:"provider_adjusted_unspecified"',
            'shareBasis:"provider per-share; split/ADR normalization unverified"',
        )
    )
    if not collector_declares_unknown:
        raise ConsensusBasisAuditError("Nasdaq collector basis safeguards are missing")

    if alpha_manifest.get("response_sha256", "").upper() != _sha256(alpha_raw):
        raise ConsensusBasisAuditError("Alpha Vantage raw hash does not match manifest")
    adjudication_source = alpha_adjudication.get("source")
    if not isinstance(adjudication_source, dict):
        raise ConsensusBasisAuditError("Alpha Vantage adjudication source binding is missing")
    if adjudication_source.get("raw_sha256", "").upper() != _sha256(alpha_raw):
        raise ConsensusBasisAuditError("Alpha Vantage adjudication raw hash mismatch")
    if adjudication_source.get("manifest_sha256", "").upper() != _sha256(alpha_manifest_raw):
        raise ConsensusBasisAuditError("Alpha Vantage adjudication manifest hash mismatch")
    if alpha_adjudication.get("accounting_basis") != "unknown":
        raise ConsensusBasisAuditError("Alpha Vantage accounting basis must remain unknown")
    if alpha_adjudication.get("historical_availability_verified") is not False:
        raise ConsensusBasisAuditError("Alpha Vantage PIT history must remain unverified")
    if alpha_adjudication.get("operational_eligible") is not False:
        raise ConsensusBasisAuditError("Alpha Vantage evidence cannot be operational")

    estimates = alpha.get("estimates")
    if alpha.get("symbol") != EXPECTED_TICKER or not isinstance(estimates, list):
        raise ConsensusBasisAuditError("Alpha Vantage estimates-array schema mismatch")
    alpha_fy = [
        row
        for row in estimates
        if isinstance(row, dict)
        and row.get("date") == "2027-01-31"
        and row.get("horizon") == "fiscal year"
    ]
    if len(alpha_fy) != 1:
        raise ConsensusBasisAuditError("Alpha Vantage Jan 2027 fiscal-year row is not unique")
    alpha_eps = _finite_number(
        alpha_fy[0].get("eps_estimate_average"),
        "Alpha Vantage Jan 2027 average EPS",
        positive=True,
    )
    difference_pct = abs(alpha_eps - nasdaq_eps) / nasdaq_eps * 100.0

    unit_records = unit_evidence.get("records")
    if not isinstance(unit_records, dict):
        raise ConsensusBasisAuditError("EPS unit evidence records are invalid")
    scoped_tickers = sorted(str(ticker) for ticker in unit_records)
    if EXPECTED_TICKER in unit_records:
        raise ConsensusBasisAuditError(
            "NVDA unit evidence unexpectedly exists; explicit contract review is required"
        )

    blockers = list(nasdaq_contract_result["blockers"])
    blockers.extend(
        [
            "nasdaq_provider_methodology_not_preserved",
            "nasdaq_forward_period_definition_unknown",
            "alpha_accounting_basis_unknown",
            "alpha_historical_pit_unverified",
            "alpha_usage_rights_not_approved",
            "nvda_unit_evidence_missing",
        ]
    )
    blockers = list(dict.fromkeys(blockers))

    sources = {
        "nasdaq_normalized_snapshot": {
            "path": str(nasdaq_path),
            "source_hash": nasdaq_hash,
        },
        "nasdaq_collector_source": {
            "path": str(collector_path),
            "source_hash": _sha256(collector_raw),
        },
        "alpha_provider_native_raw": {
            "path": str(alpha_path),
            "source_hash": _sha256(alpha_raw),
        },
        "alpha_capture_manifest": {
            "path": str(alpha_manifest_file),
            "source_hash": _sha256(alpha_manifest_raw),
        },
        "alpha_partial_adjudication": {
            "path": str(alpha_adjudication_file),
            "source_hash": _sha256(alpha_adjudication_raw),
        },
        "ticker_scoped_eps_unit_evidence": {
            "path": str(unit_path),
            "source_hash": _sha256(unit_raw),
        },
    }

    result: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "ticker": EXPECTED_TICKER,
        "sources": sources,
        "nasdaq_investigation": {
            "provider": "Nasdaq Analyst Research",
            "provider_id": EXPECTED_NASDAQ_PROVIDER,
            "source_url": nasdaq_record.get("sourceUrl"),
            "snapshot_kind": "normalized_derived_snapshot",
            "provider_native_raw_bytes_preserved": False,
            "consensus_eps": nasdaq_eps,
            "fiscal_period_label": nasdaq_record.get("fiscalEnd"),
            "captured_at": nasdaq_record.get("collectedAt"),
            "available_at": nasdaq_record.get("collectedAt"),
            "accounting_basis": "unknown",
            "provider_label": nasdaq_record.get("accountingBasis"),
            "eps_share_basis": "unknown",
            "split_adjustment": "unknown",
            "adr_share_conversion": "unknown",
            "forward_period_definition": "unknown_provider_fiscal_label_only",
            "currency": nasdaq_record.get("currency"),
            "methodology_evidence_found": False,
            "finding": (
                "Local code preserves a normalized fiscal-year consensus label and explicitly "
                "marks EPS, accounting, split, and ADR bases as unspecified; no provider-native "
                "response bytes or official methodology document was found in the supplied files."
            ),
            "contract": nasdaq_contract,
        },
        "alpha_crosscheck": {
            "provider": "Alpha Vantage",
            "provider_schema": EXPECTED_ALPHA_SCHEMA,
            "source_hash": _sha256(alpha_raw),
            "available_at": alpha_manifest.get("available_at"),
            "fiscal_period": "2027-01-31",
            "horizon": "fiscal year",
            "average_eps": alpha_eps,
            "accounting_basis": "unknown",
            "split_adjustment": "unknown",
            "historical_availability_verified": False,
            "usage": "numeric_crosscheck_only",
            "absolute_difference": abs(alpha_eps - nasdaq_eps),
            "difference_pct": difference_pct,
        },
        "ticker_scoped_unit_evidence": {
            "source_hash": _sha256(unit_raw),
            "covered_tickers": scoped_tickers,
            "nvda_covered": False,
            "scope_rule": "ASML, ARM, and TSM evidence applies only to the named ticker and period.",
        },
        "proxy_price_map": {
            "status": "proxy_inputs_available",
            "classification": "provider_homogeneous_proxy",
            "consensus_eps": nasdaq_eps,
            "fiscal_period": nasdaq_record.get("fiscalEnd"),
            "alpha_numeric_crosscheck_difference_pct": difference_pct,
            "confidence_effect": "cross_provider_numeric_agreement_only",
            "verified_action": None,
            "operational_eligible": False,
        },
        "verified_price_map": {
            "status": "locked",
            "values": {},
            "verified_action": None,
            "operational_eligible": False,
            "blockers": blockers,
        },
        "alternative_source_contract": {
            "required_fields": list(REQUIRED_PROVIDER_FIELDS),
            "rules": [
                "Provider-native bytes must be preserved before normalization.",
                "GAAP/non-GAAP and basic/diluted bases must be explicit, not inferred.",
                "Split and ADR/share conversion policies must be source-bound.",
                "Fiscal period and forward-horizon definitions must be explicit.",
                "Storage, derived-analysis, local-display, and public-display rights are separate.",
                "SEC actuals and issuer guidance remain separate from Street consensus.",
            ],
            "candidate_categories": [
                {
                    "category": "nasdaq_analyst_research",
                    "status": "normalized_snapshot_only",
                    "purpose": "Requires provider-native bytes, official basis methodology, and rights evidence",
                },
                {
                    "category": "alpha_vantage_earnings_estimates",
                    "status": "numeric_crosscheck_only",
                    "purpose": "Raw bytes exist, but accounting/share/split bases, PIT history, and rights remain unverified",
                },
                {
                    "category": "fmp_financial_estimates",
                    "status": "not_connected_not_evaluated",
                    "purpose": "Candidate only; endpoint access, field basis, vintage, and rights require separate approval",
                },
                {
                    "category": "licensed_institutional_consensus",
                    "status": "not_connected_cost_and_license_required",
                    "purpose": "FactSet/LSEG/Capital IQ class source; no subscription or field verification performed",
                },
                {
                    "category": "user_supplied_licensed_export",
                    "status": "provider_neutral_contract_available",
                    "purpose": "Local import only after raw-byte, basis, timestamp, unit, and rights validation",
                },
                {
                    "category": "issuer_guidance",
                    "status": "available_for_separate_axis_only",
                    "purpose": "Company outlook; never a substitute for Street consensus",
                },
                {
                    "category": "sec_actuals",
                    "status": "available_for_separate_axis_only",
                    "purpose": "Reported actuals and share-unit validation only",
                },
            ],
        },
        "decision_state": {
            "proxy_action_may_remain": True,
            "verified_action": None,
            "operational_eligible": False,
            "new_decision_created": False,
        },
    }
    result["calculation_sha256"] = _canonical_sha256(result)
    return result


def validate_consensus_basis_audit(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("audit_version") != AUDIT_VERSION:
        raise ConsensusBasisAuditError("consensus basis audit version mismatch")
    expected_hash = value.get("calculation_sha256")
    if not isinstance(expected_hash, str):
        raise ConsensusBasisAuditError("consensus basis audit calculation hash is missing")
    payload = dict(value)
    payload.pop("calculation_sha256", None)
    if _canonical_sha256(payload) != expected_hash.upper():
        raise ConsensusBasisAuditError("consensus basis audit calculation hash mismatch")
    verified = value.get("verified_price_map")
    decision = value.get("decision_state")
    if not isinstance(verified, dict) or verified.get("status") != "locked":
        raise ConsensusBasisAuditError("verified price map must remain locked")
    if verified.get("values") != {} or verified.get("verified_action") is not None:
        raise ConsensusBasisAuditError("verified price map contains unapproved values")
    if verified.get("operational_eligible") is not False:
        raise ConsensusBasisAuditError("verified price map cannot be operational")
    if not isinstance(decision, dict) or decision.get("verified_action") is not None:
        raise ConsensusBasisAuditError("verified action must remain null")
    if decision.get("operational_eligible") is not False:
        raise ConsensusBasisAuditError("decision cannot be operational")
    for source in (value.get("sources") or {}).values():
        if not isinstance(source, dict):
            raise ConsensusBasisAuditError("consensus audit source record is invalid")
        path = Path(str(source.get("path")))
        if not path.is_file() or _sha256(path.read_bytes()) != str(source.get("source_hash", "")).upper():
            raise ConsensusBasisAuditError("consensus audit source hash mismatch")
    return value


def write_consensus_basis_audit(path: str | Path, value: dict[str, Any]) -> Path:
    validate_consensus_basis_audit(value)
    output = Path(path)
    atomic_write_json(output, value)
    return output


def load_consensus_basis_audit(path: str | Path) -> dict[str, Any]:
    _, _, value = _read_json(path, "consensus basis audit")
    return validate_consensus_basis_audit(value)
