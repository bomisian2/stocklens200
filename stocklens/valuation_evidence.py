from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


class ValuationEvidenceError(ValueError):
    """Raised when valuation evidence is not reproducible or point-in-time safe."""


@dataclass(frozen=True)
class EPSAdjustmentEvidence:
    category: str
    amount_per_share: float
    recurring: bool
    source_url: str
    available_at_utc: str
    description: str = ""


@dataclass(frozen=True)
class EarningsQualityEvidence:
    gaap_eps: float
    normalized_eps: float | None
    state: str
    loss_assessment: str
    source_url: str
    available_at_utc: str
    adjustments: tuple[EPSAdjustmentEvidence, ...] = ()


@dataclass(frozen=True)
class HistoricalPEObservation:
    observed_at_utc: str
    price: float
    public_ttm_eps: float
    eps_available_at_utc: str
    risk_free_rate: float | None
    sector_pe: float | None
    distorted: bool
    distortion_reasons: tuple[str, ...]
    price_source_url: str
    eps_source_url: str


@dataclass(frozen=True)
class ComparableMultipleObservation:
    observed_at_utc: str
    ev_to_ebitda: float
    available_at_utc: str
    source_url: str


@dataclass(frozen=True)
class ForecastEvidence:
    available_at_utc: str
    source_url: str
    eps_growth: float | None = None
    fcf_growth: float | None = None
    dispersion: float | None = None
    roic: float | None = None


@dataclass(frozen=True)
class CalibrationEvidence:
    version: str
    selected_before_holdout: bool
    holdout_observations: int
    accepted: bool
    source_url: str
    coefficients: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ValuationEvidence:
    ticker: str
    source_sha256: str
    fixture: bool
    historical_pe: tuple[HistoricalPEObservation, ...] = ()
    comparable_ev_ebitda: tuple[ComparableMultipleObservation, ...] = ()
    earnings_quality: EarningsQualityEvidence | None = None
    forecast: ForecastEvidence | None = None
    calibration: CalibrationEvidence | None = None
    historical_forecast_error: float | None = None
    current_sector_pe: float | None = None


_ADJUSTMENT_CATEGORIES = {
    "amortization",
    "asset_disposal",
    "restructuring",
    "stock_compensation",
    "impairment",
    "investment_gain_loss",
    "litigation",
    "other",
}
_EARNINGS_STATES = {"clean", "distorted", "negative", "structural_loss", "unverified"}


