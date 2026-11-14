from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.nvda_research import (
    NvdaResearchError,
    adjudicate_partial_estimates,
    build_consensus_read_model,
    build_provisional_research_decision,
    load_research_artifacts,
)


AVAILABLE_AT = "2026-10-18T00:38:27.040534Z"


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _row(day: str, horizon: str, *, invalid_revenue: bool = False) -> dict:
    average = "100.0"
    high = "99.0" if invalid_revenue else "110.0"
    return {
        "date": day,
        "horizon": horizon,
        "eps_estimate_average": "9.3124",
        "eps_estimate_high": "10.1000",
        "eps_estimate_low": "9.0100",
        "eps_estimate_analyst_count": "51.0000",
        "eps_estimate_average_7_days_ago": "9.3000",
        "eps_estimate_average_30_days_ago": "9.2500",
        "eps_estimate_average_60_days_ago": "9.2000",
        "eps_estimate_average_90_days_ago": "9.1000",
        "eps_estimate_revision_up_trailing_7_days": "1.0000",
        "eps_estimate_revision_down_trailing_7_days": "0.0000",
        "eps_estimate_revision_up_trailing_30_days": "2.0000",
        "eps_estimate_revision_down_trailing_30_days": "0.0000",
        "revenue_estimate_average": average,
        "revenue_estimate_high": high,
        "revenue_estimate_low": "90.0",
        "revenue_estimate_analyst_count": "54.00",
    }


def _rows() -> list[dict]:
    rows = [
        _row(f"{year}-{month:02d}-28", "fiscal quarter")
        for year in range(2018, 2026)
        for month in (1, 4, 7, 10)
    ]
    rows.extend([
        _row("2026-01-28", "fiscal quarter"),
        _row("2026-04-28", "fiscal quarter"),
        _row("2026-07-28", "fiscal quarter"),
        _row("2025-01-28", "fiscal year"),
        _row("2026-01-28", "fiscal year"),
    ])
    rows[24] = _row("2024-01-28", "fiscal quarter", invalid_revenue=True)
    rows.extend([
        _row("2026-10-31", "fiscal quarter"),
        _row("2027-01-31", "fiscal quarter"),
        _row("2027-01-31", "fiscal year"),
        _row("2028-01-31", "fiscal year"),
    ])
    assert len(rows) == 41
    return rows


def _write_estimate_evidence(tmp_path: Path, rows: list[dict] | None = None) -> dict[str, Path]:
    rows = rows or _rows()
    raw = tmp_path / "NVDA_EARNINGS_ESTIMATES.response.raw"
    manifest = tmp_path / "NVDA_EARNINGS_ESTIMATES.response.raw.manifest.json"
    ledger = tmp_path / "continuation-collection-run.json"
    raw_bytes = json.dumps({"symbol": "NVDA", "estimates": rows}).encode()
    raw.write_bytes(raw_bytes)
    linked = {
        "request_id": "nvda-estimates",
        "function": "EARNINGS_ESTIMATES",
        "ticker": "NVDA",
        "response_sha256": _hash(raw_bytes),
        "byte_length": len(raw_bytes),
        "content_type": "application/json",
        "response_completed_at": AVAILABLE_AT,
    }
    manifest.write_text(json.dumps({
        **linked,
        "available_at": AVAILABLE_AT,
        "valid_provider_dataset": False,
        "validation_code": "estimate_revenue_range_invalid",
    }), encoding="utf-8")
    ledger.write_text(json.dumps({
        "calls_attempted": 1,
        "calls_completed": 1,
        "automatic_retries": 0,
        "redirects_allowed": False,
        "results": [linked],
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }), encoding="utf-8")
    return {"raw": raw, "manifest": manifest, "ledger": ledger}


