from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


BRIDGE_EVIDENCE_STATES = {
    "official_observed",
    "book_value_proxy",
    "model_estimate",
    "missing",
}
BRIDGE_TREATMENTS = {"add", "subtract", "disclosure_only", "unresolved"}


def _finite(value: Any, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def validate_bridge_item(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one EV-to-common-equity item without inventing a value.

    A reported balance can still be ineligible for the bridge. For example,
    reported marketable securities require an operating-cash/liquidity policy,
    while an operating-lease balance requires a matching FCFF lease convention.
    """

    item_id = str(raw.get("id", "")).strip()
    if not item_id:
        raise ValueError("bridge item id is required")
    state = str(raw.get("state", ""))
    if state not in BRIDGE_EVIDENCE_STATES:
        raise ValueError(f"{item_id}.state must be one of {sorted(BRIDGE_EVIDENCE_STATES)}")
    treatment = str(raw.get("bridge_treatment", ""))
    if treatment not in BRIDGE_TREATMENTS:
        raise ValueError(
            f"{item_id}.bridge_treatment must be one of {sorted(BRIDGE_TREATMENTS)}"
        )

    value = raw.get("value_usd")
    if state == "missing":
        if value is not None:
            raise ValueError(f"{item_id} marked missing must have a null value")
        if treatment not in {"unresolved", "disclosure_only"}:
            raise ValueError(f"{item_id} missing value cannot be added or subtracted")
    else:
        value = _finite(value, f"{item_id}.value_usd")
        if value < 0:
            raise ValueError(f"{item_id}.value_usd cannot be negative")

    source_ids = raw.get("source_ids", [])
    if not isinstance(source_ids, list) or any(not isinstance(item, str) for item in source_ids):
        raise ValueError(f"{item_id}.source_ids must be a list of strings")
    if state in {"official_observed", "book_value_proxy"} and not source_ids:
        raise ValueError(f"{item_id} reported values require a source")
    if state == "model_estimate" and not str(raw.get("rationale", "")).strip():
        raise ValueError(f"{item_id} estimates require a rationale")

    formal_eligible = raw.get("formal_eligible", False)
    if not isinstance(formal_eligible, bool):
        raise ValueError(f"{item_id}.formal_eligible must be boolean")
    if formal_eligible and (state in {"model_estimate", "missing"} or treatment == "unresolved"):
        raise ValueError(f"{item_id} cannot be formally eligible in its current state")

    return {
        "id": item_id,
        "label": str(raw.get("label", item_id)),
        "value_usd": value,
        "state": state,
        "bridge_treatment": treatment,
        "formal_eligible": formal_eligible,
        "claim_class": raw.get("claim_class"),
        "source_ids": source_ids,
        "as_of": raw.get("as_of"),
        "rationale": raw.get("rationale"),
    }


def validate_accounting_consistency(
    *,
    items: Iterable[Mapping[str, Any]],
    fcff_lease_basis: str,
    preferred_treatment: str,
    share_denominator: Mapping[str, Any],
) -> list[str]:
    """Reject lease, preferred-claim, and dilution double counting."""

    validated = [validate_bridge_item(item) for item in items]
    errors: list[str] = []
    operating_lease_subtracted = any(
        item["claim_class"] == "operating_lease"
        and item["bridge_treatment"] == "subtract"
        for item in validated
    )
    if fcff_lease_basis == "gaap_ebit_unadjusted" and operating_lease_subtracted:
        errors.append(
            "operating lease liability cannot be subtracted while GAAP EBIT retains operating lease expense"
        )

    preferred_subtracted = any(
        item["claim_class"] == "convertible_preferred"
        and item["bridge_treatment"] == "subtract"
        for item in validated
    )
    includes_conversion = bool(share_denominator.get("includes_preferred_if_converted"))
    if preferred_subtracted and includes_conversion:
        errors.append(
            "convertible preferred cannot be both subtracted as a claim and included as conversion dilution"
        )
    if preferred_treatment == "claim" and includes_conversion:
        errors.append("preferred claim treatment requires a denominator that excludes conversion shares")
    if preferred_treatment == "if_converted" and preferred_subtracted:
        errors.append("if-converted preferred treatment cannot also subtract the liquidation claim")
    if preferred_treatment not in {"claim", "if_converted", "unresolved"}:
        errors.append("preferred treatment must be claim, if_converted, or unresolved")
    return errors


def assess_share_denominator(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Assess whether a share count can divide a point-in-time equity value."""

    value = raw.get("value")
    if value is not None:
        value = _finite(value, "share_denominator.value")
        if value <= 0:
            raise ValueError("share denominator must be positive")
    kind = str(raw.get("kind", "missing"))
    valid_kind = kind == "point_in_time_fully_diluted"
    complete = bool(
        value is not None
        and valid_kind
        and raw.get("treasury_stock_method_complete") is True
        and raw.get("preferred_policy_complete") is True
    )
    return {
        "value": value,
        "unit": raw.get("unit", "shares"),
        "kind": kind,
        "as_of": raw.get("as_of"),
        "source_ids": raw.get("source_ids", []),
        "includes_preferred_if_converted": bool(raw.get("includes_preferred_if_converted")),
        "eligible_for_per_share_value": complete,
        "reason": (
            None
            if complete
            else "point-in-time fully diluted shares with treasury-stock and preferred treatment are required"
        ),
    }


def assess_equity_bridge(
    *,
    enterprise_value_range: Mapping[str, Any] | None,
    items: Iterable[Mapping[str, Any]],
    share_denominator: Mapping[str, Any],
    fcff_lease_basis: str,
    preferred_treatment: str,
    enterprise_value_formal: bool = False,
) -> dict[str, Any]:
    """Grade the bridge without calculating through unresolved or missing inputs.

    Grade A is reserved for a formally eligible common-equity range. Grade B is
    a research range based on official actuals plus disclosed assumptions. Grade
    C means a required conversion step is unavailable. The current GOOGL audit
    intentionally yields B at EV and C at common-equity/per-share levels.
    """

    validated = [validate_bridge_item(item) for item in items]
    shares = assess_share_denominator(share_denominator)
    consistency_errors = validate_accounting_consistency(
        items=validated,
        fcff_lease_basis=fcff_lease_basis,
        preferred_treatment=preferred_treatment,
        share_denominator=share_denominator,
    )
    unresolved = [
        item["id"]
        for item in validated
        if item["state"] == "missing"
        or item["bridge_treatment"] == "unresolved"
        or not item["formal_eligible"]
    ]
    ev_available = bool(
        enterprise_value_range
        and enterprise_value_range.get("low") is not None
        and enterprise_value_range.get("high") is not None
    )
    bridge_complete = not unresolved and not consistency_errors
    common_equity_available = ev_available and bridge_complete
    per_share_available = common_equity_available and shares["eligible_for_per_share_value"]
    common_grade = (
        ("A" if enterprise_value_formal else "B") if common_equity_available else "C"
    )
    per_share_grade = ("A" if enterprise_value_formal else "B") if per_share_available else "C"

    # Arithmetic is deliberately blocked unless every bridge and denominator
    # requirement has passed. No partial sum is allowed to look like equity value.
    return {
        "enterprise_value_grade": ("A" if enterprise_value_formal else "B") if ev_available else "C",
        "common_equity_grade": common_grade,
        "per_share_grade": per_share_grade,
        "enterprise_value_range": dict(enterprise_value_range) if ev_available else None,
        "common_equity_value_range": None,
        "per_share_value_range": None,
        "bridge_complete": bridge_complete,
        "unresolved_or_nonformal_items": sorted(unresolved),
        "accounting_consistency_errors": consistency_errors,
        "share_denominator": shares,
        "official_or_proxy_items": validated,
        "operational_use": False,
        "reason": (
            "research enterprise value is available, but unresolved bridge policy and a missing "
            "point-in-time fully diluted denominator prevent common-equity and per-share values"
            if ev_available and not per_share_available
            else "required enterprise-value evidence is unavailable"
        ),
    }


def per_share_balance_sensitivity(amount_usd: float, issued_shares: float) -> dict[str, Any]:
    """Return a mechanical scale diagnostic, never a fair-value calculation."""

    amount = _finite(amount_usd, "amount_usd")
    shares = _finite(issued_shares, "issued_shares")
    if amount < 0 or shares <= 0:
        raise ValueError("amount must be non-negative and issued shares must be positive")
    return {
        "amount_usd": amount,
        "issued_shares": shares,
        "mechanical_usd_per_issued_share": amount / shares,
        "is_fair_value": False,
        "is_diluted_per_share_value": False,
        "operational_use": False,
        "warning": "issued shares are used only to show scale and are not a fully diluted denominator",
    }
