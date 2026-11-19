from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stocklens.personal.decision_model import (
    DecisionModelError,
    backtest_pit_per_anchor,
    build_decision_analysis_snapshot,
    calculate_target_multiple,
    load_decision_config,
    load_decision_inputs,
    walk_forward_full_model,
)
from stocklens.personal.same_date_builder import build_same_date_snapshot


SOURCE_FIXTURES = Path(__file__).parent / "fixtures" / "same_date_sources"
CONFIG = Path(__file__).parents[1] / "config" / "decision-model-v1.json"


def _build_inputs(tmp_path: Path) -> tuple[Path, dict]:
    path = build_same_date_snapshot(
        decision_ledger_path=SOURCE_FIXTURES / "decision-ledger.json",
        consensus_path=SOURCE_FIXTURES / "consensus.json",
        regime_path=SOURCE_FIXTURES / "regime.json",
        output_dir=tmp_path,
        decision_time="2026-10-14T12:52:02.418Z",
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, load_decision_inputs(path, expected_sha256=digest, source_root=tmp_path)


def _legacy_history(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "source_hash": "a" * 64,
        "available_at": "2026-09-11",
        "sample_gate_passed": True,
        "minimum_sample": 20,
        "sample_count_used": 20 if ticker == "AVGO" else 31,
        "multiple_basis": "ttm_per",
        "eps_basis": "reported_ttm_unknown",
        "full_history_percentiles": {"P35": 31.43, "P50": 39.42},
    }


def test_builder_and_loader_verify_raw_sources(tmp_path: Path) -> None:
    path, inputs = _build_inputs(tmp_path)
    assert inputs["source_evidence_verified"] is True
    assert inputs["prices"]["AVGO"]["price_as_of"] == "2026-10-13"
    assert inputs["prices"]["AVGO"]["market_session"] == "2026-10-13"
    assert inputs["consensus"]["basis"] == "unverified_street_basis"
    assert path.parent.joinpath("evidence", "consensus.json").is_file()
    second_path = build_same_date_snapshot(
        decision_ledger_path=SOURCE_FIXTURES / "decision-ledger.json",
        consensus_path=SOURCE_FIXTURES / "consensus.json",
        regime_path=SOURCE_FIXTURES / "regime.json",
        output_dir=tmp_path / "second",
        decision_time="2026-10-14T12:52:02.418Z",
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(second_path.read_bytes()).hexdigest()


def test_raw_source_missing_and_hash_mismatch_are_blocked(tmp_path: Path) -> None:
    path, _ = _build_inputs(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    consensus_path = tmp_path / "evidence" / "consensus.json"
    original = consensus_path.read_bytes()
    consensus_path.unlink()
    with pytest.raises(DecisionModelError, match="raw source file is missing"):
        load_decision_inputs(path, expected_sha256=digest, source_root=tmp_path)
    consensus_path.write_bytes(original + b" ")
    with pytest.raises(DecisionModelError, match="raw source hash mismatch"):
        load_decision_inputs(path, expected_sha256=digest, source_root=tmp_path)


def test_normalized_basis_cannot_claim_more_than_raw_source(tmp_path: Path) -> None:
    path, _ = _build_inputs(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["consensus"]["basis"] = "gaap_fy1"
    path.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(DecisionModelError, match="does not verify"):
        load_decision_inputs(path, expected_sha256=digest, source_root=tmp_path)


def test_raw_generated_at_must_match_manifest_and_decision_time(tmp_path: Path) -> None:
    path, _ = _build_inputs(tmp_path)
    ledger_path = tmp_path / "evidence" / "decision_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["generatedAt"] = "2026-10-14T13:30:00Z"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    ledger_hash = hashlib.sha256(ledger_path.read_bytes()).hexdigest().upper()
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["source_evidence"]["decision_ledger"]["sha256"] = ledger_hash
    for ticker in ("AVGO", "NVDA"):
        snapshot["prices"][ticker]["source_hash"] = ledger_hash
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(DecisionModelError, match="generatedAt does not match"):
        load_decision_inputs(path, expected_sha256=digest, source_root=tmp_path)


def test_ttm_forward_basis_mismatch_disables_action(tmp_path: Path) -> None:
    _, inputs = _build_inputs(tmp_path)
    result = build_decision_analysis_snapshot(
        "AVGO", inputs=inputs, self_history=_legacy_history("AVGO"), config=load_decision_config(CONFIG)
    )
    assert result["status"] == "blocked"
    assert result["target_per"] is None
    assert result["base_fair_value"] is None
    assert result["buy_prices"] == []
    assert result["action"] == "비활성"
    assert result["candidate_arithmetic"]["historical_multiple_basis"] == "ttm_per"
    assert result["candidate_arithmetic"]["used_for_action"] is False


def test_action_remains_blocked_when_operational_evidence_is_not_loader_verified(tmp_path: Path) -> None:
    _, inputs = _build_inputs(tmp_path)
    inputs["consensus_basis_verified"] = True
    inputs["consensus"]["basis"] = "gaap_fy1"
    inputs["benchmark_multiples"] = {
        "AVGO": {
            "verified": True,
            "eps_basis": "gaap_fy1",
            "relative_forward_per": 32.0,
            "source": "synthetic independent benchmark",
            "source_hash": "e" * 64,
        }
    }
    inputs["walk_forward_validation"] = {
        "AVGO": {
            "status": "verified_actual_holdout",
            "passed": True,
            "future_information_used": False,
        }
    }
    inputs["data_rights"] = {"status": "verified", "decision_display_allowed": True}
    history = _legacy_history("AVGO")
    history.update(
        {
            "multiple_basis": "pit_forward_per",
            "eps_basis": "gaap_fy1",
            "forward_per_percentiles": {"P35": 30.0},
        }
    )
    result = build_decision_analysis_snapshot(
        "AVGO", inputs=inputs, self_history=history, config=load_decision_config(CONFIG)
    )
    assert result["status"] == "blocked"
    assert result["action"] == "비활성"
    assert result["buy_prices"] == []
    assert any("loader" in blocker for blocker in result["blockers"])
    assert next(gate for gate in result["promotion_gates"] if gate["id"] == "walk_forward_final_holdout")["passed"] is True
    assert next(gate for gate in result["promotion_gates"] if gate["id"] == "data_rights")["passed"] is True
    assert next(gate for gate in result["promotion_gates"] if gate["id"] == "operational_evidence_loader")["passed"] is False


def test_price_session_mismatch_and_stale_or_future_regime_are_blocked(tmp_path: Path) -> None:
    _, inputs = _build_inputs(tmp_path)
    config = load_decision_config(CONFIG)
    mismatched = copy.deepcopy(inputs)
    mismatched["prices"]["AVGO"]["price_as_of"] = "2026-10-12"
    with pytest.raises(DecisionModelError, match="price_as_of"):
        build_decision_analysis_snapshot("AVGO", inputs=mismatched, self_history=_legacy_history("AVGO"), config=config)

    stale = copy.deepcopy(inputs)
    stale["regime"]["as_of"] = "2026-09-01"
    with pytest.raises(DecisionModelError, match="regime is stale"):
        build_decision_analysis_snapshot("AVGO", inputs=stale, self_history=_legacy_history("AVGO"), config=config)

    future = copy.deepcopy(inputs)
    future["regime"]["as_of"] = "2026-10-15"
    with pytest.raises(DecisionModelError, match="future regime"):
        build_decision_analysis_snapshot("AVGO", inputs=future, self_history=_legacy_history("AVGO"), config=config)


def test_independent_target_multiple_does_not_accept_current_price() -> None:
    weights = {"self_history_forward_per": 0.5, "growth_revision": 0.3, "market_sector_premium": 0.2}
    axes = {"self_history_forward_per": 30.0, "growth_revision": 36.0, "market_sector_premium": 32.0}
    first, _ = calculate_target_multiple(axes, weights, regime_adjustment=-0.01)
    synthetic_current_prices = (100.0, 999.0)
    results = [calculate_target_multiple(axes, weights, regime_adjustment=-0.01)[0] for _ in synthetic_current_prices]
    assert results == pytest.approx([first, first])


def test_target_multiple_renormalizes_only_verified_axes() -> None:
    target, trace = calculate_target_multiple(
        {"self_history_forward_per": 30.0, "growth_revision": 36.0, "market_sector_premium": None},
        {"self_history_forward_per": 0.5, "growth_revision": 0.3, "market_sector_premium": 0.2},
        regime_adjustment=-0.02,
    )
    assert trace["normalized_weights"] == pytest.approx(
        {"self_history_forward_per": 0.625, "growth_revision": 0.375}
    )
    assert target == pytest.approx((30.0 * 0.625 + 36.0 * 0.375) * 0.98)


def test_date_only_filing_blocks_same_day_close_backtest() -> None:
    result = backtest_pit_per_anchor(
        {
            "historical_per": [
                {
                    "filing_date": "2026-01-01",
                    "availability_precision": "date_only_no_acceptance_timestamp",
                    "ttm_per": {"value": 20.0},
                }
            ]
        },
        [{"date": "2026-01-02", "close": 100.0}],
    )
    assert result["status"] == "blocked_acceptance_time_unknown"
    assert result["future_information_used"] == "unknown"
    assert result["horizons"] == {}


def test_exact_acceptance_backtest_is_input_order_independent() -> None:
    rows = [
        {
            "acceptance_timestamp": f"2020-{month:02d}-01T21:00:00Z",
            "ttm_per": {"value": value},
        }
        for month, value in enumerate((50, 48, 46, 44, 42, 20, 21), start=1)
    ]
    prices = [
        {"date": f"2020-{month:02d}-02", "close": 100.0 + month}
        for month in range(1, 13)
    ] + [
        {"date": f"2021-{month:02d}-02", "close": 120.0 + month}
        for month in range(1, 13)
    ]
    chronological = backtest_pit_per_anchor({"historical_per": rows}, prices)
    reversed_input = backtest_pit_per_anchor({"historical_per": list(reversed(rows))}, prices)
    assert chronological == reversed_input
    assert chronological["future_information_used"] is False


def _walk_forward_rows() -> list[dict]:
    start = datetime(2020, 1, 1, 21, tzinfo=timezone.utc)
    rows = []
    for index in range(20):
        decision = start + timedelta(days=index * 30)
        rows.append(
            {
                "decision_time": decision.isoformat(),
                "available_at": (decision - timedelta(hours=2)).isoformat(),
                "entry_time": (decision + timedelta(days=1)).isoformat(),
                "exit_time": (decision + timedelta(days=4)).isoformat(),
                "qqq_entry_time": (decision + timedelta(days=1)).isoformat(),
                "qqq_exit_time": (decision + timedelta(days=4)).isoformat(),
                "soxx_entry_time": (decision + timedelta(days=1)).isoformat(),
                "soxx_exit_time": (decision + timedelta(days=4)).isoformat(),
                "eps_basis": "gaap_fy1",
                "benchmark_verified": True,
                "forward_per": 22.0 if index >= 12 else 26.0 + index * 0.1,
                "growth_revision_per": 30.0,
                "benchmark_forward_per": 28.0,
                "regime_adjustment": -0.01,
                "entry_price": 100.0,
                "exit_price": 112.0 + index,
                "qqq_entry_price": 100.0,
                "qqq_exit_price": 108.0,
                "soxx_entry_price": 100.0,
                "soxx_exit_price": 110.0,
                "daily_adjusted_path": [
                    {"date": (decision + timedelta(days=1)).date().isoformat(), "close": 100.0},
                    {"date": (decision + timedelta(days=2)).date().isoformat(), "close": 96.0},
                    {"date": (decision + timedelta(days=3)).date().isoformat(), "close": 105.0},
                    {"date": (decision + timedelta(days=4)).date().isoformat(), "close": 112.0 + index},
                ],
            }
        )
    return rows


def test_full_model_walk_forward_uses_chronological_holdout_and_daily_mdd() -> None:
    result = walk_forward_full_model(
        _walk_forward_rows(),
        weights={"self_history_forward_per": 0.5, "growth_revision": 0.3, "market_sector_premium": 0.2},
    )
    assert result["status"] == "fixture_validated_full_model_walk_forward"
    assert result["training_rows"] == 12
    assert result["validation"]["sample_count"] == 4
    assert result["holdout"]["sample_count"] == 4
    assert result["holdout"]["median_qqq_excess"] is not None
    assert result["holdout"]["median_soxx_excess"] is not None
    assert result["holdout"]["maximum_drawdown"] == pytest.approx(-0.04)
    assert result["future_information_used"] is False
    assert result["operational_eligible"] is False


def test_full_model_walk_forward_blocks_incomplete_actual_evidence() -> None:
    result = walk_forward_full_model(
        [],
        weights={"self_history_forward_per": 0.5, "growth_revision": 0.3, "market_sector_premium": 0.2},
    )
    assert result["status"] == "blocked_insufficient_full_model_rows"
    assert result["operational_eligible"] is False


def test_full_model_blocks_entry_before_or_at_decision_time() -> None:
    rows = _walk_forward_rows()
    rows[0]["entry_time"] = rows[0]["decision_time"]
    rows[0]["qqq_entry_time"] = rows[0]["entry_time"]
    rows[0]["soxx_entry_time"] = rows[0]["entry_time"]
    with pytest.raises(DecisionModelError, match="future-information boundary"):
        walk_forward_full_model(
            rows,
            weights={"self_history_forward_per": 0.5, "growth_revision": 0.3, "market_sector_premium": 0.2},
        )


def test_inputs_and_config_are_not_mutated(tmp_path: Path) -> None:
    _, inputs = _build_inputs(tmp_path)
    config = load_decision_config(CONFIG)
    inputs_before = copy.deepcopy(inputs)
    config_before = copy.deepcopy(config)
    build_decision_analysis_snapshot("NVDA", inputs=inputs, self_history=_legacy_history("NVDA"), config=config)
    assert inputs == inputs_before
    assert config == config_before
