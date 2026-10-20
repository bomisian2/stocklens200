from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from stocklens.personal import decision_model
from stocklens.personal.decision_model_v2 import build_proxy_decision, write_decision_and_ledger
from stocklens.personal.decision_evidence import (
    AUDIT_VERSION,
    REQUIRED_REQUIREMENT_IDS,
    REQUIRED_SOURCE_ROLES,
)
from stocklens.personal.v23_adapter import EXPECTED_HISTORICAL_ROWS
from stocklens.personal.same_date_builder import build_same_date_snapshot
from stocklens.personal.nvda_research_p511 import (
    PEER_PROXY_VERSION as NVDA_P511_PEER_PROXY_VERSION,
    RESEARCH_DECISION_VERSION as NVDA_P511_DECISION_VERSION,
    SUPERSESSION_VERSION as NVDA_P511_SUPERSESSION_VERSION,
    TARGET_MULTIPLE_POLICY_VERSION as NVDA_P511_POLICY_VERSION,
)


APPTEST_TIMEOUT_SECONDS = 30
pytestmark = pytest.mark.streamlit


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_ready_audit(
    *,
    audit_path: Path,
    legacy_path: Path,
    decision_path: Path,
    v23_path: Path,
    price_history_path: Path,
) -> str:
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    source_root = decision_path.parent
    manifest = decision["source_evidence"]
    config_path = Path(__file__).parents[1] / "config" / "decision-model-v1.json"
    source_paths = {
        "legacy_snapshot": legacy_path,
        "decision_snapshot": decision_path,
        "decision_ledger": source_root / manifest["decision_ledger"]["path"],
        "consensus": source_root / manifest["consensus"]["path"],
        "regime": source_root / manifest["regime"]["path"],
        "price_history": price_history_path,
        "v23_ledger": v23_path,
        "decision_config": config_path,
    }
    assert set(source_paths) == set(REQUIRED_SOURCE_ROLES)
    report = {
        "audit_version": AUDIT_VERSION,
        "evidence_classification": "synthetic_fixture",
        "decision_time": decision["decision_time"],
        "target_tickers": ["AVGO", "NVDA"],
        "legacy_snapshot_hash": _sha256(legacy_path),
        "decision_snapshot_hash": _sha256(decision_path),
        "status": "ready",
        "decision_creation_allowed": True,
        "decision_created": False,
        "block_stage": None,
        "operational_eligible": False,
        "requirements": [
            {"id": requirement_id, "passed": True, "status": "passed", "reason": "fixture"}
            for requirement_id in REQUIRED_REQUIREMENT_IDS
        ],
        "evidence": [
            {
                "role": role,
                "path": str(path.resolve()),
                "source_hash": _sha256(path),
            }
            for role, path in source_paths.items()
        ],
    }
    audit_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return _sha256(audit_path)


def _top10_consensus_fixture() -> dict:
    shared = {
        "collectedAt": "2026-10-14T12:52:02.418Z",
        "providerId": "nasdaq-analyst-earnings-forecast",
        "collectionStatus": "success",
        "currency": "USD",
        "epsBasis": "unknown",
        "accountingBasis": "provider_adjusted_unspecified",
        "revisionsUp4w": 1,
        "revisionsDown4w": 0,
        "revisionState": "상향 우세",
    }
    return {
        "generatedAt": "2026-10-14T12:52:02.418Z",
        "source": "Nasdaq Analyst Research earnings forecast API",
        "methodology": "fixture annual aggregate",
        "records": {
            "AVGO": {
                **shared, "ticker": "AVGO", "fiscalEnd": "Oct 2026",
                "consensusEps": 10.37, "lowEps": 10.06, "highEps": 12.38,
                "analystCount": 13, "nextFiscalEnd": "Oct 2027", "nextConsensusEps": 17.45,
                "sourceUrl": "https://api.nasdaq.com/api/analyst/AVGO/earnings-forecast",
            },
            "NVDA": {
                **shared, "ticker": "NVDA", "fiscalEnd": "Jan 2027",
                "consensusEps": 9.25, "lowEps": 9.01, "highEps": 9.71,
                "analystCount": 17, "nextFiscalEnd": "Jan 2028", "nextConsensusEps": 15.53,
                "sourceUrl": "https://api.nasdaq.com/api/analyst/NVDA/earnings-forecast",
            },
            **{
                f"P{index}": {
                    **shared, "ticker": f"P{index}", "fiscalEnd": "Dec 2026",
                    "consensusEps": 5.0, "lowEps": 4.5, "highEps": 5.5,
                    "analystCount": 10, "nextFiscalEnd": "Dec 2027",
                    "nextConsensusEps": 6.0 + index * 0.1,
                    "sourceUrl": f"https://api.nasdaq.com/api/analyst/P{index}/earnings-forecast",
                }
                for index in range(1, 7)
            },
        },
    }


