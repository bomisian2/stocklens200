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
    validate_decision,
    write_decision_and_ledger,
)


DECISION_TIME = "2026-10-20T01:32:32.832654Z"


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
        industries[ticker] = {"sector": "반도체", "etf": "SOXX"}
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
    assert [row["action"] for row in results] == [
        "분할매수 시작", "분할매수 시작", "신규 매수 중단·비중 축소 검토",
    ]
    assert all(row["calculation"]["current_price_used_in_target_multiple"] is False for row in results)
    assert all(row["verified_action"] is None and row["operational_eligible"] is False for row in results)


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


def test_adjustment_caps_ladder_and_action_branches(tmp_path: Path) -> None:
    result = _build(_fixture(tmp_path))
    calc = result["calculation"]
    assert abs(calc["growth_adjustment"]) <= 0.15
    assert abs(calc["revision_adjustment"]) <= 0.05
    assert abs(calc["combined_growth_revision_adjustment"]) <= 0.20
    assert abs(calc["regime"]["adjustment"]) <= 0.10
    base = result["scenario_values"]["base"]
    assert result["buy_prices"] == pytest.approx([base * 0.97, base * 0.93, base * 0.89])
    assert result["reduction_review_price"] == pytest.approx(base * 1.15)


def test_artifact_and_source_hashes_fail_closed_and_ledger_is_versioned(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = _build(paths)
    decision_path = tmp_path / "private_data" / "decision" / "nvda-v2.json"
    ledger_path = tmp_path / "private_data" / "decision" / "ledger-v2.json"
    write_decision_and_ledger(decision=result, decision_path=decision_path, ledger_path=ledger_path)
    loaded = load_decision(decision_path)
    assert loaded["decision_status"] == "proxy_action"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["ledger_version"] == "decision-model-v2-signal-ledger-v1"
    assert len(ledger["records"]) == 1
    assert ledger["records"][0]["performance"]["return_6m"] is None

    tampered = copy.deepcopy(result)
    tampered["calculation"]["target_forward_multiple"] += 1
    with pytest.raises(DecisionModelV2Error, match="calculation SHA"):
        validate_decision(tampered)

    paths["macro"].write_text(json.dumps({"generatedAt": "2026-10-19T22:35:57Z", "score": 0}), encoding="utf-8")
    with pytest.raises(DecisionModelV2Error, match="source hash"):
        load_decision(decision_path)


def test_tukey_outlier_is_removed_and_proxy_is_never_verified(tmp_path: Path) -> None:
    result = _build(_fixture(tmp_path))
    assert result["peer_gate"]["pre_outlier_count"] == 8
    assert result["peer_gate"]["post_outlier_count"] == 7
    assert any(row["reason"] == "tukey_1_5_iqr_outlier" for row in result["peer_gate"]["excluded_peers"])
    assert result["proxy_classification"] == "provider_homogeneous_proxy"
    assert result["peer_gate"]["point_in_time_verified"] is False
    assert result["verified_action"] is None
