from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.decision_model_v2 import (
    DecisionModelV2Error,
    _confidence_position_policy,
    _decision_calculation_sha256,
    build_proxy_decision,
    build_proxy_decision_from_bundle,
    load_decision,
    preserve_existing_artifacts,
    validate_decision,
    write_decision_and_ledger,
)
from stocklens.personal.decision_input_bundle import (
    create_input_bundle,
    detect_parent_input_updates,
    file_sha256,
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
    evidence_hash = hashlib.sha256(consensus_path.read_bytes()).hexdigest()
    evidence_path = data / "consensus-evidence" / "2026-10-19" / f"{evidence_hash}.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(consensus_path.read_bytes())
    _write(data / "consensus-evidence" / "manifest.json", {
        "version": 1,
        "artifacts": {
            evidence_hash: {
                "evidenceFile": f"consensus-evidence/2026-10-19/{evidence_hash}.json",
                "evidenceGeneratedAt": generated,
                "evidenceCommitSha": None,
                "evidenceArtifactId": f"sha256:{evidence_hash}",
            }
        },
    })
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


def _build_bundled(paths: dict[str, Path], private_root: Path) -> tuple[dict, Path, str]:
    bundle_dir, _manifest, manifest_sha = create_input_bundle(
        data_root=paths["price"].parent,
        config_path=paths["config"],
        decision_root=private_root / "decision",
        decision_time=DECISION_TIME,
        code_commit_sha=TEST_COMMIT_SHA,
    )
    decision = build_proxy_decision_from_bundle(
        bundle_dir=bundle_dir,
        manifest_sha256=manifest_sha,
        code_commit_sha=TEST_COMMIT_SHA,
    )
    return decision, bundle_dir, manifest_sha


def test_proxy_action_is_price_invariant_and_non_operational(tmp_path: Path) -> None:
    results = []
    for price in (100.0, 217.09, 999.0):
        paths = _fixture(tmp_path / str(price), price=price)
        results.append(_build(paths))
    multiples = [row["calculation"]["target_forward_multiple"] for row in results]
    bases = [row["scenario_values"]["base"] for row in results]
    assert multiples[0] == pytest.approx(multiples[1]) == pytest.approx(multiples[2])
    assert bases[0] == pytest.approx(bases[1]) == pytest.approx(bases[2])
    assert [row["action"] for row in results] == ["제한적 분할매수", "제한적 분할매수", "신규 매수 중단·비중 축소 검토"]
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
    built, _bundle_dir, _manifest_sha = _build_bundled(paths, tmp_path / "private_data")
    decision_path = tmp_path / "private_data" / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = tmp_path / "private_data" / "decision" / "decision-model-v2.1-signal-ledger.json"
    write_decision_and_ledger(
        decision=built,
        decision_path=decision_path,
        ledger_path=ledger_path,
    )
    loaded = load_decision(decision_path)
    rebuilt = build_proxy_decision_from_bundle(
        bundle_dir=built["input_bundle"]["path"],
        manifest_sha256=built["input_bundle"]["manifest_sha256"],
        code_commit_sha=TEST_COMMIT_SHA,
    )
    assert loaded == built == rebuilt
    assert loaded["calculation_sha256"] == rebuilt["calculation_sha256"]
    assert loaded["code_commit_sha"] == TEST_COMMIT_SHA
    assert loaded["generator_version"] == "decision-model-v2.1-generator-v4"


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
    result, _bundle_dir, _manifest_sha = _build_bundled(paths, tmp_path / "private_data")
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
    result, bundle_dir, _manifest_sha = _build_bundled(paths, tmp_path / "private_data")
    decision_path = tmp_path / "private_data" / "decision" / "nvda-v2.json"
    ledger_path = tmp_path / "private_data" / "decision" / "ledger-v2.json"
    write_decision_and_ledger(decision=result, decision_path=decision_path, ledger_path=ledger_path)
    loaded = load_decision(decision_path)
    assert loaded["decision_status"] == "proxy_action"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["ledger_version"] == "decision-model-v2.1-signal-ledger-v1"
    assert len(ledger["records"]) == 1
    assert ledger["records"][0]["performance"]["return_6m"] is None
    assert ledger["records"][0]["execution_policy_version"] == "current-price-ladder-confidence-cap-v1"

    tampered = copy.deepcopy(result)
    tampered["calculation"]["target_forward_multiple"] += 1
    with pytest.raises(DecisionModelV2Error, match="calculation SHA"):
        validate_decision(tampered)

    paths["macro"].write_text(json.dumps({"generatedAt": "2026-10-19T22:35:57Z", "score": 0}), encoding="utf-8")
    assert load_decision(decision_path) == loaded
    assert detect_parent_input_updates(bundle_dir)["new_input_available"] is True


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


def test_bundle_consensus_byte_tamper_fails_closed(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    decision, bundle_dir, _manifest_sha = _build_bundled(paths, tmp_path / "private_data")
    decision_path = tmp_path / "private_data" / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = tmp_path / "private_data" / "decision" / "ledger.json"
    write_decision_and_ledger(decision=decision, decision_path=decision_path, ledger_path=ledger_path)
    consensus_path = bundle_dir / "consensus.json"
    consensus_path.write_bytes(consensus_path.read_bytes() + b" ")
    with pytest.raises(DecisionModelV2Error, match="consensus .*hash mismatch"):
        load_decision(decision_path)


def test_bundle_manifest_hash_tamper_fails_closed(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    decision, bundle_dir, _manifest_sha = _build_bundled(paths, tmp_path / "private_data")
    decision_path = tmp_path / "private_data" / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = tmp_path / "private_data" / "decision" / "ledger.json"
    write_decision_and_ledger(decision=decision, decision_path=decision_path, ledger_path=ledger_path)
    manifest_path = bundle_dir / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at"] = "2026-10-20T01:32:33Z"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DecisionModelV2Error, match="manifest SHA-256 mismatch"):
        load_decision(decision_path)


def test_append_only_ledger_preserves_records_and_is_idempotent(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    private_root = tmp_path / "private_data"
    first, _bundle, _manifest_sha = _build_bundled(paths, private_root)
    decision_path = private_root / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = private_root / "decision" / "decision-model-v2.1-signal-ledger.json"
    write_decision_and_ledger(decision=first, decision_path=decision_path, ledger_path=ledger_path)
    original_record = json.loads(ledger_path.read_text(encoding="utf-8"))["records"][0]
    write_decision_and_ledger(decision=first, decision_path=decision_path, ledger_path=ledger_path)
    assert len(json.loads(ledger_path.read_text(encoding="utf-8"))["records"]) == 1

    later_time = "2026-10-20T02:32:32.832654Z"
    bundle_dir, _manifest, manifest_sha = create_input_bundle(
        data_root=paths["price"].parent,
        config_path=paths["config"],
        decision_root=private_root / "decision",
        decision_time=later_time,
        code_commit_sha=TEST_COMMIT_SHA,
    )
    second = build_proxy_decision_from_bundle(
        bundle_dir=bundle_dir,
        manifest_sha256=manifest_sha,
        code_commit_sha=TEST_COMMIT_SHA,
    )
    write_decision_and_ledger(decision=second, decision_path=decision_path, ledger_path=ledger_path)
    records = json.loads(ledger_path.read_text(encoding="utf-8"))["records"]
    assert len(records) == 2
    assert records[0] == original_record
    assert records[1]["decision_time"] == later_time


def test_bundle_creation_blocks_future_source_before_promotion(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    macro = json.loads(paths["macro"].read_text(encoding="utf-8"))
    macro["generatedAt"] = "2026-10-21T00:00:00Z"
    paths["macro"].write_text(json.dumps(macro), encoding="utf-8")
    with pytest.raises(ValueError, match="future data"):
        create_input_bundle(
            data_root=paths["price"].parent,
            config_path=paths["config"],
            decision_root=tmp_path / "private_data" / "decision",
            decision_time=DECISION_TIME,
            code_commit_sha=TEST_COMMIT_SHA,
        )
    bundle_root = tmp_path / "private_data" / "decision" / "input-bundles"
    assert not bundle_root.exists() or not list(bundle_root.iterdir())


def test_new_parent_consensus_is_update_not_integrity_failure(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    decision, bundle_dir, _manifest_sha = _build_bundled(paths, tmp_path / "private_data")
    decision_path = tmp_path / "private_data" / "decision" / "nvda-decision-model-v2.1.json"
    ledger_path = tmp_path / "private_data" / "decision" / "ledger.json"
    write_decision_and_ledger(decision=decision, decision_path=decision_path, ledger_path=ledger_path)
    manifest_path = paths["price"].parent / "consensus-evidence" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    newer = paths["price"].parent / "consensus-evidence" / "2026-10-20" / "new.json"
    newer.parent.mkdir(parents=True, exist_ok=True)
    newer.write_text("{}", encoding="utf-8")
    newer_hash = hashlib.sha256(newer.read_bytes()).hexdigest()
    newer_hashed = newer.with_name(f"{newer_hash}.json")
    newer.rename(newer_hashed)
    manifest["artifacts"][newer_hash] = {
        "evidenceFile": f"consensus-evidence/2026-10-20/{newer_hash}.json",
        "evidenceGeneratedAt": "2026-10-20T02:00:00Z",
        "evidenceCommitSha": None,
        "evidenceArtifactId": f"sha256:{newer_hash}",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_decision(decision_path) == decision
    update = detect_parent_input_updates(bundle_dir)
    assert update["new_input_available"] is True
    assert "consensus" in update["changed_roles"]


def test_bundle_sources_are_internal_and_manifest_hash_is_recorded(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    decision, bundle_dir, manifest_sha = _build_bundled(paths, tmp_path / "private_data")
    assert decision["input_bundle"]["manifest_sha256"] == manifest_sha
    assert file_sha256(bundle_dir / "bundle-manifest.json") == manifest_sha
    for source in decision["sources"].values():
        Path(source["path"]).resolve().relative_to(bundle_dir.resolve())
    assert decision["personal_action_status"] == "active_proxy"
    assert decision["personal_action_tier"] == "limited"
    assert decision["position_budget_percent"] == 50
    assert decision["execution_allocation_weights"] == [17, 16, 17]


@pytest.mark.parametrize(
    ("score", "tier", "budget", "weights", "action"),
    [
        (15, "exploratory", 25, [8, 8, 9], "탐색적 분할매수"),
        (35, "limited", 50, [17, 16, 17], "제한적 분할매수"),
        (50, "moderate", 75, [25, 25, 25], "분할매수"),
        (60, "full", 100, [33, 33, 34], "분할매수"),
    ],
)
def test_confidence_score_caps_position_budget(
    score: int, tier: str, budget: int, weights: list[int], action: str
) -> None:
    policy = _confidence_position_policy(score)
    assert policy == {
        "personal_action_tier": tier,
        "position_budget_percent": budget,
        "execution_allocation_weights": weights,
        "buy_action": action,
    }
    assert sum(policy["execution_allocation_weights"]) == policy["position_budget_percent"]


def test_allocation_sum_must_equal_position_budget(tmp_path: Path) -> None:
    decision = _build(_fixture(tmp_path))
    decision["execution_allocation_weights"] = [8, 8, 8]
    decision["calculation_sha256"] = _decision_calculation_sha256(decision)
    with pytest.raises(DecisionModelV2Error, match="execution allocation"):
        validate_decision(decision)


def test_fundamental_invalidation_blocks_action_and_capital(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    consensus = json.loads(paths["consensus"].read_text(encoding="utf-8"))
    consensus["records"]["NVDA"]["nextConsensusEps"] = 10.0
    paths["consensus"].write_text(json.dumps(consensus), encoding="utf-8")
    decision = _build(paths)
    assert decision["fundamental_invalidation"]["triggered"] is True
    assert decision["decision_status"] == "blocked"
    assert decision["personal_action_status"] == "blocked"
    assert decision["action"] == "신규 진입 중단"
    assert decision["personal_action_tier"] == "blocked"
    assert decision["position_budget_percent"] == 0
    assert decision["execution_buy_prices"] is None
    assert decision["execution_allocation_weights"] is None
    validate_decision(decision)
