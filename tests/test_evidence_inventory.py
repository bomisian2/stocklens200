from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.evidence_inventory import (
    EvidenceInventoryError,
    build_p4_evidence_inventory,
    write_p4_inventory,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerow({
            "date": "2026-10-12",
            "open": "100",
            "high": "102",
            "low": "99",
            "close": "101",
            "volume": "1000",
        })
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_roots(tmp_path: Path) -> tuple[Path, Path]:
    private_root = tmp_path / "private_data"
    top10_root = tmp_path / "top10"
    csv_hash = _write_csv(private_root / "market" / "AVGO.csv")
    _write_json(private_root / "market" / "AVGO.manifest.json", {
        "ticker": "AVGO",
        "provider": "Alpha Vantage",
        "dataset": "TIME_SERIES_DAILY",
        "collected_at_utc": "2026-10-13T12:02:37Z",
        "market_date": "2026-10-12",
        "provider_market_date": "2026-10-12",
        "price_policy": "raw_as_traded_unadjusted",
        "adjusted_prices_available": False,
        "sha256": csv_hash,
    })
    _write_json(top10_root / "price-history.json", {
        "generatedAt": "2026-10-15T22:30:26Z",
        "methodology": "Yahoo Finance 5-year confirmed daily data compressed monthly",
        "records": {
            "AVGO": [{"date": "2026-10-15", "close": 344.33}],
            "NVDA": [{"date": "2026-10-15", "close": 217.23}],
        },
    })
    _write_json(top10_root / "top100-consensus.json", {
        "generatedAt": "2026-10-15T22:31:59Z",
        "source": "Nasdaq Analyst Research earnings forecast API",
        "records": {
            ticker: {
                "ticker": ticker,
                "collectedAt": "2026-10-15T22:31:59Z",
                "fiscalEnd": "Oct 2026",
                "nextFiscalEnd": "Oct 2027",
                "analystCount": 10,
            }
            for ticker in ("AVGO", "NVDA")
        },
    })
    _write_json(top10_root / "decision-ledger.json", {
        "generatedAt": "2026-10-15T22:48:28Z",
        "asOfDate": "2026-10-15",
        "records": [],
    })
    _write_json(top10_root / "sec-quarterly-ledger.json", {
        "generatedAt": "2026-10-16T01:10:54Z",
        "records": {},
    })
    _write_json(top10_root / "macro-snapshot.json", {
        "generatedAt": "2026-10-15T22:31:13Z",
        "state": "neutral",
    })
    return private_root, top10_root


def test_inventory_is_read_only_and_blocks_without_provider_native_raw(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    report = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00+09:00",
    )
    assert report["status"] == "blocked"
    assert report["block_stage"] == "provider_native_evidence_inventory"
    assert report["provider_native_raw_count"] == 0
    assert report["provider_parser_eligible_count"] == 0
    assert report["provider_parsers_implemented"] == []
    assert report["candidate_generation_attempted"] is False
    assert report["approval_requested"] is False
    assert report["v1_1_snapshot_created"] is False
    assert report["decision_created"] is False
    assert report["network_calls"] == 0


def test_alpha_vantage_csv_is_actual_normalized_cache_not_provider_raw(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    report = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-15T23:00:00Z",
    )
    rows = {row["candidate_id"]: row for row in report["candidates"]}
    avgo = rows["alpha-vantage-avgo-daily"]
    assert avgo["classification"] == "normalized_only"
    assert avgo["provider_native_raw_exists"] is False
    assert avgo["manifest_hash_matches_data"] is True
    assert avgo["as_of"] == "2026-10-12"
    assert avgo["available_at"] == "2026-10-13T12:02:37Z"
    assert avgo["provider_timestamp"] is None
    assert avgo["adjusted"] is False
    assert avgo["parser_eligible"] is False


def test_cross_project_files_remain_normalized_or_derived_and_rights_fail_closed(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    report = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-15T23:00:00Z",
    )
    rows = {row["candidate_id"]: row for row in report["candidates"]}
    assert rows["top10-avgo-nvda-price-history"]["classification"] == "normalized_only"
    assert rows["top10-company-forward-per-ledger"]["classification"] == "derived_only"
    assert rows["top10-market-regime-proxy"]["classification"] == "derived_only"
    assert rows["usage-rights-documents"]["classification"] == "missing"
    assert rows["independent-qqq-soxx-peer-forward-multiples"]["classification"] == "missing"
    assert all(row["provider_native_raw_exists"] is False for row in rows.values())


def test_inventory_output_is_private_path_only_and_does_not_mutate_sources(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    before = hashlib.sha256((top10_root / "top100-consensus.json").read_bytes()).hexdigest()
    report = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-15T23:00:00Z",
    )
    output = write_p4_inventory(report, private_root / "decision" / "evidence-inventory-p4.json")
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["decision_created"] is False
    after = hashlib.sha256((top10_root / "top100-consensus.json").read_bytes()).hexdigest()
    assert before == after


def test_issuer_native_financial_document_is_supporting_not_same_date_target(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    raw_path = private_root / "raw" / "NVDA_q2fy2027.pdf"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"%PDF-1.7\nissuer financial evidence\n")
    raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    _write_json(private_root / "normalized" / "financials" / "NVDA.json", {
        "ticker": "NVDA",
        "documents": [{
            "distribution": "NVIDIA official investor relations",
            "period_end": "2026-07-26",
            "published_at": "2026-08-26T16:20:00-04:00",
            "sha256": raw_hash,
        }],
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-15T23:00:00Z",
    )
    assert report["same_date_target_provider_native_raw_count"] == 0
    assert report["supporting_provider_native_raw_count"] == 1
    supporting = report["supporting_provider_native_evidence"][0]
    assert supporting["classification"] == "provider_native"
    assert supporting["same_date_target_roles_satisfied"] == []
    assert supporting["source_hash"] == raw_hash.upper()


def test_invalid_inspection_timestamp_and_json_are_blocked(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    with pytest.raises(EvidenceInventoryError, match="timezone-aware"):
        build_p4_evidence_inventory(
            private_data_root=private_root,
            top10_data_root=top10_root,
            inspected_at="2026-10-16",
        )
    (top10_root / "macro-snapshot.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(EvidenceInventoryError, match="invalid JSON"):
        build_p4_evidence_inventory(
            private_data_root=private_root,
            top10_data_root=top10_root,
            inspected_at="2026-10-15T23:00:00Z",
        )
