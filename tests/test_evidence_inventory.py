from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import pytest

from stocklens.personal.evidence_inventory import (
    DEFAULT_OUTPUT_NAME,
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
        writer.writerow({"date": "2026-10-12", "open": "100", "high": "102", "low": "99", "close": "101", "volume": "1000"})
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_roots(tmp_path: Path) -> tuple[Path, Path]:
    private_root, top10_root = tmp_path / "private_data", tmp_path / "top10"
    private_root.mkdir()
    csv_hash = _write_csv(private_root / "market" / "AVGO.csv")
    _write_json(private_root / "market" / "AVGO.manifest.json", {
        "collected_at_utc": "2026-10-13T12:02:37Z", "market_date": "2026-10-12",
        "provider_market_date": "2026-10-12", "adjusted_prices_available": False,
        "sha256": csv_hash,
    })
    common = {"generatedAt": "2026-10-15T22:30:26Z"}
    _write_json(top10_root / "price-history.json", {**common, "records": {"AVGO": [], "NVDA": []}})
    _write_json(top10_root / "top100-consensus.json", {**common, "records": {}})
    _write_json(top10_root / "decision-ledger.json", {**common, "records": []})
    _write_json(top10_root / "sec-quarterly-ledger.json", {**common, "records": {}})
    _write_json(top10_root / "macro-snapshot.json", {**common, "state": "neutral"})
    return private_root, top10_root


def _build(tmp_path: Path, inspected_at: str = "2026-10-16T08:00:00Z") -> tuple[dict, Path, Path]:
    private_root, top10_root = _build_roots(tmp_path)
    return build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root, inspected_at=inspected_at,
    ), private_root, top10_root


def test_inventory_remains_blocked_and_reports_execution_not_global_history(tmp_path: Path) -> None:
    report, _, _ = _build(tmp_path)
    assert report["status"] == "blocked"
    assert report["inventory_complete"] is False
    assert report["network_capability_present"] is False
    assert report["network_calls_observed_in_this_run"] == 0
    assert report["decisions_created_in_this_run"] == 0
    assert report["promotion_attempted"] is False
    assert report["operational_eligible"] is False


def test_manifest_completeness_and_unknown_file_detection(tmp_path: Path) -> None:
    report, _, top10_root = _build(tmp_path)
    assert report["manifest_version"] == "evidence-inventory-manifest-v1"
    assert report["expected_artifact_count"] == 22
    assert report["inspected_artifact_count"] == 7
    assert report["missing_artifact_count"] == 15
    _write_json(top10_root / "new-forward-eps-source.json", {"generatedAt": "2026-10-15T20:00:00Z"})
    refreshed = build_p4_evidence_inventory(
        private_data_root=tmp_path / "private_data", top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
    )
    assert refreshed["unclassified_artifact_count"] == 1
    assert refreshed["unclassified_relevant_artifacts"][0]["classification"] == "unclassified"


