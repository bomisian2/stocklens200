from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.decision_model import (
    DecisionModelError,
    backtest_pit_per_anchor,
    build_decision_analysis_snapshot,
    load_decision_config,
    load_decision_inputs,
)


FIXTURE = Path(__file__).parent / "fixtures" / "same_date_inputs_v1.json"
CONFIG = Path(__file__).parents[1] / "config" / "decision-model-v1.json"


def _inputs() -> dict:
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    return load_decision_inputs(FIXTURE, expected_sha256=digest)


def _history(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "source_hash": "a" * 64,
        "available_at": "2026-09-11",
        "sample_gate_passed": True,
        "minimum_sample": 20,
        "sample_count_used": 20 if ticker == "AVGO" else 31,
        "full_history_percentiles": {
            "P35": 31.431997604535702 if ticker == "AVGO" else 39.25852144282254,
            "P50": 39.41570225551989 if ticker == "AVGO" else 44.653060809284646,
        },
    }


def test_same_date_snapshot_is_deterministic_and_uses_completed_session() -> None:
    inputs = _inputs()
    config = load_decision_config(CONFIG)
    first = build_decision_analysis_snapshot("AVGO", inputs=inputs, self_history=_history("AVGO"), config=config)
    second = build_decision_analysis_snapshot("AVGO", inputs=inputs, self_history=_history("AVGO"), config=config)

    assert first == second
    assert first["source_hash"] == second["source_hash"]
    assert first["market_session"] == first["price_as_of"] == "2026-10-13"
    assert first["price_lag_calendar_days"] == 0
    assert first["consensus_available_at"] == first["decision_time"]
    assert first["decision_status"] == "candidate"
    assert first["operational_eligible"] is False


def test_future_information_and_stale_price_are_blocked() -> None:
    config = load_decision_config(CONFIG)
    future = _inputs()
    future["consensus"]["available_at"] = "2026-10-14T12:52:02.419Z"
    with pytest.raises(DecisionModelError, match="future information"):
        build_decision_analysis_snapshot("AVGO", inputs=future, self_history=_history("AVGO"), config=config)

    stale = _inputs()
    stale["prices"]["AVGO"]["market_session"] = "2026-10-01"
    stale["prices"]["AVGO"]["price_as_of"] = "2026-10-01"
    stale["prices"]["AVGO"]["available_at"] = "2026-10-01T20:00:00Z"
    with pytest.raises(DecisionModelError, match="stale"):
        build_decision_analysis_snapshot("AVGO", inputs=stale, self_history=_history("AVGO"), config=config)


def test_eps_basis_cannot_be_mixed() -> None:
    inputs = _inputs()
    inputs["consensus"]["basis"] = "reported_gaap"
    with pytest.raises(DecisionModelError, match="unverified_street_basis"):
        build_decision_analysis_snapshot(
            "AVGO",
            inputs=inputs,
            self_history=_history("AVGO"),
            config=load_decision_config(CONFIG),
        )


def test_actual_candidate_actions_target_per_and_buy_ladder() -> None:
    inputs = _inputs()
    config = load_decision_config(CONFIG)
    avgo = build_decision_analysis_snapshot("AVGO", inputs=inputs, self_history=_history("AVGO"), config=config)
    nvda = build_decision_analysis_snapshot("NVDA", inputs=inputs, self_history=_history("NVDA"), config=config)

    assert avgo["current_price"] == pytest.approx(357.31)
    assert avgo["target_per"] == pytest.approx(35.01254190436358)
    assert avgo["base_fair_value"] == pytest.approx(363.0800595482503)
    assert avgo["price_burden"] == "중립"
    assert avgo["buy_prices"] == [348.56, 334.03, 319.51]
    assert avgo["action"] == "대기"
    assert avgo["action_text"] == "현재는 매수하지 말고 $348.56까지 기다림"

    assert nvda["current_price"] == pytest.approx(228.52)
    assert nvda["target_per"] == pytest.approx(39.92301248280606)
    assert nvda["base_fair_value"] == pytest.approx(369.28786546595603)
    assert nvda["price_burden"] == "낮음"
    assert nvda["buy_prices"] == [228.52, 219.38, 210.24]
    assert nvda["action"] == "분할매수"
    assert nvda["action_text"] == "현재 가격에서 1차 매수를 시작"

    for result in (avgo, nvda):
        assert 0.05 <= result["buy_ladder_span_of_first_price"] <= 0.12
        assert result["calculation_trace"]["historical_forward_per"]["weight"] == 0.5
        assert result["calculation_trace"]["growth_revision"]["weight"] == 0.3
        assert result["calculation_trace"]["market_sector_premium"]["weight"] == 0.2
        assert result["calculation_trace"]["regime"]["adjustment"] == pytest.approx(-0.01)
        assert any(not gate["passed"] for gate in result["promotion_gates"])


def test_snapshot_hash_change_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    path = tmp_path / "inputs.json"
    original = json.dumps(payload, sort_keys=True).encode("utf-8")
    path.write_bytes(original)
    approved = hashlib.sha256(original).hexdigest()
    load_decision_inputs(path, expected_sha256=approved)
    payload["prices"]["AVGO"]["value"] = 999.0
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(DecisionModelError, match="SHA-256"):
        load_decision_inputs(path, expected_sha256=approved)


def _field(value, as_of: str) -> dict:
    return {"value": value, "as_of": as_of}


def test_partial_pit_backtest_uses_split_adjusted_entry_and_no_future_rows() -> None:
    rows = []
    per_values = [50, 48, 46, 44, 42, 20, 21]
    dates = ["2022-01-15", "2022-02-15", "2022-03-15", "2022-04-15", "2022-05-15", "2022-06-15", "2022-07-15"]
    for index, (as_of, per_value) in enumerate(zip(dates, per_values, strict=True)):
        rows.append(
            {
                "filing_date": as_of,
                "split_basis_factor": 10.0,
                "price": _field(100.0 + index * 10.0, as_of),
                "ttm_per": _field(float(per_value), as_of),
            }
        )
    history = [
        {"date": "2022-12-31", "close": 20.0},
        {"date": "2023-06-30", "close": 25.0},
        {"date": "2023-07-31", "close": 30.0},
    ]
    result = backtest_pit_per_anchor({"historical_per": rows}, history)

    assert result["ledger_row_count"] == 7
    assert result["signal_count"] == 2
    assert result["future_information_used"] is False
    assert result["benchmark_status"] == "unavailable_no_qqq_monthly_history"
    assert result["operational_eligible"] is False


def test_config_and_inputs_are_not_mutated() -> None:
    inputs = _inputs()
    config = load_decision_config(CONFIG)
    inputs_before = copy.deepcopy(inputs)
    config_before = copy.deepcopy(config)
    build_decision_analysis_snapshot("NVDA", inputs=inputs, self_history=_history("NVDA"), config=config)
    assert inputs == inputs_before
    assert config == config_before