def _top10_macro_fixture() -> dict:
    values = {
        "tenYear": (5.22, -0.05, 0.22, 5.17, 4.91, True, True, "%"),
        "oil": (94.7, 5.88, -10.52, 93.47, 89.67, True, True, "USD"),
        "vix": (15.27, 1.73, -11.22, 15.46, 15.45, False, False, "pt"),
        "qqq": (743.57, -2.12, 5.54, 741.34, 726.55, True, True, "USD"),
        "rsp": (213.3, 0.46, -0.31, 211.37, 216.18, True, False, "USD"),
        "hyg": (77.09, -0.23, -1.65, 77.65, 78.7, False, False, "USD"),
    }
    return {
        "generatedAt": "2026-10-13T22:35:27.276Z", "state": "중립", "score": 3,
        "interpretation": "공식 국면을 대체하지 않음", "methodology": "fixture proxy",
        "indicators": {
            key: {
                "label": key, "asOf": "2026-10-13", "value": row[0],
                "change5": row[1], "change20": row[2], "ma20": row[3], "ma50": row[4],
                "above20": row[5], "above50": row[6], "unit": row[7],
                "source": "Yahoo Finance 확정 일봉",
            }
            for key, row in values.items()
        },
    }


def test_personal_app_renders_financials_without_market_signal(tmp_path, monkeypatch) -> None:
    financial_dir = tmp_path / "normalized" / "financials"
    financial_dir.mkdir(parents=True)
    financial = {
        "company": "Fixture Corp",
        "ticker": "FIX",
        "status": "verified",
        "collected_at_utc": "2026-10-13T00:00:00+00:00",
        "periods": [
            {
                "label": "FY2025",
                "period_end": "2025-12-31",
                "accounting_basis": "fixture_only",
                "metrics": {"revenue": 100.0, "diluted_eps": 1.0},
            }
        ],
        "balance_sheet": {"as_of": "2025-12-31", "cash_and_equivalents": 10.0},
        "guidance": {"status": "unavailable"},
        "documents": [
            {
                "key": "fixture",
                "published_at": "2026-01-01T00:00:00+00:00",
                "period_end": "2025-12-31",
                "distribution": "test fixture",
                "sha256": "0" * 64,
                "url": "https://example.invalid/fixture",
            }
        ],
    }
    (financial_dir / "FIX.json").write_text(json.dumps(financial), encoding="utf-8")
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "financial_tickers_verified": 1,
                "financial_periods_verified": 1,
                "market": {"status": "blocked"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))
    app_path = Path(__file__).parents[1] / "personal_app.py"

    app = AppTest.from_file(str(app_path)).run(timeout=APPTEST_TIMEOUT_SECONDS)

    assert not app.exception
    rendered = " ".join(item.value for item in [*app.title, *app.caption, *app.warning, *app.error])
    assert "개인 연구" in rendered
    assert "운영 판단: hold" in rendered
    assert "공식 목표가격" not in rendered
    assert any("개인용 시장 데이터 현황" in item.value for item in app.subheader)


