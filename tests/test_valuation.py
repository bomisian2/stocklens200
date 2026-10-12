from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from stocklens.financials import FinancialPeriod
from stocklens.valuation import MODEL_VERSION, evaluate_company
from stocklens.valuation_evidence import (
    CalibrationEvidence,
    ComparableMultipleObservation,
    EarningsQualityEvidence,
    EPSAdjustmentEvidence,
    ForecastEvidence,
    HistoricalPEObservation,
    ValuationEvidence,
)


def _period(year: int, *, eps: float = 5.0, fcf: float = 20_000.0, operating: float = 28_000.0, net: float = 22_000.0) -> FinancialPeriod:
    return FinancialPeriod(
        ticker="GOOGL", cik=1652044, company_name="Alphabet", fiscal_year=year,
        period_type="FY", period_start=f"{year}-01-01", period_end=f"{year}-12-31",
        filed_at=f"{year + 1}-02-01", available_at_utc=f"{year + 1}-02-01T21:00:00+00:00",
        availability_basis="sec_acceptance_timestamp", availability_estimated=False,
        form="10-K", accession=f"{year}-x", currency="USD", revenue=100_000 + (year - 2020) * 10_000,
        operating_income=operating, net_income=net, pretax_income=27_000, income_tax=5_000,
        diluted_eps=eps, diluted_shares=4_000, cfo=30_000, capex=10_000, fcf=fcf,
        cash=80_000, total_debt=20_000, equity=150_000, sbc=8_000,
        depreciation_amortization=7_000, buybacks=15_000, validation_state="verified",
    )


def _evidence(
    *,
    state: str = "clean",
    gaap_eps: float = 5.0,
    normalized_eps: float | None = None,
) -> ValuationEvidence:
    start = datetime(2019, 1, 31, 21, tzinfo=timezone.utc)
    history = []
    for index in range(72):
        observed = start + timedelta(days=index * 30)
        pe = 16 + (index % 12) * 0.5
        history.append(HistoricalPEObservation(
            observed_at_utc=observed.isoformat(), price=gaap_eps * pe,
            public_ttm_eps=gaap_eps, eps_available_at_utc=(observed - timedelta(days=30)).isoformat(),
            risk_free_rate=0.035 + (index % 5) * 0.002, sector_pe=18 + (index % 3),
            distorted=False, distortion_reasons=(),
            price_source_url="https://example.com/price", eps_source_url="https://example.com/eps",
        ))
    adjustments = ()
    if state == "distorted":
        adjustments = (EPSAdjustmentEvidence(
            category="investment_gain_loss", amount_per_share=-2.0, recurring=False,
            source_url="https://example.com/filing", available_at_utc="2025-02-01T21:00:00+00:00",
        ),)
    comparable = tuple(ComparableMultipleObservation(
        observed_at_utc=f"2024-0{month}-15T21:00:00+00:00", ev_to_ebitda=10 + month,
        available_at_utc=f"2024-0{month}-01T21:00:00+00:00", source_url="https://example.com/comparable",
    ) for month in range(1, 7))
    return ValuationEvidence(
        ticker="GOOGL", source_sha256="fixture", fixture=True,
        historical_pe=tuple(history), comparable_ev_ebitda=comparable,
        earnings_quality=EarningsQualityEvidence(
            gaap_eps=gaap_eps, normalized_eps=normalized_eps, state=state,
            loss_assessment="one_time" if state == "distorted" else "not_applicable",
            source_url="https://example.com/filing", available_at_utc="2025-02-01T21:00:00+00:00",
            adjustments=adjustments,
        ),
        forecast=ForecastEvidence(
            available_at_utc="2025-02-01T21:00:00+00:00", source_url="https://example.com/forecast",
            eps_growth=0.09, fcf_growth=0.08, dispersion=0.07, roic=0.22,
        ),
        calibration=CalibrationEvidence(
            version="fixture-calibration", selected_before_holdout=True, holdout_observations=40,
            accepted=True, source_url="https://example.com/calibration", coefficients={"eps_growth": 0.5, "roic": 0.1},
        ),
        historical_forecast_error=0.12, current_sector_pe=20.0,
    )


def _evaluate(price: float = 100.0, periods: list[FinancialPeriod] | None = None, evidence: ValuationEvidence | None = None, prior_judgement: str | None = None):
    return evaluate_company(
        ticker="GOOGL", company_name="Alphabet", sector="Communication Services",
        industry="Internet Content", price=price, price_as_of_utc="2025-12-31T21:00:00+00:00",
        periods=periods or [_period(year) for year in range(2020, 2025)], risk_free_rate=0.042,
        evaluated_at=datetime(2025, 12, 31, 22, tzinfo=timezone.utc),
        valuation_evidence=evidence or _evidence(), prior_judgement=prior_judgement,
    )


def test_valuation_is_reproducible() -> None:
    left = _evaluate()
    right = _evaluate()
    assert left.to_dict() == right.to_dict()
    assert left.model_version == MODEL_VERSION
    assert left.status == "evaluated"


def test_market_price_does_not_move_fair_value() -> None:
    cheap = _evaluate(price=50.0)
    expensive = _evaluate(price=500.0)
    assert cheap.base_value == expensive.base_value
    assert cheap.upside_to_base != expensive.upside_to_base


