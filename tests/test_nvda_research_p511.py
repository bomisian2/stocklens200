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


def _fixture(tmp_path: Path, *, price: float = 214.13, peer_count: int = 5) -> dict[str, Path]:
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
            "epsBasis": "unknown", "accountingBasis": "provider_adjusted_unspecified", "collectionStatus": "success",
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
            "epsBasis": "unknown", "accountingBasis": "provider_adjusted_unspecified", "collectionStatus": "success",
        }
        valuations[ticker] = [{
            "asOf": "2026-10-16", "expectedPe": 18.0 + index * 2.0,
            "estimateKind": "Nasdaq 컨센서스 EPS (8명)", "estimatePeriod": "Jan 2027",
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
        "supersession": private / "decision" / "supersession-p511.json",
        "peer": private / "normalized" / "peer-p511.json",
        "decision": private / "decision" / "decision-p511.json",
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


def test_price_changes_only_implied_multiple_not_independent_target(tmp_path: Path) -> None:
    results = []
    for value in (100.0, 214.13, 999.0):
        paths = _fixture(tmp_path / str(value), price=value)
        _, peer, decision = _build(paths)
        results.append((peer, decision))
    assert [item[0]["post_outlier_median"] for item in results] == [22.0, 22.0, 22.0]
    assert [item[1]["independent_target_multiple"] for item in results] == [22.0, 22.0, 22.0]
    assert [item[1]["independent_research_base_value"] for item in results] == pytest.approx([203.94] * 3)
    assert [item[1]["current_implied_forward_multiple"] for item in results] == pytest.approx(
        [100.0 / 9.27, 214.13 / 9.27, 999.0 / 9.27]
    )
    assert all(item[1]["provisional_research_buy_prices"] == [197.82, 189.66, 181.51] for item in results)


def test_insufficient_actual_basis_peers_uses_only_conditional_table(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, peer_count=1)
    supersession, peer, decision = _build(paths)
    assert supersession["status"] == "superseded_circular_price_anchor"
    assert peer["peer_gate_passed"] is False
    assert [row["ticker"] for row in peer["post_outlier_peers"]] == ["P1"]
    assert decision["independent_target_multiple"] is None
    assert decision["independent_research_base_value"] is None
    assert decision["provisional_research_buy_prices"] is None
    assert decision["buy_ladder_status"] == "not_created_peer_gate_failed"
    assert decision["research_action"] == "대기"
    assert [row["price"] for row in decision["conditional_multiple_table"]] == pytest.approx([185.4, 203.94, 222.48])
    assert decision["first_review_rule"]["price_at_or_below"] == pytest.approx(203.94)
    assert decision["alpha_vantage_cross_validation"]["valuation_input"] is False
    assert decision["alpha_vantage_cross_validation"]["trading_decision_input"] is False
    assert decision["operational_decision"] is None
    assert decision["operational_eligible"] is False


def test_future_valuation_row_is_excluded_and_cannot_change_target(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    payload = json.loads(paths["valuation"].read_text(encoding="utf-8"))
    payload["records"]["P1"].append({
        "asOf": "2026-10-17", "expectedPe": 999.0,
        "estimateKind": "Nasdaq 컨센서스 EPS (8명)", "estimatePeriod": "Jan 2027",
    })
    _write(paths["valuation"], payload)
    _, peer, decision = _build(paths)
    assert peer["post_outlier_median"] == pytest.approx(22.0)
    assert {row["expected_pe"] for row in peer["post_outlier_peers"]} == {18.0, 20.0, 22.0, 24.0, 26.0}
    assert peer["excluded_future_valuation_rows"] == [{
        "ticker": "P1", "as_of": "2026-10-17", "reason": "future_valuation_row",
    }]
    assert decision["independent_target_multiple"] == pytest.approx(22.0)


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


def test_future_source_timestamp_fails_closed(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    payload = json.loads(paths["macro"].read_text(encoding="utf-8"))
    payload["generatedAt"] = "2026-10-19T00:00:00Z"
    _write(paths["macro"], payload)
    with pytest.raises(NvdaResearchP511Error, match="later than decision_time"):
        _build(paths)


def test_loader_rejects_legacy_p51_decision_and_accepts_only_p511(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    loaded = load_p511_artifacts(
        supersession_path=paths["supersession"],
        peer_proxy_path=paths["peer"],
        decision_path=paths["decision"],
    )
    assert loaded[2]["research_decision_version"] == RESEARCH_DECISION_VERSION
    paths["decision"].write_bytes(paths["p51_decision"].read_bytes())
    with pytest.raises(NvdaResearchP511Error, match="P5.1.1 decision version mismatch"):
        load_p511_artifacts(
            supersession_path=paths["supersession"],
            peer_proxy_path=paths["peer"],
            decision_path=paths["decision"],
        )
