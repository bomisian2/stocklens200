from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.nvda_research import (
    ADJUDICATION_VERSION as P51_ADJUDICATION_VERSION,
    READ_MODEL_VERSION as P51_READ_MODEL_VERSION,
    RESEARCH_DECISION_VERSION as P51_DECISION_VERSION,
)
from stocklens.personal.nvda_research_p511 import (
    RESEARCH_DECISION_VERSION,
    NvdaResearchP511Error,
    build_p511_artifacts,
    load_p511_artifacts,
)


DECISION_TIME = "2026-10-18T00:38:27.040534Z"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _fixture(
    tmp_path: Path,
    *,
    price: float = 214.13,
    peer_count: int = 5,
    verified_peers: bool = False,
) -> dict[str, Path]:
    private = tmp_path / "private_data"
    raw = _write(private / "raw" / "alpha.json", {"fixture": "raw"})
    capture = _write(private / "raw" / "alpha.manifest.json", {"fixture": "manifest"})
    terms_pdf = private / "raw" / "terms.pdf"
    terms_pdf.parent.mkdir(parents=True, exist_ok=True)
    terms_pdf.write_bytes(b"synthetic terms fixture")

    p51_adjudication = _write(private / "decision" / "p51-adjudication.json", {
        "adjudication_version": P51_ADJUDICATION_VERSION,
        "row_counts": {"total": 41, "valid": 40, "quarantined": 1, "future_valid": 4, "future_quarantined": 0},
        "operational_eligible": False,
    })
    p51_read_model = _write(private / "normalized" / "p51-read.json", {
        "read_model_version": P51_READ_MODEL_VERSION,
        "adjudication_sha256": _hash(p51_adjudication),
        "ticker": "NVDA",
        "available_at": DECISION_TIME,
        "accounting_basis": "unknown",
        "forward_records": [{
            "period_date": "2027-01-31",
            "horizon": "fiscal year",
            "available_at": DECISION_TIME,
            "eps": {"low": 9.01, "average": 9.3124, "high": 10.1, "analyst_count": 51},
        }],
        "operational_eligible": False,
    })
    p51_decision = _write(private / "decision" / "p51-decision.json", {
        "research_decision_version": P51_DECISION_VERSION,
        "source_hashes": {"consensus_read_model": _hash(p51_read_model)},
        "provisional_research_base_value": 214.08,
        "provisional_research_buy_prices": [203.42, 195.93, 188.43],
        "research_action_text": "withdraw me",
        "operational_eligible": False,
    })
    terms = _write(private / "decision" / "terms-review.json", {
        "review_version": "alpha-vantage-terms-review-p4.3.1-v1",
        "terms_document": {"relative_path": "raw/terms.pdf", "pdf_sha256": _hash(terms_pdf)},
        "scope_assessments": {
            "derived_analysis": {"status": "manual_review_required"},
            "local_display": {"status": "manual_review_required"},
        },
        "public_display_permission": "not_authorized",
        "operational_eligible": False,
    })
    supplemental = _write(private / "decision" / "supplemental.json", {
        "adjudication_version": "alpha-vantage-supplemental-adjudication-p5.0.2-v1",
        "requests": [{
            "request_id": "nvda-daily",
            "ticker": "NVDA",
            "function": "TIME_SERIES_DAILY",
            "source_relative_path": "raw/alpha.json",
            "source_hash": _hash(raw),
            "capture_manifest_relative_path": "raw/alpha.manifest.json",
            "capture_manifest_hash": _hash(capture),
            "allowed_use": "recent_price_flow_and_integrity_research_only",
            "prohibited_uses": ["adjusted_returns", "long_horizon_backtest", "forward_per", "valuation", "trading_decision"],
        }],
        "operational_eligible": False,
    })

    data = tmp_path / "top10"
    price_history = _write(data / "price-history.json", {
        "generatedAt": "2026-10-16T22:36:45.419Z",
        "records": {"NVDA": [{"date": "2026-10-16", "close": price}]},
    })
    records = {
        "NVDA": {
            "ticker": "NVDA", "collectedAt": "2026-10-16T22:38:40.679Z",
            "fiscalEnd": "Jan 2027", "consensusEps": 9.27, "lowEps": 9.01, "highEps": 9.71,
            "analystCount": 16, "revisionsUp4w": 2, "revisionsDown4w": 0,
            "epsBasis": "forward_diluted_adjusted" if verified_peers else "unknown",
            "accountingBasis": "non_gaap_adjusted" if verified_peers else "provider_adjusted_unspecified",
            "providerTimestampVerified": verified_peers,
            "collectionStatus": "success",
        }
    }
    valuations: dict[str, list[dict]] = {}
    industries = {"NVDA": {"sector": "AI 반도체", "etf": "SOXX"}}
    for index in range(peer_count):
        ticker = f"P{index + 1}"
        records[ticker] = {
            "ticker": ticker, "collectedAt": "2026-10-16T22:38:40.679Z",
            "fiscalEnd": "Jan 2027", "consensusEps": 5.0, "lowEps": 4.0, "highEps": 6.0,
            "analystCount": 8, "revisionsUp4w": 0, "revisionsDown4w": 0,
            "epsBasis": "forward_diluted_adjusted" if verified_peers else "unknown",
            "accountingBasis": "non_gaap_adjusted" if verified_peers else "provider_adjusted_unspecified",
            "providerTimestampVerified": verified_peers,
            "collectionStatus": "success",
        }
        valuations[ticker] = [{
            "asOf": "2026-10-16", "expectedPe": 18.0 + index * 2.0,
            "estimateKind": "Nasdaq 컨센서스 EPS (8명)", "estimatePeriod": "Jan 2027",
            "pointInTimeVerified": verified_peers,
        }]
        industries[ticker] = {"sector": "반도체", "etf": "SOXX"}
    consensus = _write(data / "top100-consensus.json", {
        "generatedAt": "2026-10-16T22:38:40.679Z", "records": records,
    })
    valuation = _write(data / "valuation-history.json", {
        "generatedAt": "2026-10-16T23:00:38.659Z", "records": valuations,
    })
    industry = _write(data / "industry-map.json", {
        "generatedAt": "2026-10-16T22:40:59.681Z", "records": industries,
    })
    macro = _write(data / "macro-snapshot.json", {
        "generatedAt": "2026-10-16T22:37:38.345Z",
        "indicators": {"hyg": {"above20": True, "above50": True}},
    })
    return {
        "private": private,
        "p51_adjudication": p51_adjudication,
        "p51_read_model": p51_read_model,
        "p51_decision": p51_decision,
        "terms": terms,
        "supplemental": supplemental,
        "price": price_history,
        "consensus": consensus,
        "valuation": valuation,
        "industry": industry,
        "macro": macro,
        "supersession": private / "decision" / "supersession-p512.json",
        "peer": private / "normalized" / "peer-p512.json",
        "decision": private / "decision" / "decision-p512.json",
    }


