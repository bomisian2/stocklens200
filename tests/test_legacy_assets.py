from __future__ import annotations

import json

from stocklens.legacy_assets import audit_legacy_assets


def test_empty_legacy_source_fails_closed(tmp_path) -> None:
    (tmp_path / "app.py").write_text("print('legacy')\n", encoding="utf-8")

    report = audit_legacy_assets(tmp_path)

    assert report["read_only"] is True
    assert report["conversion_performed"] is False
    assert report["formal_valuation_input_available"] is False
    assert report["features"]["bandTable"]["observation_count"] == 0
    assert report["features"]["analystLog.oneTime"]["observation_count"] == 0


def _band_row(year: int, month: int = 1) -> dict:
    return {
        "observed_at_utc": f"{year}-{month:02d}-02T21:00:00+00:00",
        "price": 100.0 + year + month / 100,
        "public_ttm_eps": 5.0,
        "eps_available_at_utc": f"{year}-{month:02d}-01T21:00:00+00:00",
        "price_source_url": "https://example.com/price",
        "eps_source_url": "https://example.com/filing",
        "eps_basis": "GAAP",
        "split_adjusted": True,
        "distorted": False,
    }


def test_complete_legacy_export_separates_reference_and_formal_gate(tmp_path) -> None:
    rows = [_band_row(year, month) for year in range(2020, 2026) for month in (1, 4, 7, 10)]
    payload = {
        "issuer": {
            "bandTable": rows,
            "analystLog": {
                "oneTime": [{
                    "amount_per_share": 0.25,
                    "category": "restructuring",
                    "available_at_utc": "2025-02-01T21:00:00+00:00",
                    "source_url": "https://example.com/filing",
                    "recurring": False,
                    "tax_basis": "after_tax",
                }]
            },
        }
    }
    (tmp_path / "legacy.json").write_text(json.dumps(payload), encoding="utf-8")

    report = audit_legacy_assets(tmp_path)
    band = report["features"]["bandTable"]
    one_time = report["features"]["analystLog.oneTime"]

    assert band["observation_count"] == 24
    assert band["complete_point_in_time_count"] == 24
    assert band["effective_observation_count"] == 24
    assert band["reference_eligible"] is True
    assert band["formal_valuation_eligible"] is True
    assert one_time["read_only_adapter_eligible"] is True
    assert report["conversion_performed"] is False


def test_repeated_rows_do_not_inflate_effective_observation_count(tmp_path) -> None:
    repeated = [_band_row(2020), *[_band_row(2025) for _ in range(24)]]
    (tmp_path / "legacy.json").write_text(
        json.dumps({"issuer": {"bandTable": repeated}}), encoding="utf-8"
    )

    report = audit_legacy_assets(tmp_path)
    band = report["features"]["bandTable"]

    assert band["observation_count"] == 25
    assert band["duplicate_row_count"] == 23
    assert band["effective_observation_count"] == 2
    assert band["formal_valuation_eligible"] is False


def test_code_names_and_incomplete_rows_do_not_become_evidence(tmp_path) -> None:
    (tmp_path / "legacy.js").write_text(
        "function computePriceValidity(){}; const confidence=1; "
        "const distorted=true; const PEG=2; const signature='x'; const reviewQueue=[];",
        encoding="utf-8",
    )
    (tmp_path / "data.json").write_text(
        json.dumps({"bandTable": [{"date": "2025-01-01", "price": 10, "eps": 1}]}),
        encoding="utf-8",
    )

    report = audit_legacy_assets(tmp_path)

    assert report["features"]["computePriceValidity"]["present_in_text"] is True
    assert report["features"]["distorted+PEG veto"]["present_in_text"] is True
    assert report["features"]["review queue signature"]["present_in_text"] is True
    assert report["features"]["bandTable"]["reference_eligible"] is False
    assert report["formal_valuation_input_available"] is False