def _write_decision_sources(tmp_path: Path) -> dict[str, Path]:
    price_raw = tmp_path / "NVDA_TIME_SERIES_DAILY.response.raw"
    price_manifest = tmp_path / "NVDA_TIME_SERIES_DAILY.response.raw.manifest.json"
    price_bytes = json.dumps({
        "Meta Data": {"2. Symbol": "NVDA"},
        "Time Series (Daily)": {
            "2026-10-16": {"1. open": "210", "2. high": "216", "3. low": "209", "4. close": "214.13", "5. volume": "1000"},
            "2026-10-15": {"1. open": "215", "2. high": "217", "3. low": "212", "4. close": "213", "5. volume": "900"},
        },
    }).encode()
    price_raw.write_bytes(price_bytes)
    price_manifest.write_text(json.dumps({
        "response_sha256": _hash(price_bytes),
        "validation_code": "valid_unadjusted_daily",
        "available_at": "2026-10-17T06:28:21.775956Z",
    }), encoding="utf-8")
    macro = tmp_path / "macro-snapshot.json"
    macro.write_text(json.dumps({
        "generatedAt": "2026-10-16T22:37:38.345Z",
        "state": "경계",
        "score": -1,
        "indicators": {
            "qqq": {"above20": False, "above50": False},
            "vix": {"above20": True, "above50": True},
            "hyg": {"above20": False, "above50": False},
            "tenYear": {"above20": False, "above50": True},
        },
    }), encoding="utf-8")
    valuation = tmp_path / "valuation-history.json"
    valuation.write_text(json.dumps({
        "records": {"NVDA": [
            {"asOf": "2026-10-13", "expectedPe": 24.7, "estimateKind": "Nasdaq 컨센서스 EPS"},
            {"asOf": "2026-10-14", "expectedPe": 23.92, "estimateKind": "Nasdaq 컨센서스 EPS"},
            {"asOf": "2026-10-15", "expectedPe": 23.48, "estimateKind": "Nasdaq 컨센서스 EPS"},
            {"asOf": "2026-10-16", "expectedPe": 23.1, "estimateKind": "Nasdaq 컨센서스 EPS"},
        ]},
    }), encoding="utf-8")
    return {
        "price_raw": price_raw,
        "price_manifest": price_manifest,
        "macro": macro,
        "valuation": valuation,
    }


def _adjudicate(tmp_path: Path, rows: list[dict] | None = None):
    paths = _write_estimate_evidence(tmp_path, rows)
    output = tmp_path / "adjudication.json"
    before = {key: path.read_bytes() for key, path in paths.items()}
    report = adjudicate_partial_estimates(
        raw_path=paths["raw"],
        manifest_path=paths["manifest"],
        ledger_path=paths["ledger"],
        output_path=output,
        generated_at="2026-10-18T01:00:00Z",
    )
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    return paths, output, report


def test_partial_adjudication_reproduces_41_40_1_and_four_forward(tmp_path: Path) -> None:
    paths, output, report = _adjudicate(tmp_path)
    assert report["capture_status"] == "captured"
    assert report["schema_status"] == "valid"
    assert report["data_quality_status"] == "partial_valid"
    assert report["usage_status"] == "research_only"
    assert report["row_counts"] == {
        "total": 41, "valid": 40, "quarantined": 1,
        "future_valid": 4, "future_quarantined": 0,
    }
    isolated = report["quarantined_records"][0]
    assert isolated["position"] == 25
    assert isolated["date"] == "2024-01-28"
    assert isolated["horizon"] == "fiscal quarter"
    assert isolated["reason"] == "estimate_revenue_range_invalid"
    assert isolated["canonical_sha256"] == "6E2F601F842DC85B6B437B569D7036D3DC5022AF6A2510DEAD3F43A033137903"
    assert report["source_bytes_modified"] is False
    assert output.is_file()


def test_read_model_keeps_past_non_pit_and_future_research_only(tmp_path: Path) -> None:
    paths, output, report = _adjudicate(tmp_path)
    model_path = tmp_path / "read-model.json"
    model = build_consensus_read_model(
        raw_path=paths["raw"],
        adjudication=report,
        adjudication_sha256=_hash(output.read_bytes()),
        output_path=model_path,
    )
    assert len(model["records"]) == 40
    assert len(model["forward_records"]) == 4
    assert model["accounting_basis"] == "unknown"
    assert model["historical_availability_verified"] is False
    assert model["fy1_ntm_mapping_eligible"] is False
    assert model["operational_eligible"] is False
    past = [row for row in model["records"] if not row["research_eligible"]]
    assert past
    assert all(row["historical_record_classification"] == "current_snapshot_records_for_past_periods" for row in past)
    assert all(row["backtest_eligible"] is False for row in model["records"])
    assert all(row["historical_per_distribution_eligible"] is False for row in model["records"])
    assert all(row["evidence_class"] == "current_forward_consensus_snapshot" for row in model["forward_records"])