def _build(paths: dict[str, Path]):
    return build_p511_artifacts(
        private_root=paths["private"],
        p51_adjudication_path=paths["p51_adjudication"],
        p51_read_model_path=paths["p51_read_model"],
        p51_decision_path=paths["p51_decision"],
        terms_review_path=paths["terms"],
        supplemental_adjudication_path=paths["supplemental"],
        price_history_path=paths["price"],
        consensus_path=paths["consensus"],
        valuation_history_path=paths["valuation"],
        industry_map_path=paths["industry"],
        macro_path=paths["macro"],
        supersession_output_path=paths["supersession"],
        peer_output_path=paths["peer"],
        decision_output_path=paths["decision"],
        generated_at="2026-10-18T02:00:00Z",
    )


def _reseal_decision(path: Path, payload: dict) -> None:
    payload.pop("calculation_sha256", None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload["calculation_sha256"] = hashlib.sha256(encoded).hexdigest().upper()
    _write(path, payload)


def test_price_changes_only_implied_multiple_and_p5_actions_stay_null(tmp_path: Path) -> None:
    results = []
    for value in (100.0, 214.13, 999.0):
        paths = _fixture(tmp_path / str(value), price=value, verified_peers=True)
        _, peer, decision = _build(paths)
        results.append((peer, decision))
    assert [item[0]["post_outlier_median"] for item in results] == [None, None, None]
    assert [item[0]["peer_gate_passed"] for item in results] == [False, False, False]
    assert [item[1]["independent_target_multiple"] for item in results] == [None, None, None]
    assert [item[1]["independent_research_base_value"] for item in results] == [None, None, None]
    assert [item[1]["current_implied_forward_multiple"] for item in results] == pytest.approx(
        [100.0 / 9.27, 214.13 / 9.27, 999.0 / 9.27]
    )
    assert all(item[1]["provisional_research_buy_prices"] is None for item in results)
    assert all(item[1]["research_action"] is None for item in results)
    assert all(item[1]["fair_value"] is None for item in results)
    assert all(item[0]["current_price_used_in_target_multiple"] is False for item in results)
    assert all(item[1]["target_multiple_policy"]["current_price_used_in_target"] is False for item in results)
    assert len({item[1]["target_multiple_policy"]["policy_version"] for item in results}) == 1


def test_insufficient_actual_basis_peers_uses_only_conditional_table(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, peer_count=5)
    supersession, peer, decision = _build(paths)
    assert supersession["status"] == "superseded_circular_price_anchor"
    assert peer["peer_gate_passed"] is False
    assert peer["status"] == "provider_homogeneous_proxy"
    assert peer["post_outlier_peers"] == []
    assert peer["target_basis_explicit"] is False
    assert peer["point_in_time_verified"] is False
    assert decision["independent_target_multiple"] is None
    assert decision["independent_research_base_value"] is None
    assert decision["provisional_research_buy_prices"] is None
    assert decision["buy_ladder_status"] == "not_generated_p5_diagnostic_only"
    assert decision["valuation_signal_status"] == "not_generated"
    assert decision["control_action"] == "no_new_position_signal"
    assert decision["research_action"] is None
    assert decision["research_action_text"] is None
    assert decision["first_review_rule"] is None
    assert decision["fair_value"] is None
    assert decision["buy_range"] is None
    assert decision["sell_range"] is None
    assert [row["price"] for row in decision["conditional_multiple_table"]] == pytest.approx([185.4, 203.94, 222.48])
    assert decision["alpha_vantage_internal_audit"]["valuation_input"] is False
    assert decision["alpha_vantage_internal_audit"]["trading_decision_input"] is False
    assert decision["operational_decision"] is None
    assert decision["operational_eligible"] is False


def test_future_valuation_row_is_excluded_and_cannot_change_target(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, verified_peers=True)
    payload = json.loads(paths["valuation"].read_text(encoding="utf-8"))
    payload["records"]["P1"].append({
        "asOf": "2026-10-17", "expectedPe": 999.0,
        "estimateKind": "Nasdaq 컨센서스 EPS (8명)", "estimatePeriod": "Jan 2027",
    })
    _write(paths["valuation"], payload)
    _, peer, decision = _build(paths)
    assert peer["post_outlier_median"] is None
    assert peer["post_outlier_peers"] == []
    assert peer["excluded_future_valuation_rows"] == [{
        "ticker": "P1", "as_of": "2026-10-17", "reason": "future_valuation_row",
    }]
    assert decision["independent_target_multiple"] is None
    assert decision["valuation_signal_status"] == "not_generated"


def test_alpha_prohibited_use_contract_is_mandatory_and_sources_stay_immutable(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    immutable = {key: paths[key].read_bytes() for key in ("p51_adjudication", "p51_read_model", "p51_decision", "terms", "supplemental")}
    _build(paths)
    assert {key: paths[key].read_bytes() for key in immutable} == immutable

    payload = json.loads(paths["supplemental"].read_text(encoding="utf-8"))
    payload["requests"][0]["prohibited_uses"].remove("valuation")
    _write(paths["supplemental"], payload)
    with pytest.raises(NvdaResearchP511Error, match="prohibited-use contract"):
        _build(paths)


def test_manual_review_required_is_not_an_approval(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _, _, decision = _build(paths)
    policy = decision["alpha_vantage_policy"]
    assert policy["approval_receipt_present"] is False
    assert policy["derived_analysis_authorized"] is False
    assert policy["local_display_authorized"] is False
    assert policy["public_display_permission"] == "not_authorized"


def test_embedded_terms_approval_object_cannot_authorize_use(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    terms = json.loads(paths["terms"].read_text(encoding="utf-8"))
    terms["approval_receipt"] = {
        "status": "approved",
        "approved_scopes": ["derived_analysis", "local_display"],
        "approved_by": "self-asserted",
        "approved_at": "2026-10-17T00:00:00Z",
        "expires_at": "2027-10-17T00:00:00Z",
    }
    _write(paths["terms"], terms)
    _, _, decision = _build(paths)
    policy = decision["alpha_vantage_policy"]
    assert policy["approval_receipt_present"] is False
    assert policy["approval_receipt_validation"] == "external_pinned_file_only"
    assert policy["derived_analysis_authorized"] is False
    assert policy["local_display_authorized"] is False


def test_five_unknown_basis_peers_never_create_target_or_action(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, peer_count=5, verified_peers=False)
    _, peer, decision = _build(paths)
    assert peer["peer_gate_passed"] is False
    assert decision["independent_target_multiple"] is None
    assert decision["first_review_rule"] is None
    assert decision["research_action"] is None


def test_point_in_time_false_blocks_target_even_with_explicit_basis(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, verified_peers=True)
    consensus = json.loads(paths["consensus"].read_text(encoding="utf-8"))
    consensus["records"]["P1"]["providerTimestampVerified"] = False
    _write(paths["consensus"], consensus)
    _, peer, decision = _build(paths)
    assert peer["peer_gate_passed"] is False
    assert decision["fair_value"] is None
    assert decision["buy_range"] is None


def test_self_asserted_pit_flags_do_not_verify_linked_evidence(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, verified_peers=True)
    _, peer, decision = _build(paths)
    assert peer["self_asserted_provider_timestamp_ignored"] is True
    assert peer["target_point_in_time_verified"] is False
    assert peer["target_pit_evidence_reason"] == "linked_pit_evidence_missing"
    assert peer["peer_gate_passed"] is False
    assert decision["research_action"] is None


def test_future_macro_is_excluded_and_cannot_create_action(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    payload = json.loads(paths["macro"].read_text(encoding="utf-8"))
    payload["generatedAt"] = "2026-10-19T00:00:00Z"
    _write(paths["macro"], payload)
    _, _, decision = _build(paths)
    assert decision["regime"]["source_eligible"] is False
    assert "later than decision_time" in decision["regime"]["exclusion_reason"]
    assert decision["regime"]["adjustment"] == 0.0
    assert decision["research_action"] is None
    assert decision["operational_decision"] is None


@pytest.mark.parametrize(
    ("source_key", "field"),
    (("price", "generatedAt"), ("consensus", "generatedAt"), ("valuation", "generatedAt")),
)
def test_future_price_consensus_or_valuation_snapshot_fails_closed(
    tmp_path: Path,
    source_key: str,
    field: str,
) -> None:
    paths = _fixture(tmp_path, verified_peers=True)
    payload = json.loads(paths[source_key].read_text(encoding="utf-8"))
    payload[field] = "2026-10-19T00:00:00Z"
    _write(paths[source_key], payload)
    with pytest.raises(NvdaResearchP511Error, match="later than decision_time"):
        _build(paths)


def test_future_peer_record_is_excluded_and_prevents_action(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, verified_peers=True)
    payload = json.loads(paths["consensus"].read_text(encoding="utf-8"))
    payload["records"]["P1"]["collectedAt"] = "2026-10-19T00:00:00Z"
    _write(paths["consensus"], payload)
    _, peer, decision = _build(paths)
    assert peer["peer_gate_passed"] is False
    assert any(row["ticker"] == "P1" and "later than decision_time" in row["reason"] for row in peer["excluded_peers"])
    assert decision["research_action"] is None
    assert decision["fair_value"] is None


def test_loader_rejects_p51_and_p511_decisions_and_accepts_only_p512(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    loaded = load_p511_artifacts(
        supersession_path=paths["supersession"],
        peer_proxy_path=paths["peer"],
        decision_path=paths["decision"],
    )
    assert loaded[2]["research_decision_version"] == RESEARCH_DECISION_VERSION
    paths["decision"].write_bytes(paths["p51_decision"].read_bytes())
    with pytest.raises(NvdaResearchP511Error, match="P5.1.2 decision version mismatch"):
        load_p511_artifacts(
            supersession_path=paths["supersession"],
            peer_proxy_path=paths["peer"],
            decision_path=paths["decision"],
        )


@pytest.mark.parametrize(
    "mutation",
    (
        {"peer_gate_passed": True, "fair_value": 999.0},
        {"research_action": "분할매수", "buy_range": [200.0, 195.0, 190.0]},
    ),
)
def test_loader_rejects_unsealed_decision_action_tampering(
    tmp_path: Path, mutation: dict,
) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    decision.update(mutation)
    _write(paths["decision"], decision)
    with pytest.raises(NvdaResearchP511Error, match="calculation SHA-256 mismatch"):
        load_p511_artifacts(
            supersession_path=paths["supersession"],
            peer_proxy_path=paths["peer"],
            decision_path=paths["decision"],
        )


def test_loader_rejects_calculation_sha_tampering(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    decision["calculation_sha256"] = "0" * 64
    _write(paths["decision"], decision)
    with pytest.raises(NvdaResearchP511Error, match="calculation SHA-256 mismatch"):
        load_p511_artifacts(
            supersession_path=paths["supersession"],
            peer_proxy_path=paths["peer"],
            decision_path=paths["decision"],
        )


def test_loader_rejects_resealed_peer_gate_mismatch(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    decision["peer_gate_passed"] = True
    _reseal_decision(paths["decision"], decision)
    with pytest.raises(NvdaResearchP511Error, match="decision/peer gate mismatch"):
        load_p511_artifacts(
            supersession_path=paths["supersession"],
            peer_proxy_path=paths["peer"],
            decision_path=paths["decision"],
        )


def test_loader_rejects_resealed_peer_diagnostic_mismatch(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    decision = json.loads(paths["decision"].read_text(encoding="utf-8"))
    decision["peer_diagnostic"]["post_outlier_median"] = 99.0
    _reseal_decision(paths["decision"], decision)
    with pytest.raises(NvdaResearchP511Error, match="decision/peer diagnostic mismatch"):
        load_p511_artifacts(
            supersession_path=paths["supersession"],
            peer_proxy_path=paths["peer"],
            decision_path=paths["decision"],
        )
