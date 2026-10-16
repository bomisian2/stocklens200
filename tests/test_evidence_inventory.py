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
    resolve_repository_private_data,
    validate_inventory_source_set,
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
    assert report["expected_artifact_count"] == 34
    assert report["inspected_artifact_count"] == 7
    assert report["missing_artifact_count"] == 25
    assert report["optional_missing_artifact_count"] == 2
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
    assert rows["NVDA"]["official_issuer_origin_independently_verified"] is False


def test_forged_distribution_text_cannot_verify_official_issuer_origin(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    raw = private_root / "raw" / "NVDA_report.pdf"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"issuer bytes")
    _write_json(private_root / "normalized" / "financials" / "NVDA.json", {
        "documents": [{
            "distribution": "NVIDIA official investor relations",
            "source_url": "https://attacker.example/nvda",
            "final_url": "https://attacker.example/nvda",
            "period_end": "2026-07-26",
            "published_at": "2026-08-26T16:20:00-04:00",
            "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        }],
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
    )
    evidence = report["supporting_provider_native_evidence"][0]
    assert evidence["official_issuer_origin_independently_verified"] is False


def test_verified_official_issuer_requires_source_and_final_official_domains(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    raw = private_root / "raw" / "NVDA_report.pdf"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"issuer bytes")
    _write_json(private_root / "normalized" / "financials" / "NVDA.json", {
        "documents": [{
            "distribution": "arbitrary display text",
            "source_url": "https://investor.nvidia.com/financial-info/default.aspx",
            "final_url": "https://investor.nvidia.com/financial-info/default.aspx",
            "period_end": "2026-07-26",
            "published_at": "2026-08-26T16:20:00-04:00",
            "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        }],
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
    )
    evidence = report["supporting_provider_native_evidence"][0]
    assert evidence["official_issuer_origin_independently_verified"] is True


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


@pytest.mark.parametrize("bad_path", ["../escape.json", "/absolute.json", "C:/escape.json"])
def test_manifest_artifact_path_escape_is_blocked(tmp_path: Path, bad_path: str) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    manifest = json.loads(Path("config/evidence-inventory-manifest-v1.json").read_text(encoding="utf-8"))
    manifest["artifacts"][0]["path"] = bad_path
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)
    with pytest.raises(EvidenceInventoryError, match="must not be absolute|portable relative"):
        build_p4_evidence_inventory(
            private_data_root=private_root,
            top10_data_root=top10_root,
            inspected_at="2026-10-16T08:00:00Z",
            inventory_manifest_path=manifest_path,
        )


def test_known_exclusion_path_escape_is_blocked(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    manifest = json.loads(Path("config/evidence-inventory-manifest-v1.json").read_text(encoding="utf-8"))
    manifest["known_exclusions"][0]["path"] = "../escape.json"
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)
    with pytest.raises(EvidenceInventoryError, match="must not be absolute"):
        build_p4_evidence_inventory(
            private_data_root=private_root,
            top10_data_root=top10_root,
            inspected_at="2026-10-16T08:00:00Z",
            inventory_manifest_path=manifest_path,
        )


def test_manifest_artifact_symlink_escape_is_blocked(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "terms.html").write_text("terms", encoding="utf-8")
    try:
        os.symlink(outside, private_root / "escape", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    manifest = json.loads(Path("config/evidence-inventory-manifest-v1.json").read_text(encoding="utf-8"))
    manifest["artifacts"][0]["path"] = "escape/terms.html"
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)
    with pytest.raises(EvidenceInventoryError, match="resolves outside"):
        build_p4_evidence_inventory(
            private_data_root=private_root,
            top10_data_root=top10_root,
            inspected_at="2026-10-16T08:00:00Z",
            inventory_manifest_path=manifest_path,
        )


def test_source_set_hash_detects_added_deleted_or_changed_files(tmp_path: Path) -> None:
    report, _, top10_root = _build(tmp_path)
    assert validate_inventory_source_set(report, top10_root)["inventory_status"] == "current"
    _write_json(top10_root / "volume-breakout-study.json", {"generatedAt": "2026-10-16T07:23:59Z"})
    stale = validate_inventory_source_set(report, top10_root)
    assert stale["inventory_status"] == "stale"
    assert stale["source_set_hash"] != report["source_set_hash"]


@pytest.mark.parametrize(
    ("relative", "payload"),
    [
        ("raw/alpha_vantage/new-run/AVGO_TIME_SERIES_DAILY.response.raw", b"raw"),
        ("raw/alpha_vantage/new-run/AVGO_TIME_SERIES_DAILY.response.raw.manifest.json", b"{}"),
        ("rights/alpha_vantage/new-run/provider-terms-manifest.json", b"{}"),
        ("normalized/financials/NEW.json", b"{}"),
    ],
)
def test_complete_source_set_detects_added_private_evidence(
    tmp_path: Path, relative: str, payload: bytes,
) -> None:
    report, private_root, top10_root = _build(tmp_path)
    path = private_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    stale = validate_inventory_source_set(report, top10_root)
    assert stale["inventory_status"] == "stale"
    assert stale["source_set_hash"] != report["source_set_hash"]


def test_complete_source_set_detects_manifest_change_and_top10_deletion(tmp_path: Path) -> None:
    private_root, top10_root = _build_roots(tmp_path)
    manifest = tmp_path / "inventory-manifest.json"
    manifest.write_bytes(Path("config/evidence-inventory-manifest-v1.json").read_bytes())
    report = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
        inventory_manifest_path=manifest,
    )
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert validate_inventory_source_set(report, top10_root)["inventory_status"] == "stale"
    refreshed = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
        inventory_manifest_path=manifest,
    )
    (top10_root / "price-history.json").unlink()
    assert validate_inventory_source_set(refreshed, top10_root)["inventory_status"] == "stale"


def test_inventory_output_itself_is_excluded_from_source_set(tmp_path: Path) -> None:
    report, private_root, top10_root = _build(tmp_path)
    write_p4_inventory(report, private_data_root=private_root)
    assert validate_inventory_source_set(report, top10_root)["inventory_status"] == "current"


def test_volume_breakout_study_is_explicitly_excluded(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    _write_json(top10_root / "volume-breakout-study.json", {"generatedAt": "2026-10-16T07:23:59Z"})
    report = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
    )
    assert report["unclassified_artifact_count"] == 0
    assert any(Path(row["path"]).name == "volume-breakout-study.json" for row in report["explicitly_excluded_artifacts"])


def test_record_timestamp_counts_future_and_missing_target_records(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    _write_json(top10_root / "top100-consensus-history.json", {
        "generatedAt": "2026-10-15T22:00:00Z",
        "observations": [
            {"ticker": "AVGO", "collectedAt": "2026-10-17T00:00:00Z"},
            {"ticker": "NVDA", "collectedAt": None},
        ],
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root,
        top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
        decision_time="2026-10-16T07:00:00Z",
    )
    row = next(item for item in report["artifacts"] if item["artifact_id"] == "top10-consensus-history")
    validation = row["record_timestamp_validation"]
    assert validation["future_timestamp_count"] == 1
    assert validation["missing_timestamp_count"] == 1
    assert validation["affected_tickers"] == ["AVGO", "NVDA"]


def test_provider_raw_is_discovered_but_not_parser_candidate_without_rights(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    raw = private_root / "raw" / "alpha_vantage" / "AVGO_TIME_SERIES_DAILY.response.raw"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b'{"Time Series (Daily)": {}}')
    _write_json(raw.with_suffix(raw.suffix + ".manifest.json"), {
        "provider": "Alpha Vantage",
        "function": "TIME_SERIES_DAILY",
        "ticker": "AVGO",
        "captured_at": "2026-10-15T20:00:00Z",
        "http_status": 200,
        "final_url": "https://www.alphavantage.co/query",
        "content_type": "application/json",
        "byte_length": len(raw.read_bytes()),
        "response_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "capture_state": "captured",
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
    )
    row = next(item for item in report["artifacts"] if item["artifact_id"] == "alpha-avgo-daily-raw")
    assert row["classification"] == "discovered_unverified"
    assert row["provider_native_raw_exists"] is True
    assert row["parser_eligible"] is False
    assert report["same_date_target_provider_native_raw_count"] == 1
    assert report["provider_parser_eligible_count"] == 0


def test_empty_dataset_and_self_declared_rights_cannot_create_parser_candidate(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    raw = private_root / "raw" / "alpha_vantage" / "AVGO_TIME_SERIES_DAILY.response.raw"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b'{"Time Series (Daily)": {}}')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    _write_json(raw.with_suffix(raw.suffix + ".manifest.json"), {
        "provider": "Alpha Vantage", "function": "TIME_SERIES_DAILY", "ticker": "AVGO",
        "request_started_at": "2026-10-15T19:59:58Z",
        "headers_received_at": "2026-10-15T19:59:59Z",
        "response_completed_at": "2026-10-15T20:00:00Z",
        "available_at": "2026-10-15T20:00:00Z", "http_status": 200,
        "final_url": "https://www.alphavantage.co/query", "content_type": "application/json",
        "byte_length": len(raw.read_bytes()), "response_sha256": digest, "capture_state": "captured",
        "valid_provider_dataset": True,
    })
    terms = private_root / "raw/alpha_vantage/terms-of-service.response.raw"
    terms.write_bytes(b"<html>terms</html>")
    terms_digest = hashlib.sha256(terms.read_bytes()).hexdigest()
    terms_capture = terms.with_suffix(terms.suffix + ".manifest.json")
    _write_json(terms_capture, {
        "provider": "Alpha Vantage", "function": None, "ticker": None,
        "request_started_at": "2026-10-15T19:59:50Z",
        "headers_received_at": "2026-10-15T19:59:51Z",
        "response_completed_at": "2026-10-15T19:59:52Z",
        "available_at": "2026-10-15T19:59:52Z", "http_status": 200,
        "final_url": "https://www.alphavantage.co/terms_of_service/", "content_type": "text/html",
        "byte_length": len(terms.read_bytes()), "response_sha256": terms_digest,
        "capture_state": "captured", "valid_provider_dataset": True,
    })
    _write_json(private_root / "rights" / "provider-terms-manifest.json", {
        "personal_noncommercial_confirmed": True,
        "local_storage_permission": "allowed",
        "derived_analysis_permission": "allowed",
        "local_display_permission": "allowed",
        "terms_source_hash": terms_digest,
        "terms_capture_manifest_hash": hashlib.sha256(terms_capture.read_bytes()).hexdigest(),
        "linked_response_hashes": [terms_digest, digest],
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root,
        inspected_at="2026-10-16T08:00:00Z",
    )
    row = next(item for item in report["artifacts"] if item["artifact_id"] == "alpha-avgo-daily-raw")
    assert row["classification"] == "discovered_unverified"
    assert row["parser_eligible"] is False
    assert row["candidate_checks"]["valid_provider_dataset"] is False
    assert row["candidate_checks"]["rights_scope"] is False
    assert row["rights_review_checks"]["terms_source_hash"] is True
    assert row["rights_review_checks"]["terms_capture_manifest_hash"] is True
    assert row["rights_review_checks"]["reviewed_by"] is False
    assert row["rights_review_checks"]["review_result"] is False
    assert report["provider_parser_eligible_count"] == 0


def test_provider_response_completed_after_decision_time_is_blocked(tmp_path: Path) -> None:
    _, private_root, top10_root = _build(tmp_path)
    raw = private_root / "raw" / "alpha_vantage" / "AVGO_TIME_SERIES_DAILY.response.raw"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps({
        "Meta Data": {"2. Symbol": "AVGO"},
        "Time Series (Daily)": {
            "2026-10-13": {
                "1. open": "100", "2. high": "102", "3. low": "99",
                "4. close": "101", "5. volume": "1000",
            }
        },
    }), encoding="utf-8")
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    _write_json(raw.with_suffix(raw.suffix + ".manifest.json"), {
        "provider": "Alpha Vantage", "function": "TIME_SERIES_DAILY", "ticker": "AVGO",
        "request_started_at": "2026-10-16T07:59:58Z",
        "headers_received_at": "2026-10-16T07:59:59Z",
        "response_completed_at": "2026-10-16T08:00:01Z",
        "available_at": "2026-10-16T08:00:01Z",
        "http_status": 200, "final_url": "https://www.alphavantage.co/query",
        "content_type": "application/json", "byte_length": len(raw.read_bytes()),
        "response_sha256": digest, "capture_state": "captured", "valid_provider_dataset": True,
    })
    report = build_p4_evidence_inventory(
        private_data_root=private_root, top10_data_root=top10_root,
        inspected_at="2026-10-16T08:01:00Z", decision_time="2026-10-16T08:00:00Z",
    )
    row = next(item for item in report["artifacts"] if item["artifact_id"] == "alpha-avgo-daily-raw")
    assert row["candidate_checks"]["request_response_timestamps"] is False
    assert row["parser_eligible"] is False


def test_private_root_name_is_fail_closed(tmp_path: Path) -> None:
    wrong_root = tmp_path / "not-private"
    wrong_root.mkdir()
    top10 = tmp_path / "top10"
    top10.mkdir()
    with pytest.raises(EvidenceInventoryError, match="named private_data"):
        build_p4_evidence_inventory(
            private_data_root=wrong_root,
            top10_data_root=top10,
            inspected_at="2026-10-16T08:00:00Z",
        )


def test_repository_private_root_rejects_non_repository_external_root(tmp_path: Path) -> None:
    with pytest.raises(EvidenceInventoryError, match="verified Git repository"):
        resolve_repository_private_data(tmp_path, require_exists=False)


def test_inventory_cli_has_no_private_data_root_override() -> None:
    source = Path("scripts/inventory_same_date_evidence.py").read_text(encoding="utf-8")
    assert "--private-data-root" not in source
    assert "resolve_repository_private_data(REPOSITORY_ROOT)" in source
