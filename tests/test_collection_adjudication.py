from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal import collection_adjudication as adjudication
from stocklens.personal.evidence_inventory import EvidenceInventoryError


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _daily() -> bytes:
    return json.dumps({
        "Meta Data": {"2. Symbol": "AVGO", "4. Output Size": "Compact"},
        "Time Series (Daily)": {
            "2026-10-15": {
                "1. open": "350", "2. high": "360", "3. low": "349",
                "4. close": "357", "5. volume": "1000",
            },
            "2026-10-14": {
                "1. open": "345", "2. high": "355", "3. low": "344",
                "4. close": "352", "5. volume": "900",
            },
        },
    }).encode()


def _make_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    private = tmp_path / "private_data"
    private.mkdir()
    monkeypatch.setattr(adjudication, "resolve_repository_private_data", lambda *args, **kwargs: private)
    run_id = "approved-fixture"
    run_root = private / "raw" / "alpha_vantage" / run_id
    run_root.mkdir(parents=True)
    requests = [
        ("alpha-vantage-terms", "terms-of-service.response.raw", None, None, b"%PDF terms", True, "valid_terms_document"),
        ("avgo-daily", "AVGO_TIME_SERIES_DAILY.response.raw", "TIME_SERIES_DAILY", "AVGO", _daily(), True, "valid_unadjusted_daily"),
        ("nvda-daily", "NVDA_TIME_SERIES_DAILY.response.raw", "TIME_SERIES_DAILY", "NVDA", b'{"Information":"redacted"}', False, "provider_error_envelope"),
        ("avgo-estimates", "AVGO_EARNINGS_ESTIMATES.response.raw", "EARNINGS_ESTIMATES", "AVGO", b'{"Information":"redacted"}', False, "provider_error_envelope"),
        ("nvda-estimates", "NVDA_EARNINGS_ESTIMATES.response.raw", "EARNINGS_ESTIMATES", "NVDA", b'{"Information":"redacted"}', False, "provider_error_envelope"),
    ]
    results = []
    for request_id, filename, function, ticker, payload, valid, code in requests:
        raw = run_root / filename
        raw.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest().upper()
        content_type = "application/pdf" if function is None else "application/json"
        manifest = {
            "request_id": request_id, "function": function, "ticker": ticker,
            "content_type": content_type, "byte_length": len(payload),
            "response_sha256": digest,
            "capture_state": "captured" if valid else "invalid_provider_dataset",
            "valid_provider_dataset": valid, "validation_code": code,
        }
        _write_json(raw.with_suffix(raw.suffix + ".manifest.json"), manifest)
        results.append({**manifest, "response_file": filename})
    _write_json(run_root / "collection-run.json", {
        "run_id": run_id, "run_state": "failed", "failure_code": "data_collection_incomplete",
        "results": results, "parser_attempted": False, "decision_created": False,
        "operational_eligible": False,
    })
    return private, run_id


def test_adjudication_is_hash_linked_redacted_and_never_promotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, run_id = _make_run(tmp_path, monkeypatch)
    result = adjudication.adjudicate_alpha_run(
        project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T13:00:00Z",
    )
    value = json.loads(result["adjudication_path"].read_text(encoding="utf-8"))
    statuses = {row["request_id"]: row["adjudication"] for row in value["requests"]}
    assert statuses == {
        "alpha-vantage-terms": "captured_valid",
        "avgo-daily": "captured_unadjusted_daily",
        "nvda-daily": "invalid_provider_dataset",
        "avgo-estimates": "invalid_provider_dataset",
        "nvda-estimates": "invalid_provider_dataset",
    }
    invalid = [row for row in value["requests"] if row["adjudication"] == "invalid_provider_dataset"]
    assert all(row["restriction_class"] == "provider_rate_or_plan_limit" for row in invalid)
    assert all(row["counts_toward_same_date_required_raw"] is False for row in invalid)
    assert value["parser_candidate_count"] == 0
    assert value["decision_created"] is value["operational_eligible"] is False
    assert "redacted" not in result["adjudication_path"].read_text(encoding="utf-8")
    assert result["collection_run_sha256"] == hashlib.sha256(
        (private / f"raw/alpha_vantage/{run_id}/collection-run.json").read_bytes()
    ).hexdigest().upper()


def test_avgo_daily_limits_and_terms_permissions_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, run_id = _make_run(tmp_path, monkeypatch)
    result = adjudication.adjudicate_alpha_run(
        project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T13:00:00Z",
    )
    value = json.loads(result["adjudication_path"].read_text(encoding="utf-8"))
    avgo = next(row for row in value["requests"] if row["request_id"] == "avgo-daily")
    assert avgo["record_count"] == 2
    assert avgo["first_market_date"] == "2026-10-14"
    assert avgo["last_market_date"] == "2026-10-15"
    assert avgo["price_adjustment_policy"] == "as_traded_unadjusted"
    assert "forward_per_ledger" in avgo["prohibited_uses"]
    terms = json.loads(result["terms_review_path"].read_text(encoding="utf-8"))
    assert terms["reviewed_by"] is terms["reviewed_at"] is None
    assert terms["scope_assessments"]["local_storage"]["status"] == "manual_review_required"
    assert terms["scope_assessments"]["public_display"]["status"] == "not_authorized"
    assert terms["scope_assessments"]["redistribution"]["status"] == "not_authorized"


def test_existing_outputs_and_changed_raw_are_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, run_id = _make_run(tmp_path, monkeypatch)
    adjudication.adjudicate_alpha_run(
        project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T13:00:00Z",
    )
    with pytest.raises(EvidenceInventoryError, match="already exists"):
        adjudication.adjudicate_alpha_run(
            project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T13:01:00Z",
        )
    for path in (private / "decision").iterdir():
        path.unlink()
    raw = private / f"raw/alpha_vantage/{run_id}/AVGO_TIME_SERIES_DAILY.response.raw"
    raw.write_bytes(raw.read_bytes() + b"changed")
    with pytest.raises(EvidenceInventoryError, match="SHA-256 mismatch"):
        adjudication.adjudicate_alpha_run(
            project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T13:02:00Z",
        )
