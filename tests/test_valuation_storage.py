from __future__ import annotations

from pathlib import Path

import pytest

from stocklens.valuation import ValuationResult
from stocklens.valuation_storage import ValuationStore, ValuationStoreError


def _result(ticker: str = "ABC") -> ValuationResult:
    return ValuationResult(
        ticker=ticker, company_name="ABC", model_version="test", evaluated_at_utc="2025-01-01T00:00:00+00:00",
        price=10, price_as_of_utc="2024-12-31T21:00:00+00:00", financials_as_of_utc="2024-02-01T00:00:00+00:00",
        conservative_value=8, base_value=12, optimistic_value=15, upside_to_base=0.1667,
        safety_margin=0.2, favorable_buy_low=9.6, favorable_buy_high=10.56, judgement="저평가",
        confidence_score=75, uncertainty="medium", valuation_family="general", methods_used="fcf_dcf",
        model_details_json="[]", growth_drivers="growth", risk_factors="risk", eps_revision_trend="unavailable",
        status="evaluated",
    )


def test_storage_rejects_partial_production_output(tmp_path: Path) -> None:
    with pytest.raises(ValuationStoreError, match="200 rows"):
        ValuationStore(tmp_path).save_valuations([_result()])


def test_storage_writes_partial_development_output_and_history(tmp_path: Path) -> None:
    store = ValuationStore(tmp_path)
    frame = store.save_valuations([_result()], require_200=False)
    assert len(frame) == 1
    assert store.valuations_path.exists()
    assert store.history_path.exists()


def test_empty_financial_cache_round_trips(tmp_path: Path) -> None:
    store = ValuationStore(tmp_path)
    store.save_financials([])
    assert store.load_financials() == []


def test_provenance_round_trips(tmp_path: Path) -> None:
    store = ValuationStore(tmp_path)
    payload = {"provider": "sec_edgar_official_files", "normalized_sha256": "a" * 64}
    store.save_provenance(payload)
    assert store.load_provenance() == payload