def test_personal_app_integrates_amat_without_promoting_research_values(tmp_path, monkeypatch) -> None:
    financial_dir = tmp_path / "normalized" / "financials"
    market_dir = tmp_path / "market"
    financial_dir.mkdir(parents=True)
    market_dir.mkdir(parents=True)
    financial = {
        "company": "Applied Materials, Inc.", "ticker": "AMAT", "status": "verified",
        "collected_at_utc": "2026-10-13T00:00:00+00:00",
        "periods": [
            {"kind": "fiscal_year", "label": "FY2025", "period_end": "2025-10-26", "accounting_basis": "reported_gaap", "metrics": {"revenue": 100.0, "operating_income": 25.0, "net_income": 20.0, "diluted_eps": 8.0, "diluted_shares": 808.0, "cfo": 30.0, "capex": 10.0, "fcf": 20.0}},
            {"kind": "quarter", "label": "Q3 FY2026", "period_end": "2026-07-26", "accounting_basis": "reported_gaap", "metrics": {"revenue": 30.0, "operating_income": 9.0, "net_income": 7.0, "diluted_eps": 3.0, "diluted_shares": 800.0, "cfo": 9.0, "capex": 3.0, "fcf": 6.0}},
            {"kind": "ttm", "label": "TTM through Q3 FY2026", "period_end": "2026-07-26", "accounting_basis": "derived_from_reported_gaap", "metrics": {"revenue": 120.0, "operating_income": 30.0, "net_income": 24.0, "diluted_eps": None, "diluted_shares": None, "cfo": 36.0, "capex": 12.0, "fcf": 24.0}},
        ],
        "balance_sheet": {"cash_and_equivalents": 10.0, "short_term_investments": 2.0, "long_term_investments": 3.0, "short_term_debt": 1.0, "long_term_debt": 4.0},
        "guidance": {"period": "Q4 FY2026", "revenue_midpoint": 40.0, "revenue_plus_minus": 2.0, "non_gaap_eps_midpoint": 4.0, "non_gaap_eps_plus_minus": 0.2},
        "documents": [{"key": "fixture", "published_at": "2026-08-13T20:01:00+00:00", "period_end": "2026-07-26", "distribution": "fixture", "sha256": "0" * 64, "url": "https://example.invalid/fixture"}],
    }
    (financial_dir / "AMAT.json").write_text(json.dumps(financial), encoding="utf-8")
    avgo_financial = json.loads(json.dumps(financial))
    avgo_financial["company"] = "Broadcom Inc."
    avgo_financial["ticker"] = "AVGO"
    avgo_financial["guidance"] = {
        "period": "Q4 FY2026",
        "revenue_approx": 40.0,
        "non_gaap_operating_margin_percent": 66.0,
    }
    (financial_dir / "AVGO.json").write_text(json.dumps(avgo_financial), encoding="utf-8")
    nvda_financial = json.loads(json.dumps(financial))
    nvda_financial["company"] = "NVIDIA Corporation"
    nvda_financial["ticker"] = "NVDA"
    nvda_financial["guidance"] = {"status": "unavailable"}
    (financial_dir / "NVDA.json").write_text(json.dumps(nvda_financial), encoding="utf-8")
    dates = pd.bdate_range("2026-05-20", periods=100).strftime("%Y-%m-%d")
    prices = pd.Series([400 + index for index in range(100)], dtype=float)
    frame = pd.DataFrame({"date": dates, "open": prices - 1, "high": prices + 2, "low": prices - 2, "close": prices, "volume": 1_000_000})
    frame.to_csv(market_dir / "AMAT.csv", index=False)
    frame.assign(close=prices * 0.8).to_csv(market_dir / "AVGO.csv", index=False)
    frame.assign(close=prices * 1.1).to_csv(market_dir / "QQQ.csv", index=False)
    (market_dir / "AMAT.manifest.json").write_text(json.dumps({
        "freshness_status": "verified", "market_date": dates[-1],
        "collected_at_utc": "2026-10-13T00:00:00+00:00", "provider": "fixture",
        "price_policy": "raw_as_traded_unadjusted", "classification": "test_fixture",
    }), encoding="utf-8")
    (market_dir / "AVGO.manifest.json").write_text(json.dumps({
        "freshness_status": "verified", "market_date": dates[-1],
        "collected_at_utc": "2026-10-13T00:00:00+00:00", "provider": "fixture",
        "price_policy": "raw_as_traded_unadjusted", "classification": "test_fixture",
    }), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps({
        "financial_tickers_available": 1, "financial_periods_available": 3,
        "market": {"status": "partial", "results": [
            {"ticker": "AMAT", "status": "verified", "as_of": dates[-1], "rows": 100},
            {"ticker": "AVGO", "status": "verified", "as_of": dates[-1], "rows": 100},
            {"ticker": "QQQ", "status": "verified", "as_of": dates[-1], "rows": 100},
        ]},
    }), encoding="utf-8")
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))
    v23_fixture_path = Path(__file__).parent / "fixtures" / "v23_public_ledger_minimal.json"
    v23_payload = json.loads(v23_fixture_path.read_text(encoding="utf-8"))
    for v23_ticker, count in EXPECTED_HISTORICAL_ROWS.items():
        seed = v23_payload["tickers"][v23_ticker]["historicalPer"][0]
        v23_payload["tickers"][v23_ticker]["historicalPer"] = [dict(seed) for _ in range(count)]
    v23_path = tmp_path / "v23-public-ledger.json"
    v23_bytes = json.dumps(v23_payload, sort_keys=True).encode("utf-8")
    v23_path.write_bytes(v23_bytes)
    monkeypatch.setenv("STOCKLENS_V23_LEDGER_PATH", str(v23_path))
    monkeypatch.setenv("STOCKLENS_V23_LEDGER_SHA256", hashlib.sha256(v23_bytes).hexdigest())
    top10_dir = tmp_path / "top10"
    top10_dir.mkdir()
    consensus_bytes = json.dumps(_top10_consensus_fixture(), sort_keys=True).encode("utf-8")
    consensus_path = top10_dir / "top100-consensus.json"
    consensus_path.write_bytes(consensus_bytes)
    monkeypatch.setenv("STOCKLENS_TOP10_CONSENSUS_PATH", str(consensus_path))
    monkeypatch.setenv(
        "STOCKLENS_TOP10_CONSENSUS_SHA256",
        hashlib.sha256(consensus_bytes).hexdigest(),
    )
    macro_bytes = json.dumps(_top10_macro_fixture(), sort_keys=True).encode("utf-8")
    macro_path = top10_dir / "macro-snapshot.json"
    macro_path.write_bytes(macro_bytes)
    monkeypatch.setenv("STOCKLENS_TOP10_MACRO_PATH", str(macro_path))
    monkeypatch.setenv("STOCKLENS_TOP10_MACRO_SHA256", hashlib.sha256(macro_bytes).hexdigest())
    decision_dir = tmp_path / "decision"
    decision_dir.mkdir()
    source_dir = Path(__file__).parent / "fixtures" / "same_date_sources"
    decision_path = build_same_date_snapshot(
        decision_ledger_path=source_dir / "decision-ledger.json",
        consensus_path=source_dir / "consensus.json",
        regime_path=source_dir / "regime.json",
        output_dir=decision_dir,
        decision_time="2026-10-14T12:52:02.418Z",
    )
    decision_bytes = decision_path.read_bytes()
    legacy_path = decision_dir / "same-date-inputs-legacy-v1.json"
    legacy_payload = json.loads(decision_path.read_text(encoding="utf-8"))
    legacy_payload["calculation_version"] = "same-date-investment-decision-v1"
    legacy_path.write_text(json.dumps(legacy_payload, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv("STOCKLENS_SAME_DATE_INPUT_PATH", str(decision_path))
    monkeypatch.setenv("STOCKLENS_SAME_DATE_SOURCE_ROOT", str(decision_dir))
    monkeypatch.setenv(
        "STOCKLENS_SAME_DATE_INPUT_SHA256",
        hashlib.sha256(decision_bytes).hexdigest(),
    )
    price_history_path = top10_dir / "price-history.json"
    price_history_path.write_text(
        json.dumps(
            {
                "generatedAt": "2026-10-13T22:35:27.276Z",
                "records": {
                    "AVGO": [{"date": "2026-10-13", "close": 357.31}],
                    "NVDA": [{"date": "2026-10-13", "close": 228.52}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCKLENS_TOP10_PRICE_HISTORY_PATH", str(price_history_path))
    audit_path = decision_dir / "evidence-audit-v1.json"
    audit_sha256 = _write_ready_audit(
        audit_path=audit_path,
        legacy_path=legacy_path,
        decision_path=decision_path,
        v23_path=v23_path,
        price_history_path=price_history_path,
    )
    monkeypatch.setenv("STOCKLENS_DECISION_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("STOCKLENS_DECISION_AUDIT_SHA256", audit_sha256)
    monkeypatch.setenv("STOCKLENS_SAME_DATE_LEGACY_INPUT_PATH", str(legacy_path))

    valuation_path = top10_dir / "valuation-history.json"
    valuation_path.write_text(json.dumps({
        "generatedAt": "2026-10-14T12:52:02.418Z",
        "records": {
            f"P{index}": [{
                "asOf": "2026-10-13", "expectedPe": 20.0 + index * 3.0,
                "estimatePeriod": "Dec 2026", "pointInTimeVerified": False,
            }]
            for index in range(1, 7)
        },
    }), encoding="utf-8")
    industry_path = top10_dir / "industry-map.json"
    industry_path.write_text(json.dumps({
        "generatedAt": "2026-10-14T12:52:02.418Z",
        "records": {
            "NVDA": {"sector": "AI 반도체", "etf": "SOXX"},
            **{f"P{index}": {"sector": "반도체", "etf": "SOXX"} for index in range(1, 7)},
        },
    }), encoding="utf-8")
    v2_decision = build_proxy_decision(
        price_history_path=price_history_path,
        consensus_path=consensus_path,
        valuation_history_path=valuation_path,
        industry_map_path=industry_path,
        macro_path=macro_path,
        config_path=Path(__file__).parents[1] / "config" / "decision-model-v2.json",
        decision_time="2026-10-14T12:52:02.418Z",
    )
    v2_path = decision_dir / "nvda-decision-model-v2.json"
    write_decision_and_ledger(
        decision=v2_decision,
        decision_path=v2_path,
        ledger_path=decision_dir / "decision-model-v2-ledger.json",
    )
    monkeypatch.setenv("STOCKLENS_DECISION_V2_PATH", str(v2_path))

    # P5.1.2 artifacts are synthetic UI fixtures. The loader still re-hashes every
    # declared source and rejects superseded P5.1/P5.1.1 versions.
    p512_source_dir = tmp_path / "p512-sources"
    p512_source_dir.mkdir()
    p512_sources = {}
    for name in (
        "p51_adjudication", "p51_read_model", "p51_decision", "price_history",
        "consensus", "valuation_history", "industry_map", "macro", "terms_review",
        "supplemental_adjudication",
    ):
        path = p512_source_dir / f"{name}.json"
        path.write_text(json.dumps({"fixture": name}), encoding="utf-8")
        p512_sources[name] = path
    supersession_path = decision_dir / "nvda-p512-supersession.json"
    supersession_path.write_text(json.dumps({
        "supersession_version": NVDA_P511_SUPERSESSION_VERSION,
        "status": "superseded_circular_price_anchor",
        "operational_eligible": False,
    }), encoding="utf-8")
    peer_path = decision_dir / "nvda-p512-peer.json"
    peer_path.write_text(json.dumps({
        "peer_proxy_version": NVDA_P511_PEER_PROXY_VERSION,
        "status": "provider_homogeneous_proxy",
        "minimum_peer_count": 5,
        "pre_outlier_peers": [{"ticker": "MRVL", "expected_pe": 22.0, "as_of": "2026-10-16"}],
        "post_outlier_peers": [{"ticker": "MRVL", "expected_pe": 22.0, "as_of": "2026-10-16"}],
        "excluded_peers": [{"ticker": "AMD", "reason": "fiscal_period_mismatch"}],
        "peer_gate_passed": False,
        "block_reason": "explicit-basis, point-in-time, same-session peers 0/5",
        "current_price_used_in_target_multiple": False,
        "operational_eligible": False,
    }), encoding="utf-8")
    p512_decision_path = decision_dir / "nvda-p512-decision.json"
    source_hashes = {name: _sha256(path) for name, path in p512_sources.items()}
    source_hashes.update({
        "p512_supersession": _sha256(supersession_path),
        "p512_peer_proxy": _sha256(peer_path),
    })
    p512_source_paths = {name: str(path) for name, path in p512_sources.items()}
    p512_source_paths.update({
        "p512_supersession": str(supersession_path),
        "p512_peer_proxy": str(peer_path),
    })
    p512_decision = {
        "research_decision_version": NVDA_P511_DECISION_VERSION,
        "artifact_versions": {
            "supersession": NVDA_P511_SUPERSESSION_VERSION,
            "peer_proxy": NVDA_P511_PEER_PROXY_VERSION,
            "decision": NVDA_P511_DECISION_VERSION,
            "target_multiple_policy": NVDA_P511_POLICY_VERSION,
        },
        "superseded_p51_status": "superseded_circular_price_anchor",
        "current_price": {"value": 214.13, "as_of": "2026-10-16", "available_at": "2026-10-16T22:36:45.419Z"},
        "consensus_input": {
            "period": "Jan 2027",
            "low": 9.01,
            "average": 9.27,
            "high": 9.71,
            "available_at": "2026-10-16T22:38:40.679Z",
            "eps_basis": "unknown",
        },
        "current_implied_forward_multiple": 214.13 / 9.27,
        "alpha_vantage_internal_audit": {
            "available_at": "2026-10-18T00:38:27.040534Z", "data_quality_status": "partial_valid",
            "accounting_basis": "unknown", "raw_unchanged": True,
            "valuation_input": False, "trading_decision_input": False,
        },
        "alpha_vantage_policy": {
            "approval_receipt_present": False, "derived_analysis_authorized": False,
            "local_display_authorized": False, "public_display_permission": "not_authorized",
        },
        "peer_gate_passed": False,
        "peer_proxy_status": "provider_homogeneous_proxy",
        "peer_diagnostic": {
            "status": "provider_homogeneous_proxy",
            "gate_passed": False,
            "pre_outlier_tickers": ["MRVL"],
            "post_outlier_tickers": ["MRVL"],
            "pre_outlier_median": None,
            "post_outlier_median": None,
        },
        "target_multiple_policy": {
            "policy_version": NVDA_P511_POLICY_VERSION,
            "peer_tickers": ["MRVL"],
        },
        "valuation_signal_status": "not_generated",
        "control_action": "no_new_position_signal",
        "pre_regime_target_multiple": None,
        "independent_target_multiple": None,
        "independent_research_base_value": None,
        "regime": {"adjustment": -0.03, "applied_to_target": False, "trace": ["HYG 약세 프록시: -3%"]},
        "conditional_multiple_table": [{"multiple": value, "price": 9.27 * value} for value in (20.0, 22.0, 24.0)],
        "first_review_rule": None,
        "research_action": None,
        "research_action_text": None,
        "provisional_research_buy_prices": None,
        "fair_value": None,
        "buy_range": None,
        "sell_range": None,
        "eps_sensitivity": None,
        "buy_ladder_status": "not_generated_p5_diagnostic_only",
        "accounting_basis": "unknown",
        "pit_limit": "cross-project peer data are not independently point-in-time verified",
        "source_hashes": source_hashes,
        "source_paths": p512_source_paths,
        "operational_decision": None,
        "operational_eligible": False,
    }
    encoded = json.dumps(
        p512_decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    p512_decision["calculation_sha256"] = hashlib.sha256(encoded).hexdigest().upper()
    p512_decision_path.write_text(json.dumps(p512_decision), encoding="utf-8")
    monkeypatch.setenv("STOCKLENS_NVDA_P512_SUPERSESSION_PATH", str(supersession_path))
    monkeypatch.setenv("STOCKLENS_NVDA_P512_PEER_PROXY_PATH", str(peer_path))
    monkeypatch.setenv("STOCKLENS_NVDA_P512_DECISION_PATH", str(p512_decision_path))

    app = AppTest.from_file(str(Path(__file__).parents[1] / "personal_app.py")).run(
        timeout=APPTEST_TIMEOUT_SECONDS
    )

    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["검증 EOD 종가"] == "$499.00"
    assert metrics["대용 TTM FCF yield"] == "0.0%"
    assert metrics["발행 보통주"] == "793.597M"
    assert metrics["시점 불일치 주식가치 참고"] != "미확보"
    assert metrics["EMA20"] != "미확보"
    rendered_text = " ".join(item.value for item in [*app.subheader, *app.markdown, *app.caption])
    assert "AMAT·AVGO 실제 투자 분석 비교" in rendered_text
    assert "실적은 개선되고 있는가?" in rendered_text
    assert "현재 가격에는 높은 성장이 요구되는가?" in rendered_text
    assert "대용 P/FCF" in metrics
    assert "저성장·고할인 스트레스 조건" in metrics
    assert "중간 스트레스 격자(공식 기본값 아님)" in metrics
    assert "고성장·저할인 스트레스 조건" in metrics
    assert "정상 관심 범위" in metrics
    assert metrics["정상 관심 범위"] == "미산출"
    assert "현재 시장 요구 성장" in metrics
    assert "품질 근거 상태" in metrics
    assert metrics["운영 투자판단"] == "비활성"
    assert "검증된 위기 스트레스 범위" in metrics
    expander_states = {item.label: item.proto.expanded for item in app.expander}
    assert expander_states["품질 프리미엄 증거 · 점수로 합산하지 않음"] is True
    assert expander_states["평가 축 산출 자격과 결측 정책"] is False
    assert expander_states["평가 입력과 기준일 상세"] is False
    assert expander_states["2차 상세 · DCF 스트레스 참고 범위"] is False
    warnings = " ".join(item.value for item in app.warning)
    errors = " ".join(item.value for item in app.error)
    assert "연구 참고 전용" in warnings
    assert "확정 시가총액" in warnings
    assert "운영 판단: hold" in errors
    assert "정식 적정가" in errors
    assert "목표가격·매수구간" in warnings
    assert "정확성 부족을 더 낮은 가치로 바꾸지 않으며" in warnings

    app.selectbox[0].set_value("AVGO").run(timeout=APPTEST_TIMEOUT_SECONDS)
    assert not app.exception
    avgo_metrics = {item.label: item.value for item in app.metric}
    assert avgo_metrics["발행 보통주"] == "4,773.630M"
    assert avgo_metrics["시점 불일치 주식가치 참고"] != "미확보"
    avgo_rendered = " ".join(item.value for item in [*app.caption, *app.warning, *app.error])
    assert "합성 fixture" in avgo_rendered
    assert "실제 원본 통과 또는 운영 승격이 아닙니다" in avgo_rendered
    assert "0001730168-26-000080" in avgo_rendered
    assert "정식 적정가" in avgo_rendered
    assert "operational_eligible=false" in " ".join(item.value for item in app.caption)
    assert any("v23 reconstructed PIT 비교" in item.value for item in app.subheader)
    avgo_success = " ".join(item.value for item in app.success)
    assert "동일 가격일 2026-09-11 교차검증" in avgo_success
    assert "EPS·PER 정의는 합치거나 덮어쓰지 않았습니다" in avgo_success
    assert avgo_metrics["TTM 희석 EPS"] == "$7.84"
    assert avgo_metrics["TTM PER"] == "46.17배"
    assert avgo_metrics["정상 관심 후보 · 연구 전용"] != "미산출"
    assert avgo_metrics["자기 역사 정상 관심 후보"] != "미산출"
    assert avgo_metrics["국면 조정 후보 · 연구 전용"] == avgo_metrics["자기 역사 정상 관심 후보"]
    assert avgo_metrics["경기"] == "중립·혼재"
    assert avgo_metrics["물가"] == "중립·혼재"
    assert avgo_metrics["리스크"] == "중립"
    assert avgo_metrics["현재 v23 TTM PER"] == "46.17배"
    assert avgo_metrics["미래 EPS 저/기준/고"] == "$10.06 / $10.37 / $12.38"
    assert avgo_metrics["현재가 기준 미래 PER"] != "미확보"
    assert avgo_metrics["미래 EPS×TTM PER 산술 외곽"] != "미확보"
    assert avgo_metrics["Top10 프록시 상태"] == "중립"
    assert avgo_metrics["사용 표본"] == "21/21"
    assert avgo_metrics["동일시점 현재가"] == "$357.31"
    assert avgo_metrics["기준 적정가"] == "미산출"
    assert avgo_metrics["3회 분할매수"] == "비활성"
    assert avgo_metrics["현재 행동"] == "비활성"
    avgo_markdown = " ".join(item.value for item in app.markdown)
    assert "Same-Date Investment Decision Model v1" in " ".join(item.value for item in app.subheader)
    assert "산술 입력 · 행동 계산에는 미사용" in avgo_markdown
    assert "백테스트 상태" in avgo_markdown
    assert "자기 역사 TTM PER 분포" in avgo_markdown
    assert "DCF 스트레스 가격·할인율·거시 프록시·피어 배수는 이 계산에 사용하지 않았습니다" in avgo_rendered
    assert "reconstructed PIT 연구 전용" in avgo_rendered
    assert "전체 원본 21건 · 사용 21건 · 제외 0건" in avgo_rendered
    expander_states = {item.label: item.proto.expanded for item in app.expander}
    assert expander_states["자기 역사 표본·제외 규칙·한계"] is False

    app.selectbox[0].set_value("NVDA").run(timeout=APPTEST_TIMEOUT_SECONDS)
    assert not app.exception
    nvda_info = " ".join(item.value for item in app.info)
    nvda_captions = " ".join(item.value for item in app.caption)
    nvda_metrics = {item.label: item.value for item in app.metric}
    assert "StockLens 로컬 가격이 없어 v23 연구 입력만 표시합니다" in nvda_info
    assert "NVDA Q2 FCF: null 유지" in nvda_captions
    assert nvda_metrics["정상 관심 후보 · 연구 전용"] != "미산출"
    assert nvda_metrics["현재가"] == "$214.13"
    assert nvda_metrics["피어 목표 배수"] == "미생성"
    assert nvda_metrics["가치·행동 신호"] == "not_generated"
    nvda_rendered = " ".join(item.value for item in [*app.subheader, *app.warning, *app.info, *app.caption, *app.success, *app.error, *app.markdown])
    assert "P5.1.1의 임의 22배 행동 기준을 모두 철회" in nvda_rendered
    assert "superseded_circular_price_anchor" in nvda_rendered
    assert "적정가·매수구간·매도구간·연구 행동은 모두 미생성" in nvda_rendered
    assert "203.94 이하에서 1차 검토" not in nvda_rendered
    assert "조건부 연구 행동: 대기" not in nvda_rendered
    assert "research_action=null" in nvda_rendered
    assert "first_review_rule=null" in nvda_rendered
    assert "20·22·24배는 선택 근거가 없는 민감도 표" in nvda_rendered
    assert "approval_receipt_present" not in nvda_rendered
    for withdrawn in ("214.08", "203.42", "195.93", "188.43"):
        assert withdrawn not in nvda_rendered
    assert "operational_decision=null · operational_eligible=false" in nvda_rendered
    assert "NVDA Decision Model v2 · proxy_action" in nvda_rendered
    assert "provider_homogeneous_proxy" in nvda_rendered
    assert "verified_action=null · operational_eligible=false" in nvda_rendered
    assert "현재 가격에서 1차 매수를 시작" in nvda_rendered
    assert nvda_metrics["현재가 · proxy"] == "$228.52"
    assert nvda_metrics["현재 행동 · proxy"] == "분할매수 시작"
    assert nvda_metrics["목표 Forward 배수"] != "미생성"
    assert nvda_metrics["자기 역사 정상 관심 후보"] != "미산출"
    assert nvda_metrics["국면 조정 후보 · 연구 전용"] == nvda_metrics["자기 역사 정상 관심 후보"]
    assert nvda_metrics["사용 표본"] == "36/36"
    assert nvda_metrics["미래 EPS 저/기준/고"] == "$9.01 / $9.25 / $9.71"
    assert nvda_metrics["동일시점 현재가"] == "$228.52"
    assert nvda_metrics["기준 적정가"] == "미산출"
    assert nvda_metrics["현재 행동"] == "비활성"
    assert "동일 가격일 2026-09-11 교차검증" not in " ".join(item.value for item in app.success)

    original_builder = decision_model.build_decision_analysis_snapshot

    def _decision_must_not_run(*args, **kwargs):
        raise AssertionError("decision generation must not run without a verified audit")

    monkeypatch.setattr(decision_model, "build_decision_analysis_snapshot", _decision_must_not_run)
    missing_audit_path = decision_dir / "missing-audit.json"
    monkeypatch.setenv("STOCKLENS_DECISION_AUDIT_PATH", str(missing_audit_path))
    monkeypatch.delenv("STOCKLENS_DECISION_AUDIT_SHA256", raising=False)
    missing_audit_app = AppTest.from_file(
        str(Path(__file__).parents[1] / "personal_app.py")
    ).run(timeout=APPTEST_TIMEOUT_SECONDS)
    missing_audit_app.selectbox[0].set_value("AVGO").run(timeout=APPTEST_TIMEOUT_SECONDS)
    assert not missing_audit_app.exception
    missing_audit_rendered = " ".join(
        item.value for item in [*missing_audit_app.warning, *missing_audit_app.caption]
    )
    assert "evidence_preflight blocked: evidence audit is missing" in missing_audit_rendered
    assert "decision 객체 미생성" in missing_audit_rendered
    missing_labels = [item.label for item in missing_audit_app.metric]
    assert "현재 행동" not in missing_labels
    assert "기준 적정가" not in missing_labels
    assert "3회 분할매수" not in missing_labels
    monkeypatch.setattr(decision_model, "build_decision_analysis_snapshot", original_builder)

    blocked_report = {
        "audit_version": AUDIT_VERSION,
        "status": "blocked",
        "block_stage": "evidence_preflight",
        "decision_creation_allowed": False,
        "decision_created": False,
        "requirements": [
            {
                "id": "verified_pit_forward_eps",
                "status": "blocked",
                "passed": False,
                "reason": "fixture basis is unverified",
            }
        ],
    }
    audit_path.write_text(
        json.dumps(blocked_report),
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCKLENS_DECISION_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("STOCKLENS_DECISION_AUDIT_SHA256", _sha256(audit_path))
    preflight_app = AppTest.from_file(
        str(Path(__file__).parents[1] / "personal_app.py")
    ).run(timeout=APPTEST_TIMEOUT_SECONDS)
    preflight_app.selectbox[0].set_value("AVGO").run(timeout=APPTEST_TIMEOUT_SECONDS)
    assert not preflight_app.exception
    preflight_rendered = " ".join(
        item.value for item in [*preflight_app.warning, *preflight_app.caption]
    )
    assert "Same-Date 모듈 비활성" in preflight_rendered
    assert "evidence_preflight blocked: evidence audit status is not ready" in preflight_rendered

    legacy_bytes = legacy_path.read_bytes()
    decision_path.write_bytes(legacy_bytes)
    monkeypatch.setenv(
        "STOCKLENS_SAME_DATE_INPUT_SHA256",
        hashlib.sha256(legacy_bytes).hexdigest(),
    )
    legacy_app = AppTest.from_file(
        str(Path(__file__).parents[1] / "personal_app.py")
    ).run(timeout=APPTEST_TIMEOUT_SECONDS)
    legacy_app.selectbox[0].set_value("AVGO").run(timeout=APPTEST_TIMEOUT_SECONDS)
    assert not legacy_app.exception
    legacy_rendered = " ".join(
        item.value for item in [*legacy_app.warning, *legacy_app.caption]
    )
    assert "Same-Date 모듈 비활성" in legacy_rendered
    assert "decision 객체 미생성" in legacy_rendered
    assert "행동 판단·적정가·분할매수 표시 0개" in legacy_rendered
    assert "same-date calculation version is not approved" in legacy_rendered


def test_personal_app_renders_avgo_and_qcom_financial_analysis_without_prices(tmp_path, monkeypatch) -> None:
    financial_dir = tmp_path / "normalized" / "financials"
    financial_dir.mkdir(parents=True)
    guidance_by_ticker = {
        "AVGO": {"period": "Q4 FY2026", "revenue_approx": 140.0, "non_gaap_operating_margin_percent": 66.0},
        "QCOM": {"period": "Q4 FY2026", "revenue_low": 95.0, "revenue_high": 105.0, "gaap_eps_low": 1.2, "gaap_eps_high": 1.4},
    }
    for ticker in ("AVGO", "QCOM"):
        financial = {
            "company": ticker, "ticker": ticker, "status": "verified",
            "collected_at_utc": "2026-10-13T00:00:00+00:00",
            "periods": [
                {"kind": "fiscal_year", "label": "FY2025", "period_end": "2025-10-31", "accounting_basis": "reported_gaap", "metrics": {"revenue": 100.0, "operating_income": 25.0, "net_income": 20.0, "diluted_eps": 2.0, "diluted_shares": 10.0, "cfo": 30.0, "capex": 10.0, "fcf": 20.0}},
                {"kind": "quarter", "label": "Q3 FY2026", "period_end": "2026-07-31", "accounting_basis": "reported_gaap", "metrics": {"revenue": 30.0, "operating_income": 9.0, "net_income": 7.0, "diluted_eps": 0.7, "diluted_shares": 9.0, "cfo": 9.0, "capex": 3.0, "fcf": 6.0}},
                {"kind": "ttm", "label": "TTM through Q3 FY2026", "period_end": "2026-07-31", "accounting_basis": "derived_from_reported_gaap", "metrics": {"revenue": 120.0, "operating_income": 30.0, "net_income": 24.0, "diluted_eps": None, "diluted_shares": None, "cfo": 36.0, "capex": 12.0, "fcf": 24.0}},
            ],
            "balance_sheet": {"cash_and_equivalents": 10.0, "short_term_debt": 1.0, "long_term_debt": 4.0},
            "guidance": guidance_by_ticker[ticker],
            "documents": [{"key": "fixture", "published_at": "2026-08-13T20:01:00+00:00", "period_end": "2026-07-31", "distribution": "fixture", "sha256": "0" * 64, "url": "https://example.invalid/fixture"}],
        }
        (financial_dir / f"{ticker}.json").write_text(json.dumps(financial), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps({
        "financial_tickers_available": 2, "financial_periods_available": 6,
        "market": {"status": "blocked", "results": [
            {"ticker": "AVGO", "status": "blocked", "message": "safe"},
            {"ticker": "QCOM", "status": "blocked", "message": "safe"},
        ]},
    }), encoding="utf-8")
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))

    app = AppTest.from_file(str(Path(__file__).parents[1] / "personal_app.py")).run(
        timeout=APPTEST_TIMEOUT_SECONDS
    )
    assert not app.exception
    for ticker in ("AVGO", "QCOM"):
        app.selectbox[0].set_value(ticker).run(timeout=APPTEST_TIMEOUT_SECONDS)
        assert not app.exception
        rendered = " ".join(item.value for item in [*app.header, *app.subheader, *app.warning, *app.error, *app.markdown])
        assert ticker in rendered
        assert "가이던스" in rendered
        assert "운영 판단: hold" in rendered
        assert "정식 적정가" in rendered


def test_personal_app_registers_all_ten_and_keeps_tsm_blocked(tmp_path, monkeypatch) -> None:
    (tmp_path / "normalized" / "financials").mkdir(parents=True)
    (tmp_path / "status.json").write_text(
        json.dumps({"financial_tickers_available": 0, "financial_periods_available": 0, "market": {"status": "unavailable"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "personal_app.py")).run(
        timeout=APPTEST_TIMEOUT_SECONDS
    )

    assert not app.exception
    assert len(app.selectbox[0].options) == 10
    app.selectbox[0].set_value("TSM").run(timeout=APPTEST_TIMEOUT_SECONDS)
    rendered = " ".join(item.value for item in [*app.header, *app.warning, *app.info, *app.error])
    assert "TIFRS / TWD" in rendered
    assert "HTTP 403" in rendered
    assert "운영 판단: hold" in rendered


def test_personal_app_loads_official_macro_snapshot_and_explains_directions(tmp_path, monkeypatch) -> None:
    financial_dir = tmp_path / "normalized" / "financials"
    financial_dir.mkdir(parents=True)
    (financial_dir / "AMAT.json").write_text(
        json.dumps(
            {
                "company": "Applied Materials, Inc.",
                "ticker": "AMAT",
                "status": "verified",
                "collected_at_utc": "2026-10-13T00:00:00+00:00",
                "periods": [
                    {
                        "kind": "fiscal_year",
                        "label": "FY2025",
                        "period_end": "2025-10-26",
                        "accounting_basis": "reported_gaap",
                        "metrics": {"revenue": 100.0, "operating_income": 25.0, "net_income": 20.0, "diluted_eps": 8.0, "diluted_shares": 808.0, "cfo": 30.0, "capex": 10.0, "fcf": 20.0},
                    },
                    {
                        "kind": "ttm",
                        "label": "TTM through Q3 FY2026",
                        "period_end": "2026-07-26",
                        "accounting_basis": "derived_from_reported_gaap",
                        "metrics": {"revenue": 120.0, "operating_income": 30.0, "net_income": 24.0, "diluted_eps": None, "diluted_shares": None, "cfo": 36.0, "capex": 12.0, "fcf": 24.0},
                    },
                ],
                "balance_sheet": {"cash_and_equivalents": 10.0, "short_term_debt": 1.0, "long_term_debt": 4.0},
                "guidance": {"status": "unavailable"},
                "documents": [{"key": "fixture", "published_at": "2026-08-13T20:01:00+00:00", "period_end": "2026-07-26", "distribution": "fixture", "sha256": "0" * 64, "url": "https://example.invalid/fixture"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "financial_tickers_available": 1,
                "financial_periods_available": 2,
                "market": {"status": "unavailable"},
            }
        ),
        encoding="utf-8",
    )
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "market_regime_favorable.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot_path = tmp_path / "macro" / "market_regime_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps({"observations": payload["observations"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STOCKLENS_REGIME_SNAPSHOT_PATH", str(snapshot_path))

    app = AppTest.from_file(str(Path(__file__).parents[1] / "personal_app.py")).run(
        timeout=APPTEST_TIMEOUT_SECONDS
    )

    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["경기"] == "확장"
    assert metrics["물가"] == "물가 안정·둔화"
    assert metrics["리스크"] == "리스크온"
    rendered = " ".join(item.value for item in [*app.markdown, *app.caption, *app.info])
    assert "공식 입력의 방향성 설명" in rendered
    assert "operational_eligible=false" in rendered
