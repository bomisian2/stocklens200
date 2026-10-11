from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

from stocklens.financials import FinancialPeriod
from stocklens.valuation import MODEL_VERSION, evaluate_company


def _period(year: int, *, eps: float = 5.0, fcf: float = 20_000.0, operating: float = 28_000.0, net: float = 22_000.0) -> FinancialPeriod:
    return FinancialPeriod(
        ticker="GOOGL", cik=1652044, company_name="Alphabet", fiscal_year=year,
        period_type="FY", period_start=f"{year}-01-01", period_end=f"{year}-12-31",
        filed_at=f"{year + 1}-02-01", available_at_utc=f"{year + 1}-02-01T21:00:00+00:00",
        form="10-K", accession=f"{year}-x", currency="USD", revenue=100_000 + (year - 2020) * 10_000,
        operating_income=operating, net_income=net, pretax_income=27_000, income_tax=5_000,
        diluted_eps=eps, diluted_shares=4_000, cfo=30_000, capex=10_000, fcf=fcf,
        cash=80_000, total_debt=20_000, equity=150_000, sbc=8_000,
        depreciation_amortization=7_000, buybacks=15_000, validation_state="verified",
    )


def _evaluate(price: float = 100.0, periods: list[FinancialPeriod] | None = None):
    return evaluate_company(
        ticker="GOOGL", company_name="Alphabet", sector="Communication Services",
        industry="Internet Content", price=price, price_as_of_utc="2025-12-31T21:00:00+00:00",
        periods=periods or [_period(year) for year in range(2020, 2025)], risk_free_rate=0.042,
        evaluated_at=datetime(2025, 12, 31, 22, tzinfo=timezone.utc),
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
    result = _evaluate(periods=periods)
    models = json.loads(result.model_details_json)
    earnings = next(model for model in models if model["name"] == "normalized_eps")
    assert earnings["inputs"]["core_eps_adjustment"] is True


def test_negative_eps_disables_earnings_model_but_dcf_can_continue() -> None:
    periods = [replace(_period(year), diluted_eps=-2.0) for year in range(2020, 2025)]
    result = _evaluate(periods=periods)
    models = json.loads(result.model_details_json)
    earnings = next(model for model in models if model["name"] == "normalized_eps")
    assert earnings["available"] is False
    assert result.status == "evaluated"
    assert "fcf_dcf" in result.methods_used


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
    result = _evaluate(periods=periods)
    assert result.status == "hold"
    assert result.base_value is None