def test_one_time_income_uses_core_eps_adjustment_for_alphabet() -> None:
    periods = [_period(year, eps=20.0, net=80_000.0) for year in range(2020, 2025)]
    result = _evaluate(periods=periods, evidence=_evidence(state="distorted", gaap_eps=20.0, normalized_eps=5.0))
    models = json.loads(result.model_details_json)
    earnings = next(model for model in models if model["name"] == "normalized_eps")
    assert earnings["inputs"]["earnings_quality_state"] == "distorted"
    assert earnings["inputs"]["normalized_eps"] == 5.0
    assert earnings["inputs"]["adjustment_categories"] == ["investment_gain_loss"]


def test_negative_eps_disables_earnings_model_but_dcf_can_continue() -> None:
    periods = [replace(_period(year), diluted_eps=-2.0) for year in range(2020, 2025)]
    result = _evaluate(periods=periods, evidence=_evidence(state="structural_loss", gaap_eps=-2.0))
    models = json.loads(result.model_details_json)
    earnings = next(model for model in models if model["name"] == "normalized_eps")
    assert earnings["available"] is False
    assert result.status == "evaluated"
    assert "fcf_dcf" in result.methods_used


def test_missing_point_in_time_pe_never_falls_back_to_fixed_sector_multiple() -> None:
    result = _evaluate(evidence=ValuationEvidence(ticker="GOOGL", source_sha256="fixture", fixture=True))
    models = json.loads(result.model_details_json)
    earnings = next(model for model in models if model["name"] == "normalized_eps")
    comparable = next(model for model in models if model["name"] == "ev_ebitda")
    assert earnings["available"] is False
    assert comparable["available"] is False
    assert result.methods_used == "fcf_dcf"


def test_company_specific_band_is_not_fixed_ten_percent() -> None:
    result = _evaluate()
    assert result.conservative_value != round(result.base_value * 0.9, 2)
    assert result.optimistic_value != round(result.base_value * 1.1, 2)
    band = next(item for item in json.loads(result.model_details_json) if item["name"] == "automatic_uncertainty_band")
    assert band["inputs"]["historical_forecast_error"] == 0.12
    assert result.safety_margin >= 0.12


def test_unaccepted_calibration_cannot_change_target_pe() -> None:
    evidence = _evidence()
    rejected = replace(
        evidence,
        calibration=replace(
            evidence.calibration,
            accepted=False,
            coefficients={"eps_growth": 999.0, "roic": 999.0},
        ),
    )
    result = _evaluate(evidence=rejected)
    earnings = next(model for model in json.loads(result.model_details_json) if model["name"] == "normalized_eps")
    assert earnings["inputs"]["calibration_applied"] is False
    assert earnings["inputs"]["calibration_adjustment"] == 0.0


def test_hysteresis_retains_prior_state_inside_exit_buffer() -> None:
    base = _evaluate(price=100.0)
    near_threshold_price = base.base_value * (1 - base.safety_margin + 0.005)
    retained = _evaluate(price=near_threshold_price, prior_judgement="저평가")
    assert retained.judgement == "저평가"


def test_intc_distorted_or_structural_loss_blocks_per_model() -> None:
    evidence = replace(
        _evidence(state="structural_loss", gaap_eps=-1.5),
        ticker="INTC",
    )
    result = evaluate_company(
        ticker="INTC", company_name="Intel", sector="Technology",
        industry="Semiconductors", price=25.0,
        price_as_of_utc="2025-12-31T21:00:00+00:00",
        periods=[replace(_period(year), ticker="INTC", diluted_eps=-1.5) for year in range(2020, 2025)],
        risk_free_rate=0.042, evaluated_at=datetime(2025, 12, 31, 22, tzinfo=timezone.utc),
        valuation_evidence=evidence,
    )
    earnings = next(model for model in json.loads(result.model_details_json) if model["name"] == "normalized_eps")
    assert result.valuation_family == "turnaround"
    assert earnings["available"] is False
    assert "structural" in earnings["reason"]


def test_future_filing_is_excluded() -> None:
    periods = [_period(year) for year in range(2020, 2023)]
    periods.append(replace(_period(2024), available_at_utc="2026-02-01T21:00:00+00:00"))
    result = _evaluate(periods=periods)
    assert result.status == "evaluated"
    assert result.financials_as_of_utc == "2023-02-01T21:00:00+00:00"


def test_reit_without_verified_ffo_is_on_hold() -> None:
    result = evaluate_company(
        ticker="REIT", company_name="Example REIT", sector="Real Estate", industry="Retail REIT",
        price=10, price_as_of_utc="2025-12-31T21:00:00+00:00",
        periods=[_period(year) for year in range(2020, 2025)], risk_free_rate=0.04,
        evaluated_at=datetime(2025, 12, 31, 22, tzinfo=timezone.utc),
    )
    assert result.status == "hold"
    assert result.judgement == "판단 보류"


def test_missing_eps_and_fcf_never_gets_a_fabricated_value() -> None:
    periods = [replace(_period(year), diluted_eps=None, fcf=None, cfo=None, capex=None, operating_income=None, depreciation_amortization=None) for year in range(2020, 2025)]
    result = _evaluate(periods=periods, evidence=ValuationEvidence(ticker="GOOGL", source_sha256="fixture", fixture=True))
    assert result.status == "hold"
    assert result.base_value is None

