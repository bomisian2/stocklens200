from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from scripts.update_valuations import _latest_available_at, _stale_prior_result
from stocklens.valuation import MODEL_VERSION, ValuationResult


def _prior() -> ValuationResult:
    return ValuationResult(
        ticker="GOOGL",
        company_name="Alphabet Inc.",
        model_version=MODEL_VERSION,
        evaluated_at_utc="2026-10-09T20:00:00+00:00",
        price=250.0,
        price_as_of_utc="2026-10-09T20:00:00+00:00",
        financials_as_of_utc="2026-07-24T20:00:00+00:00",
        conservative_value=200.0,
        base_value=240.0,
        optimistic_value=280.0,
        upside_to_base=-0.041667,
        safety_margin=0.2,
        favorable_buy_low=192.0,
        favorable_buy_high=211.2,
        judgement="적정",
        confidence_score=75,
        uncertainty="medium",
        valuation_family="general",
        methods_used="normalized_eps, fcf_dcf",
        model_details_json="[]",
        growth_drivers="verified prior driver",
        risk_factors="verified prior risk",
        eps_revision_trend="unavailable",
        status="evaluated",
    )


def test_stale_prior_keeps_values_but_blocks_current_judgement() -> None:
    stale = _stale_prior_result(
        _prior().to_dict(),
        "provider refresh failed; prior verified valuation retained and blocked from new signals",
    )

    assert stale.base_value == 240.0
    assert stale.financials_as_of_utc == "2026-07-24T20:00:00+00:00"
    assert stale.status == "stale"
    assert stale.judgement == "판단 보류"
    assert "blocked from new signals" in stale.change_reason


def test_latest_financial_availability_ignores_invalid_timestamps() -> None:
    rows = [
        SimpleNamespace(available_at_utc="invalid"),
        SimpleNamespace(available_at_utc="2026-01-01T20:00:00+00:00"),
        SimpleNamespace(available_at_utc="2026-04-01T20:00:00+00:00"),
    ]
    assert _latest_available_at(rows) == datetime(2026, 4, 1, 20, tzinfo=timezone.utc)