def test_each_added_past_error_is_quarantined_and_future_error_is_excluded(tmp_path: Path) -> None:
    rows = _rows()
    rows[5] = _row(rows[5]["date"], rows[5]["horizon"], invalid_revenue=True)
    rows[-1] = _row("2028-01-31", "fiscal year", invalid_revenue=True)
    paths, output, report = _adjudicate(tmp_path, rows)
    assert report["row_counts"] == {
        "total": 41, "valid": 38, "quarantined": 3,
        "future_valid": 3, "future_quarantined": 1,
    }
    model = build_consensus_read_model(
        raw_path=paths["raw"],
        adjudication=report,
        adjudication_sha256=_hash(output.read_bytes()),
    )
    assert len(model["forward_records"]) == 3
    assert all(row["period_date"] != "2028-01-31" for row in model["forward_records"])


def test_provisional_decision_uses_available_axis_only_and_never_promotes(tmp_path: Path) -> None:
    paths, adjudication_path, report = _adjudicate(tmp_path)
    read_model_path = tmp_path / "read-model.json"
    model = build_consensus_read_model(
        raw_path=paths["raw"], adjudication=report,
        adjudication_sha256=_hash(adjudication_path.read_bytes()), output_path=read_model_path,
    )
    sources = _write_decision_sources(tmp_path)
    decision_path = tmp_path / "decision.json"
    config = Path(__file__).parents[1] / "config" / "decision-model-v1.json"
    decision = build_provisional_research_decision(
        read_model=model,
        read_model_sha256=_hash(read_model_path.read_bytes()),
        price_raw_path=sources["price_raw"],
        price_manifest_path=sources["price_manifest"],
        macro_proxy_path=sources["macro"],
        valuation_history_path=sources["valuation"],
        config_path=config,
        output_path=decision_path,
    )
    assert decision["decision_status"] == "provisional_research"
    assert decision["research_action"] == "대기"
    assert decision["current_price"]["value"] == pytest.approx(214.13)
    assert decision["current_implied_forward_multiple"] == pytest.approx(22.9940724)
    assert decision["multiple_proxy"]["value"] == pytest.approx(23.7)
    assert decision["regime"]["adjustment"] == pytest.approx(-0.03)
    assert decision["target_per_after_regime"] == pytest.approx(22.989)
    assert decision["provisional_research_base_value"] == pytest.approx(214.0827636)
    assert decision["provisional_research_buy_prices"] == [203.42, 195.93, 188.43]
    assert decision["invalidation_condition"]["eps_average_below"] == pytest.approx(8.38116)
    assert decision["weight_trace"]["normalized_available_only"] == {"market_sector_premium": 1.0}
    assert decision["accounting_basis"] == "unknown"
    assert "독립 적정가가 아니라" in decision["circularity_notice"]
    assert decision["operational_decision"] is None
    assert decision["operational_eligible"] is False

    loaded = load_research_artifacts(
        adjudication_path=adjudication_path,
        read_model_path=read_model_path,
        decision_path=decision_path,
    )
    assert loaded[2]["research_action"] == "대기"


def test_loader_fails_closed_after_immutable_source_change(tmp_path: Path) -> None:
    paths, adjudication_path, report = _adjudicate(tmp_path)
    read_model_path = tmp_path / "read-model.json"
    model = build_consensus_read_model(
        raw_path=paths["raw"], adjudication=report,
        adjudication_sha256=_hash(adjudication_path.read_bytes()), output_path=read_model_path,
    )
    sources = _write_decision_sources(tmp_path)
    decision_path = tmp_path / "decision.json"
    build_provisional_research_decision(
        read_model=model,
        read_model_sha256=_hash(read_model_path.read_bytes()),
        price_raw_path=sources["price_raw"],
        price_manifest_path=sources["price_manifest"],
        macro_proxy_path=sources["macro"],
        valuation_history_path=sources["valuation"],
        config_path=Path(__file__).parents[1] / "config" / "decision-model-v1.json",
        output_path=decision_path,
    )
    paths["raw"].write_bytes(paths["raw"].read_bytes() + b" ")
    with pytest.raises(NvdaResearchError, match="immutable source mismatch"):
        load_research_artifacts(
            adjudication_path=adjudication_path,
            read_model_path=read_model_path,
            decision_path=decision_path,
        )
