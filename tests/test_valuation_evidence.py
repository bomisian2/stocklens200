from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from stocklens.valuation_evidence import ValuationEvidenceError, load_valuation_evidence


def _bundle(*, fixture: bool = True, eps_available: str = "2020-01-01T21:00:00+00:00") -> dict:
    return {
        "schema_version": 1,
        "fixture": fixture,
        "generated_at_utc": "2025-01-01T00:00:00+00:00",
        "issuers": {
            "GOOGL": {
                "historical_pe": [{
                    "observed_at_utc": "2020-02-01T21:00:00+00:00",
                    "price": 100,
                    "public_ttm_eps": 5,
                    "eps_available_at_utc": eps_available,
                    "risk_free_rate": 0.02,
                    "sector_pe": 20,
                    "distorted": False,
                    "distortion_reasons": [],
                    "price_source_url": "https://example.com/price",
                    "eps_source_url": "https://example.com/eps",
                }],
            },
        },
    }


def test_fixture_evidence_is_blocked_from_operational_load(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_bundle()), encoding="utf-8")
    with pytest.raises(ValuationEvidenceError, match="fixture"):
        load_valuation_evidence(path, evaluated_at=datetime(2025, 12, 31, tzinfo=timezone.utc))


def test_fixture_evidence_can_be_loaded_only_when_explicitly_allowed(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_bundle()), encoding="utf-8")
    loaded = load_valuation_evidence(
        path, evaluated_at=datetime(2025, 12, 31, tzinfo=timezone.utc), allow_fixture=True,
    )
    assert loaded["GOOGL"].fixture is True
    assert len(loaded["GOOGL"].historical_pe) == 1
    assert len(loaded["GOOGL"].source_sha256) == 64


def test_future_eps_cannot_be_used_for_historical_pe(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(_bundle(eps_available="2020-03-01T21:00:00+00:00")),
        encoding="utf-8",
    )
    with pytest.raises(ValuationEvidenceError, match="not public"):
        load_valuation_evidence(
            path, evaluated_at=datetime(2025, 12, 31, tzinfo=timezone.utc), allow_fixture=True,
        )