def _timestamp(value: Any, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValuationEvidenceError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValuationEvidenceError(f"{field_name} must include a timezone")
    return parsed


def _source(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("https://"):
        raise ValuationEvidenceError(f"{field_name} must be an https source URL")
    return text


def _finite(value: Any, field_name: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValuationEvidenceError(f"{field_name} must be numeric") from exc
    if number != number or number in {float("inf"), float("-inf")}:
        raise ValuationEvidenceError(f"{field_name} must be finite")
    return number


def _parse_issuer(ticker: str, raw: dict[str, Any], *, fixture: bool, digest: str) -> ValuationEvidence:
    pe_rows: list[HistoricalPEObservation] = []
    for index, item in enumerate(raw.get("historical_pe") or []):
        observed = _timestamp(item.get("observed_at_utc"), f"historical_pe[{index}].observed_at_utc")
        eps_available = _timestamp(item.get("eps_available_at_utc"), f"historical_pe[{index}].eps_available_at_utc")
        if eps_available > observed:
            raise ValuationEvidenceError("historical P/E uses EPS that was not public on the price date")
        price = _finite(item.get("price"), f"historical_pe[{index}].price")
        eps = _finite(item.get("public_ttm_eps"), f"historical_pe[{index}].public_ttm_eps")
        if price is None or price <= 0:
            raise ValuationEvidenceError("historical price must be positive")
        pe_rows.append(HistoricalPEObservation(
            observed_at_utc=observed.isoformat(),
            price=price,
            public_ttm_eps=eps,
            eps_available_at_utc=eps_available.isoformat(),
            risk_free_rate=_finite(item.get("risk_free_rate"), "risk_free_rate", allow_none=True),
            sector_pe=_finite(item.get("sector_pe"), "sector_pe", allow_none=True),
            distorted=bool(item.get("distorted", False)),
            distortion_reasons=tuple(str(value) for value in item.get("distortion_reasons") or []),
            price_source_url=_source(item.get("price_source_url"), "price_source_url"),
            eps_source_url=_source(item.get("eps_source_url"), "eps_source_url"),
        ))

    comparable_rows: list[ComparableMultipleObservation] = []
    for index, item in enumerate(raw.get("comparable_ev_ebitda") or []):
        observed = _timestamp(item.get("observed_at_utc"), f"comparable[{index}].observed_at_utc")
        available = _timestamp(item.get("available_at_utc"), f"comparable[{index}].available_at_utc")
        if available > observed:
            raise ValuationEvidenceError("comparable multiple was not available on its observation date")
        multiple = _finite(item.get("ev_to_ebitda"), "ev_to_ebitda")
        if multiple is None or multiple <= 0:
            raise ValuationEvidenceError("EV/EBITDA multiple must be positive")
        comparable_rows.append(ComparableMultipleObservation(
            observed_at_utc=observed.isoformat(),
            ev_to_ebitda=multiple,
            available_at_utc=available.isoformat(),
            source_url=_source(item.get("source_url"), "comparable source_url"),
        ))

    quality = None
    if raw.get("earnings_quality") is not None:
        item = raw["earnings_quality"]
        state = str(item.get("state") or "")
        if state not in _EARNINGS_STATES:
            raise ValuationEvidenceError(f"unknown earnings quality state: {state}")
        available = _timestamp(item.get("available_at_utc"), "earnings_quality.available_at_utc")
        adjustments: list[EPSAdjustmentEvidence] = []
        for index, adjustment in enumerate(item.get("adjustments") or []):
            category = str(adjustment.get("category") or "")
            if category not in _ADJUSTMENT_CATEGORIES:
                raise ValuationEvidenceError(f"unknown EPS adjustment category: {category}")
            adjustment_available = _timestamp(
                adjustment.get("available_at_utc"), f"adjustments[{index}].available_at_utc"
            )
            adjustments.append(EPSAdjustmentEvidence(
                category=category,
                amount_per_share=float(_finite(adjustment.get("amount_per_share"), "amount_per_share")),
                recurring=bool(adjustment.get("recurring")),
                source_url=_source(adjustment.get("source_url"), "adjustment source_url"),
                available_at_utc=adjustment_available.isoformat(),
                description=str(adjustment.get("description") or ""),
            ))
        quality = EarningsQualityEvidence(
            gaap_eps=float(_finite(item.get("gaap_eps"), "gaap_eps")),
            normalized_eps=_finite(item.get("normalized_eps"), "normalized_eps", allow_none=True),
            state=state,
            loss_assessment=str(item.get("loss_assessment") or "unverified"),
            source_url=_source(item.get("source_url"), "earnings_quality source_url"),
            available_at_utc=available.isoformat(),
            adjustments=tuple(adjustments),
        )

    forecast = None
    if raw.get("forecast") is not None:
        item = raw["forecast"]
        forecast = ForecastEvidence(
            available_at_utc=_timestamp(item.get("available_at_utc"), "forecast.available_at_utc").isoformat(),
            source_url=_source(item.get("source_url"), "forecast source_url"),
            eps_growth=_finite(item.get("eps_growth"), "eps_growth", allow_none=True),
            fcf_growth=_finite(item.get("fcf_growth"), "fcf_growth", allow_none=True),
            dispersion=_finite(item.get("dispersion"), "dispersion", allow_none=True),
            roic=_finite(item.get("roic"), "roic", allow_none=True),
        )

    calibration = None
    if raw.get("calibration") is not None:
        item = raw["calibration"]
        calibration = CalibrationEvidence(
            version=str(item.get("version") or ""),
            selected_before_holdout=bool(item.get("selected_before_holdout")),
            holdout_observations=int(item.get("holdout_observations") or 0),
            accepted=bool(item.get("accepted")),
            source_url=_source(item.get("source_url"), "calibration source_url"),
            coefficients={
                str(key): float(_finite(value, f"coefficient {key}"))
                for key, value in (item.get("coefficients") or {}).items()
            },
        )

    return ValuationEvidence(
        ticker=ticker.upper(),
        source_sha256=digest,
        fixture=fixture,
        historical_pe=tuple(pe_rows),
        comparable_ev_ebitda=tuple(comparable_rows),
        earnings_quality=quality,
        forecast=forecast,
        calibration=calibration,
        historical_forecast_error=_finite(
            raw.get("historical_forecast_error"), "historical_forecast_error", allow_none=True
        ),
        current_sector_pe=_finite(raw.get("current_sector_pe"), "current_sector_pe", allow_none=True),
    )


def load_valuation_evidence(
    path: str | Path,
    *,
    evaluated_at: datetime,
    allow_fixture: bool = False,
) -> dict[str, ValuationEvidence]:
    """Load a hash-addressed, point-in-time valuation evidence bundle."""
    source = Path(path)
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValuationEvidenceError("valuation evidence is not valid UTF-8 JSON") from exc
    if payload.get("schema_version") != 1:
        raise ValuationEvidenceError("unsupported valuation evidence schema")
    fixture = bool(payload.get("fixture"))
    if fixture and not allow_fixture:
        raise ValuationEvidenceError("fixture valuation evidence is blocked from operational runs")
    generated = _timestamp(payload.get("generated_at_utc"), "generated_at_utc")
    if generated > evaluated_at:
        raise ValuationEvidenceError("valuation evidence was generated after the evaluation time")
    issuers = payload.get("issuers")
    if not isinstance(issuers, dict):
        raise ValuationEvidenceError("issuers must be an object")
    return {
        str(ticker).upper(): _parse_issuer(str(ticker), raw, fixture=fixture, digest=digest)
        for ticker, raw in issuers.items()
        if isinstance(raw, dict)
    }
