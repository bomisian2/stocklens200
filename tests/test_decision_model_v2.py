from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.decision_model_v2 import (
    DecisionModelV2Error,
    build_proxy_decision,
    load_decision,
    preserve_existing_artifacts,
    validate_decision,
    write_decision_and_ledger,
)


DECISION_TIME = "2026-10-20T01:32:32.832654Z"
TEST_COMMIT_SHA = "a" * 40


def _write(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _fixture(tmp_path: Path, *, price: float = 217.09, peer_count: int = 8) -> dict[str, Path]:
    data = tmp_path / "top10"
    generated = "2026-10-19T22:35:57.807Z"
    price_path = _write(data / "price-history.json", {
        "generatedAt": generated,
        "records": {"NVDA": [{"date": "2026-10-19", "close": price}]},
    })
    target = {
        "ticker": "NVDA", "collectedAt": generated,
        "providerId": "nasdaq-analyst-earnings-forecast", "collectionStatus": "success",
        "fiscalEnd": "Jan 2027", "consensusEps": 9.30, "lowEps": 9.01, "highEps": 9.71,
        "nextFiscalEnd": "Jan 2028", "nextConsensusEps": 15.74,
        "analystCount": 17, "revisionsUp4w": 2, "revisionsDown4w": 0,
        "currency": "USD", "accountingBasis": "provider_adjusted_unspecified", "epsBasis": "unknown",
    }
    records = {"NVDA": target}
    valuations: dict[str, list[dict]] = {}
    industries = {"NVDA": {"sector": "AI 반도체", "etf": "SOXX"}}
    periods = ["Dec 2026", "Dec 2026", "Jan 2027", "Mar 2027", "Dec 2026", "Jan 2027", "Dec 2026", "Dec 2026"]
    multiples = [24.0, 28.0, 32.0, 36.0, 40.0, 44.0, 48.0, 200.0]
    for index in range(peer_count):
        ticker = f"P{index + 1}"
        period = periods[index]
        records[ticker] = {
            "ticker": ticker, "collectedAt": generated,
            "providerId": "nasdaq-analyst-earnings-forecast", "collectionStatus": "success",
            "fiscalEnd": period, "consensusEps": 5.0, "lowEps": 4.5, "highEps": 5.5,
            "nextFiscalEnd": "Dec 2027", "nextConsensusEps": 6.5 + index * 0.1,
            "analystCount": 10, "revisionsUp4w": 1, "revisionsDown4w": 0,
            "currency": "USD", "accountingBasis": "provider_adjusted_unspecified", "epsBasis": "unknown",
        }
        valuations[ticker] = [{
            "asOf": "2026-10-19", "expectedPe": multiples[index], "estimatePeriod": period,
            "pointInTimeVerified": False,
        }]
        industries[ticker] = {
            "sector": "반도체",
            "etf": "SOXX",
            "peerGroup": ["ai_chips", "networking", "analog_wireless", "chip_equipment", "chip_manufacturing"][index % 5],
        }
    consensus_path = _write(data / "top100-consensus.json", {"generatedAt": generated, "records": records})
    valuation_path = _write(data / "valuation-history.json", {"generatedAt": generated, "records": valuations})
    industry_path = _write(data / "industry-map.json", {"generatedAt": generated, "records": industries})
    macro_path = _write(data / "macro-snapshot.json", {
        "generatedAt": generated, "state": "경계", "score": -1, "reasons": ["fixture risk"],
    })
    config_path = Path(__file__).parents[1] / "config" / "decision-model-v2.json"
    return {
        "price": price_path, "consensus": consensus_path, "valuation": valuation_path,
        "industry": industry_path, "macro": macro_path, "config": config_path,
    }


def _build(paths: dict[str, Path]) -> dict:
    return build_proxy_decision(
        price_history_path=paths["price"], consensus_path=paths["consensus"],
        valuation_history_path=paths["valuation"], industry_map_path=paths["industry"],
        macro_path=paths["macro"], config_path=paths["config"], decision_time=DECISION_TIME,
        code_commit_sha=TEST_COMMIT_SHA,
    )


def test_proxy_action_is_price_invariant_and_non_operational(tmp_path: Path) -> None:
    results = []
    for price in (100.0, 217.09, 999.0):
        paths = _fixture(tmp_path / str(price), price=price)
        results.append(_build(paths))
    multiples = [row["calculation"]["target_forward_multiple"] for row in results]
    bases = [row["scenario_values"]["base"] for row in results]
    assert multiples[0] == pytest.approx(multiples[1]) == pytest.approx(multiples[2])
    assert bases[0] == pytest.approx(bases[1]) == pytest.approx(bases[2])
    assert [row["action"] for row in results] == ["분할매수 시작", "분할매수 시작", "신규 매수 중단·비중 축소 검토"]
    assert all(row["calculation"]["current_price_used_in_target_multiple"] is False for row in results)
    assert all(row["peer_gate"]["stability"]["current_price_used"] is False for row in results)
    assert all(row["verified_action"] is None and row["operational_eligible"] is False for row in results)
    assert results[1]["execution_buy_prices"] == pytest.approx([217.09, 206.2355, 195.381])
    assert all(value <= 217.09 for value in results[1]["execution_buy_prices"])
    assert results[2]["execution_buy_prices"] is None


def test_forward_period_boundary_and_minimum_peer_gate(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, peer_count=5)
    consensus = json.loads(paths["consensus"].read_text(encoding="utf-8"))
    consensus["records"]["P1"]["fiscalEnd"] = "Apr 2027"
    paths["consensus"].write_text(json.dumps(consensus), encoding="utf-8")
    valuation = json.loads(paths["valuation"].read_text(encoding="utf-8"))
    valuation["records"]["P1"][0]["estimatePeriod"] = "Apr 2027"
    paths["valuation"].write_text(json.dumps(valuation), encoding="utf-8")
    result = _build(paths)
    assert next(row for row in result["peer_gate"]["pre_outlier_peers"] if row["ticker"] == "P1")["period_gap_days"] == 89

    consensus["records"]["P1"]["fiscalEnd"] = "May 2027"
    paths["consensus"].write_text(json.dumps(consensus), encoding="utf-8")
    with pytest.raises(DecisionModelV2Error, match="4/5"):
        _build(paths)


def test_outlier_filter_requires_five_remaining_peers(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, peer_count=5)
    valuation = json.loads(paths["valuation"].read_text(encoding="utf-8"))
    for index, ticker in enumerate(valuation["records"]):
        valuation["records"][ticker][0]["expectedPe"] = [20.0, 21.0, 22.0, 23.0, 500.0][index]
    paths["valuation"].write_text(json.dumps(valuation), encoding="utf-8")
    with pytest.raises(DecisionModelV2Error, match="after outlier filter"):
        _build(paths)


def test_future_sources_and_future_valuation_rows_are_blocked_or_excluded(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    valuation = json.loads(paths["valuation"].read_text(encoding="utf-8"))
    valuation["records"]["P1"].append({
        "asOf": "2026-10-21", "expectedPe": 9999.0, "estimatePeriod": "Dec 2026",
    })
    paths["valuation"].write_text(json.dumps(valuation), encoding="utf-8")
    result = _build(paths)
    assert result["calculation"]["peer_median_before_adjustment"] < 100

    macro = json.loads(paths["macro"].read_text(encoding="utf-8"))
    macro["generatedAt"] = "2026-10-21T00:00:00Z"
    paths["macro"].write_text(json.dumps(macro), encoding="utf-8")
    with pytest.raises(DecisionModelV2Error, match="future data"):
        _build(paths)


def test_adjustment_caps_execution_ladder_and_invalidation_are_separate(tmp_path: Path) -> None:
    result = _build(_fixture(tmp_path))
    calc = result["calculation"]
    assert abs(calc["growth_adjustment"]) <= 0.15
    assert abs(calc["revision_adjustment"]) <= 0.05
    assert abs(calc["combined_growth_revision_adjustment"]) <= 0.20
    assert abs(calc["regime"]["adjustment"]) <= 0.10
    base = result["scenario_values"]["base"]
    assert result["valuation_entry_ceiling"] == pytest.approx(base * 0.97)
    current = result["current_price"]["value"]
    assert result["execution_buy_prices"] == pytest.approx([current, current * 0.95, current * 0.90])
    assert "buy_prices" not in result
    assert result["legacy_buy_prices_status"] == "deprecated_v2.1"
    assert result["reduction_review_price"] == pytest.approx(base * 1.15)
    expected_invalidation_eps = result["current_price"]["value"] / (
        result["calculation"]["target_forward_multiple"] * 0.97
    )
    assert result["valuation_entry_eps_floor"]["value"] == pytest.approx(expected_invalidation_eps)
    assert result["fundamental_invalidation"]["triggered"] is False
    assert "current_price /" in result["valuation_entry_eps_floor"]["formula"]


def test_wait_and_reduction_actions_do_not_precreate_execution_orders(tmp_path: Path) -> None:
    baseline = _build(_fixture(tmp_path / "baseline", price=100.0))
    base = baseline["scenario_values"]["base"]
    ceiling = baseline["valuation_entry_ceiling"]
    waiting = _build(_fixture(tmp_path / "wait", price=(ceiling + base) / 2))
    assert waiting["action"] == "대기"
    assert waiting["execution_buy_prices"] is None
    assert waiting["execution_anchor_price"] is None
    reducing = _build(_fixture(tmp_path / "reduce", price=base * 1.16))
    assert reducing["action"] == "신규 매수 중단·비중 축소 검토"
    assert reducing["execution_buy_prices"] is None


def test_peer_stability_reports_leave_one_out_and_unstable_case(tmp_path: Path) -> None:
    stable = _build(_fixture(tmp_path / "stable"))
    stability = stable["peer_gate"]["stability"]
    assert len(stability["leave_one_out"]) == stable["peer_gate"]["post_outlier_count"]
    assert stability["minimum_forward_per"] <= stability["median_forward_per"] <= stability["maximum_forward_per"]
    assert stability["trimmed_target_multiple"] > 0
    assert len(stability["peer_contributions"]) == stable["peer_gate"]["post_outlier_count"]

    paths = _fixture(tmp_path / "unstable", peer_count=5)
    valuation = json.loads(paths["valuation"].read_text(encoding="utf-8"))
    for ticker, multiple in zip(valuation["records"], [10.0, 10.0, 10.0, 20.0, 20.0]):
        valuation["records"][ticker][0]["expectedPe"] = multiple
    paths["valuation"].write_text(json.dumps(valuation), encoding="utf-8")
    unstable = _build(paths)
    assert unstable["peer_gate"]["stability"]["status"] == "peer_unstable"
    assert unstable["proxy_value_range"]["low"] <= unstable["proxy_value_range"]["high"]


def test_peer_group_diagnostics_do_not_replace_broad_proxy(tmp_path: Path) -> None:
    result = _build(_fixture(tmp_path))
    groups = {row["group"]: row for row in result["peer_gate"]["groups"]}
    assert groups["broad_semiconductors"]["eligible_for_overall_proxy"] is True
    assert groups["ai_compute_accelerator"]["diagnostic_only"] is True
    assert groups["semiconductor_design_network"]["diagnostic_only"] is True
    required = {
        "count", "minimum_required", "meets_minimum",
        "eligible_for_overall_proxy", "diagnostic_only",
    }
    assert all(required <= set(group) for group in groups.values())


def test_build_write_load_recalculate_round_trip(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    built = _build(paths)
    decision_path = tmp_path / "private_data" / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = tmp_path / "private_data" / "decision" / "decision-model-v2.1-signal-ledger.json"
    write_decision_and_ledger(
        decision=built,
        decision_path=decision_path,
        ledger_path=ledger_path,
        replace_ledger=True,
    )
    loaded = load_decision(decision_path)
    rebuilt = _build(paths)
    assert loaded == built == rebuilt
    assert loaded["calculation_sha256"] == rebuilt["calculation_sha256"]
    assert loaded["code_commit_sha"] == TEST_COMMIT_SHA
    assert loaded["generator_version"] == "decision-model-v2.1-generator-v2"


def test_legacy_v21_missing_meets_minimum_has_specific_error(tmp_path: Path) -> None:
    result = _build(_fixture(tmp_path))
    result["peer_gate"]["groups"][0].pop("meets_minimum")
    with pytest.raises(
        DecisionModelV2Error,
        match="peer_gate group schema mismatch: meets_minimum missing",
    ):
        validate_decision(result)


def test_existing_artifacts_are_archived_before_rebuild(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = _build(paths)
    decision_path = tmp_path / "private_data" / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = tmp_path / "private_data" / "decision" / "decision-model-v2.1-signal-ledger.json"
    write_decision_and_ledger(decision=result, decision_path=decision_path, ledger_path=ledger_path)
    decision_bytes = decision_path.read_bytes()
    ledger_bytes = ledger_path.read_bytes()
    audit_path = preserve_existing_artifacts(
        decision_path=decision_path,
        ledger_path=ledger_path,
        reason="schema drift — peer group meets_minimum field missing",
    )
    assert audit_path is not None
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["replacement_reason"] == "schema drift — peer group meets_minimum field missing"
    assert Path(audit["previous_decision"]["archive_path"]).read_bytes() == decision_bytes
    assert Path(audit["previous_ledger"]["archive_path"]).read_bytes() == ledger_bytes


def test_artifact_and_source_hashes_fail_closed_and_ledger_is_versioned(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = _build(paths)
    decision_path = tmp_path / "private_data" / "decision" / "nvda-v2.json"
    ledger_path = tmp_path / "private_data" / "decision" / "ledger-v2.json"
    write_decision_and_ledger(decision=result, decision_path=decision_path, ledger_path=ledger_path)
    loaded = load_decision(decision_path)
    assert loaded["decision_status"] == "proxy_action"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["ledger_version"] == "decision-model-v2.1-signal-ledger-v1"
    assert len(ledger["records"]) == 1
    assert ledger["records"][0]["performance"]["return_6m"] is None
    assert ledger["records"][0]["execution_policy_version"] == "current-price-ladder-v1"

    tampered = copy.deepcopy(result)
    tampered["calculation"]["target_forward_multiple"] += 1
    with pytest.raises(DecisionModelV2Error, match="calculation SHA"):
        validate_decision(tampered)

    paths["macro"].write_text(json.dumps({"generatedAt": "2026-10-19T22:35:57Z", "score": 0}), encoding="utf-8")
    with pytest.raises(DecisionModelV2Error, match="source hash"):
        load_decision(decision_path)


def test_artifact_tamper_of_execution_price_fails_closed(tmp_path: Path) -> None:
    result = _build(_fixture(tmp_path))
    result["execution_buy_prices"][1] += 1.0
    with pytest.raises(DecisionModelV2Error, match="calculation SHA"):
        validate_decision(result)


def test_tukey_outlier_is_removed_and_proxy_is_never_verified(tmp_path: Path) -> None:
    result = _build(_fixture(tmp_path))
    assert result["peer_gate"]["pre_outlier_count"] == 8
    assert result["peer_gate"]["post_outlier_count"] == 7
    assert any(row["reason"] == "tukey_1_5_iqr_outlier" for row in result["peer_gate"]["excluded_peers"])
    assert result["proxy_classification"] == "provider_homogeneous_proxy"
    assert result["peer_gate"]["point_in_time_verified"] is False
    assert result["verified_action"] is None


def test_decision_v21_has_no_p5_sources(tmp_path: Path) -> None:
    result = _build(_fixture(tmp_path))
    assert set(result["sources"]) == {
        "price_history", "consensus", "valuation_history", "industry_map", "macro", "config"
    }
    assert not any("p5" in role.lower() for role in result["sources"])
