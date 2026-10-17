from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from stocklens.analysis import (
    ANALYSIS_AREAS,
    AnalysisSchemaError,
    build_analysis_catalog,
    catalog_table,
    coverage_metrics,
    find_company,
    load_analysis_overlays,
    load_signal_research,
)
from stocklens.mvp_context import with_runtime_freshness
from stocklens.storage import SnapshotStore


ROOT = Path(__file__).resolve().parents[1]


def _inputs() -> tuple[list[dict], dict, dict]:
    store = SnapshotStore(ROOT / "data")
    universe = store.load()
    status = store.load_status()
    overlays = load_analysis_overlays(ROOT / "research" / "mvp-analysis-overlays.json")
    signals = load_signal_research(ROOT / "research" / "signal-research-summary.json")
    return build_analysis_catalog(universe, status, overlays), status, signals


def test_catalog_has_all_companies_and_six_independent_areas() -> None:
    catalog, status, _ = _inputs()
    assert len(catalog) == 200
    assert len({row["ticker"] for row in catalog}) == 200
    assert len(catalog_table(catalog)) == 200
    for row in catalog:
        assert set(row["areas"]) == set(ANALYSIS_AREAS)
        assert row["market_session_date"] == status["market_session_date"]


def test_pilot_coverage_is_truthful() -> None:
    catalog, _, signals = _inputs()
    googl = find_company(catalog, "GOOGL")
    assert googl["official_financial_periods"] == 11
    assert googl["areas"]["financial"]["state"] == "research_only"
    assert googl["areas"]["valuation"]["state"] == "research_only"
    assert googl["research_value"]["range"] == {"low": 41.63, "high": 84.88, "currency": "USD"}
    assert googl["research_value"]["formal_value"] is None
    assert googl["research_value"]["midpoint"] is None
    assert googl["research_value"]["operational_eligible"] is False
    assert googl["research_value"]["judgement"] == "hold"

    for ticker in ("AMAT", "AVGO", "QCOM"):
        company = find_company(catalog, ticker)
        assert company["official_financial_periods"] == 0
        assert company["areas"]["financial"]["state"] == "unavailable"
        assert company["areas"]["valuation"]["state"] == "blocked"
        assert company["research_value"] is None

    metrics = coverage_metrics(catalog, signals)
    assert metrics == {
        "company_count": 200,
        "official_financial_companies": 1,
        "research_value_companies": 1,
        "formal_valuation_companies": 0,
        "independent_signal_count": 0,
        "active_signal_count": 0,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["companies"][0]["research_value"].update({"operational_eligible": True}), "operationally ineligible"),
        (lambda payload: payload["companies"][0]["research_value"].update({"formal_value": 100}), "formal value"),
        (lambda payload: payload["companies"][0]["research_value"].update({"midpoint": 60}), "formal value"),
        (lambda payload: payload["companies"][0]["research_value"].update({"recommendation": "buy"}), "prohibited keys"),
        (lambda payload: payload["companies"][0]["research_value"].update({"automatic_price_comparison": True}), "cannot compare"),
    ],
)
def test_research_overlay_rejects_operational_leakage(tmp_path: Path, mutation, message: str) -> None:
    payload = json.loads((ROOT / "research" / "mvp-analysis-overlays.json").read_text(encoding="utf-8"))
    mutation(payload)
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AnalysisSchemaError, match=message):
        load_analysis_overlays(path)


def test_signal_summary_rejects_false_operational_success(tmp_path: Path) -> None:
    payload = json.loads((ROOT / "research" / "signal-research-summary.json").read_text(encoding="utf-8"))
    payload["active_signal_count"] = 1
    path = tmp_path / "signals.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AnalysisSchemaError, match="cannot publish active"):
        load_signal_research(path)


def test_runtime_freshness_separates_snapshot_quality_from_current_staleness() -> None:
    store = SnapshotStore(ROOT / "data")
    universe = store.load()
    recorded = store.load_status()

    same_session = with_runtime_freshness(
        universe,
        recorded,
        datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
    )
    assert same_session["data_state"] == "healthy"
    assert same_session["recorded_freshness_state"] == "verified"
    assert same_session["runtime_freshness_state"] == "verified"
    assert same_session["runtime_eligible_for_signals"] is True

    next_completed_session = with_runtime_freshness(
        universe,
        recorded,
        datetime(2026, 10, 12, 22, tzinfo=timezone.utc),
    )
    assert next_completed_session["data_state"] == "healthy"
    assert next_completed_session["runtime_expected_session_date"] == "2026-10-12"
    assert next_completed_session["runtime_freshness_state"] == "stale"
    assert next_completed_session["runtime_eligible_for_signals"] is False
    catalog = build_analysis_catalog(universe, next_completed_session, {})
    assert {company["areas"]["market"]["state"] for company in catalog} == {"stale"}