def test_known_history_guidance_and_valuation_are_dynamically_inspected(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    generated = "2026-10-15T22:00:00Z"
    _write_json(top10_root / "top100-consensus-history.json", {
        "generatedAt": generated,
        "observations": [
            {"ticker": ticker, "estimateType": kind, "accountingBasis": "adjusted/provider unknown"}
            for ticker in ("AVGO", "NVDA") for kind in ("FY1", "FY2")
        ],
    })
    _write_json(top10_root / "valuation-history.json", {
        "generatedAt": generated,
        "records": {ticker: [{"strictEligible": False, "pointInTimeVerified": False}] * 7 for ticker in ("AVGO", "NVDA")},
    })
    _write_json(top10_root / "guidance-event-ledger.json", {
        "generatedAt": generated, "events": [{"ticker": "AVGO"}, {"ticker": "NVDA"}],
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root, inspected_at="2026-10-16T08:00:00Z",
    )
    rows = {row["artifact_id"]: row for row in report["artifacts"]}
    assert rows["top10-consensus-history"]["target_records"] == {"AVGO": 2, "NVDA": 2}
    assert rows["top10-valuation-history"]["target_records"]["AVGO"] == {
        "rows": 7, "strict_eligible_rows": 0, "latest_point_in_time_verified": False,
    }
    assert rows["top10-guidance-events"]["target_records"] == {"AVGO": 1, "NVDA": 1}


def test_sec_counts_and_acceptance_timestamps_are_dynamic(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    _write_json(top10_root / "sec-quarterly-ledger.json", {
        "generatedAt": "2026-10-15T22:00:00Z",
        "records": {
            "AVGO": [{"ticker": "AVGO", "acceptedAt": "2026-09-02T20:10:00Z"}],
            "NVDA": {"ticker": "NVDA", "filed": "2026-08-26"},
        },
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root, inspected_at="2026-10-16T08:00:00Z",
    )
    sec = next(row for row in report["artifacts"] if row["artifact_id"] == "top10-sec-quarterly")
    assert sec["target_records"]["AVGO"] == {
        "records": 1, "records_with_acceptance_timestamp": 1, "acceptance_timestamp_count": 1,
    }
    assert sec["target_records"]["NVDA"]["records"] == 1
    assert sec["target_records"]["NVDA"]["acceptance_timestamp_count"] == 0


def test_manifest_hash_mismatch_is_invalid_integrity(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    (private_root / "market" / "AVGO.csv").write_text("date,close\n2026-10-12,999\n", encoding="utf-8")
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root, inspected_at="2026-10-16T08:00:00Z",
    )
    rows = {row["artifact_id"]: row for row in report["artifacts"]}
    assert rows["alpha-avgo-daily-data"]["classification"] == "invalid_integrity"
    assert rows["alpha-avgo-daily-manifest"]["classification"] == "invalid_integrity"
    assert report["invalid_artifact_count"] >= 2


@pytest.mark.parametrize("timestamp,status", [
    ("2026-10-17T00:00:00Z", "future_timestamp"),
    ("2026-10-15T22:00:00", "timezone_missing"),
])
def test_future_or_timezone_less_artifact_timestamp_is_blocked(
    tmp_path: Path, timestamp: str, status: str,
) -> None:
    _, private_root, top10_root = _build(tmp_path)
    _write_json(top10_root / "macro-snapshot.json", {"generatedAt": timestamp})
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root, inspected_at="2026-10-16T08:00:00Z",
    )
    macro = next(row for row in report["artifacts"] if row["artifact_id"] == "top10-macro-proxy")
    assert macro["inspection_status"] == status
    assert report["inventory_complete"] is False


def test_malformed_json_is_not_reported_as_missing(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    (top10_root / "macro-snapshot.json").write_text("{broken", encoding="utf-8")
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root, inspected_at="2026-10-16T08:00:00Z",
    )
    macro = next(row for row in report["artifacts"] if row["artifact_id"] == "top10-macro-proxy")
    assert macro["classification"] == "malformed"
    assert macro["inspection_status"] == "malformed"


def test_issuer_wording_and_raw_count_follow_actual_hash_link(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    for ticker, distribution, suffix in (
        ("AVGO", "PR Newswire issuer release", "html"),
        ("NVDA", "NVIDIA official investor relations", "pdf"),
    ):
        raw = private_root / "raw" / f"{ticker}_report.{suffix}"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(f"{ticker} raw".encode())
        _write_json(private_root / "normalized" / "financials" / f"{ticker}.json", {
            "documents": [{"distribution": distribution, "period_end": "2026-07-26",
                           "published_at": "2026-08-26T16:20:00-04:00",
                           "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}],
        })
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root, inspected_at="2026-10-16T08:00:00Z",
    )
    rows = {row["ticker"]: row for row in report["supporting_provider_native_evidence"]}
    assert report["provider_native_raw_count"] == 2
    assert rows["AVGO"]["official_issuer_origin_independently_verified"] is False
    assert rows["AVGO"]["source"] == "AVGO issuer-release supporting evidence distributed by PR Newswire"
    assert rows["NVDA"]["official_issuer_origin_independently_verified"] is True


def test_normal_private_output_succeeds(tmp_path: Path) -> None:
    report, private_root, _ = _build(tmp_path)
    output = write_p4_inventory(report, private_data_root=private_root)
    assert output == private_root / "decision" / DEFAULT_OUTPUT_NAME
    assert json.loads(output.read_text(encoding="utf-8"))["decisions_created_in_this_run"] == 0


@pytest.mark.parametrize("kind", ["tracked", "external_absolute", "dotdot"])
def test_output_escape_is_blocked_and_existing_output_unchanged(tmp_path: Path, kind: str) -> None:
    report, private_root, _ = _build(tmp_path)
    allowed = private_root / "decision" / DEFAULT_OUTPUT_NAME
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(b"prior inventory")
    if kind == "tracked":
        requested = tmp_path / "README.md"
    elif kind == "external_absolute":
        requested = tmp_path.parent / "external-inventory.json"
    else:
        requested = private_root / "decision" / ".." / "escaped.json"
    with pytest.raises(EvidenceInventoryError, match="must be private_data"):
        write_p4_inventory(report, private_data_root=private_root, output_path=requested)
    assert allowed.read_bytes() == b"prior inventory"


def test_output_collision_with_inspected_input_is_blocked(tmp_path: Path) -> None:
    report, private_root, _ = _build(tmp_path)
    allowed = private_root / "decision" / DEFAULT_OUTPUT_NAME
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(b"source evidence")
    with pytest.raises(EvidenceInventoryError, match="collides"):
        write_p4_inventory(report, private_data_root=private_root, protected_paths=[allowed])
    assert allowed.read_bytes() == b"source evidence"


def test_temporary_output_collision_with_inspected_input_is_blocked(tmp_path: Path) -> None:
    report, private_root, _ = _build(tmp_path)
    allowed = private_root / "decision" / DEFAULT_OUTPUT_NAME
    temporary = allowed.with_suffix(allowed.suffix + ".tmp")
    with pytest.raises(EvidenceInventoryError, match="collides"):
        write_p4_inventory(report, private_data_root=private_root, protected_paths=[temporary])
    assert not allowed.exists()
    assert not temporary.exists()


def test_symlink_or_junction_output_escape_is_blocked(tmp_path: Path) -> None:
    report, private_root, _ = _build(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, private_root / "decision", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    with pytest.raises(EvidenceInventoryError, match="resolves outside"):
        write_p4_inventory(report, private_data_root=private_root)
    assert list(outside.iterdir()) == []


def test_invalid_inspection_timestamp_is_blocked(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    with pytest.raises(EvidenceInventoryError, match="timezone-aware"):
        build_p4_evidence_inventory(
            private_data_root=private_root, top10_data_root=top10_root, inspected_at="2026-10-16",
        )
