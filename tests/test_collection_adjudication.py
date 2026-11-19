from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from stocklens.personal import collection_adjudication as adjudication
from stocklens.personal.alpha_evidence_validation import validate_dataset_bytes, validate_terms_bytes
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


def _pdf_page(writer: PdfWriter, text: str) -> None:
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    stream = DecodedStreamObject()
    commands = ["BT /F1 11 Tf 40 740 Td"]
    for index, line in enumerate(text.splitlines()):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({escaped}) Tj")
    commands.append("ET")
    stream.set_data(" ".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = stream


def _terms_pdf(*, include_clauses: bool = True) -> bytes:
    writer = PdfWriter()
    _pdf_page(
        writer,
        "TERMS OF SERVICE\nAlpha Vantage\n2. Grant of License.\n"
        + ("For personal, non-commercial use." if include_clauses else "General license terms."),
    )
    _pdf_page(
        writer,
        "Alpha Vantage\n4. Use Restrictions.\n15. Assignment."
        if include_clauses else "Alpha Vantage",
    )
    _pdf_page(
        writer,
        "Alpha Vantage\n7. Termination."
        if include_clauses else "Alpha Vantage",
    )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _make_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    private = tmp_path / "private_data"
    private.mkdir(parents=True)
    monkeypatch.setattr(adjudication, "resolve_repository_private_data", lambda *args, **kwargs: private)
    run_id = "approved-fixture"
    run_root = private / "raw" / "alpha_vantage" / run_id
    run_root.mkdir(parents=True)
    requests = [
        ("alpha-vantage-terms", "terms-of-service.response.raw", None, None, _terms_pdf(), True, "valid_terms_document"),
        ("avgo-daily", "AVGO_TIME_SERIES_DAILY.response.raw", "TIME_SERIES_DAILY", "AVGO", _daily(), True, "valid_unadjusted_daily"),
        ("nvda-daily", "NVDA_TIME_SERIES_DAILY.response.raw", "TIME_SERIES_DAILY", "NVDA", b'{"Information":"redacted"}', False, "provider_error_envelope"),
        ("avgo-estimates", "AVGO_EARNINGS_ESTIMATES.response.raw", "EARNINGS_ESTIMATES", "AVGO", b'{"Information":"redacted"}', False, "provider_error_envelope"),
        ("nvda-estimates", "NVDA_EARNINGS_ESTIMATES.response.raw", "EARNINGS_ESTIMATES", "NVDA", b'{"Information":"redacted"}', False, "provider_error_envelope"),
    ]
    results = []
    base_second = 1
    for index, (request_id, filename, function, ticker, payload, valid, code) in enumerate(requests):
        raw = run_root / filename
        raw.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest().upper()
        content_type = "application/pdf" if function is None else "application/json"
        start_second = base_second + (index * 3)
        started = f"2026-10-16T12:00:{start_second:02d}Z"
        headers = f"2026-10-16T12:00:{start_second + 1:02d}Z"
        completed = f"2026-10-16T12:00:{start_second + 2:02d}Z"
        request_path = "/terms_of_service/" if function is None else "/query"
        manifest = {
            "request_id": request_id, "function": function, "ticker": ticker,
            "content_type": content_type, "byte_length": len(payload),
            "response_sha256": digest,
            "capture_state": "captured" if valid else "invalid_provider_dataset",
            "valid_provider_dataset": valid, "validation_code": code,
            "request_started_at": started, "headers_received_at": headers,
            "response_completed_at": completed, "available_at": completed,
            "timestamp_order_valid": True, "http_status": 200,
            "request_host": "www.alphavantage.co", "request_path": request_path,
            "final_url": f"https://www.alphavantage.co{request_path}",
            "redirect_chain": [], "request_query_persisted": False,
            "api_key_persisted": False,
        }
        _write_json(raw.with_suffix(raw.suffix + ".manifest.json"), manifest)
        results.append({**manifest, "response_file": filename})
    _write_json(run_root / "collection-run.json", {
        "run_id": run_id, "run_state": "failed", "failure_code": "data_collection_incomplete",
        "results": results, "calls_attempted": 5, "calls_completed": 5,
        "automatic_retries": 0, "parser_attempted": False, "candidate_created": False,
        "snapshot_created": False, "decision_created": False, "operational_eligible": False,
        "started_at": "2026-10-16T12:00:00Z", "finished_at": "2026-10-16T12:00:16Z",
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
    assert all(row["restriction_class"] == "provider_restriction_unknown" for row in invalid)
    assert all(row["restriction_rule_version"] == "alpha-vantage-error-semantics-v1" for row in invalid)
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
    assert terms["terms_document"]["page_count"] == 3
    assert len(terms["terms_document"]["page_text_sha256"]) == 3
    assert terms["terms_document"]["document_title"] == "TERMS OF SERVICE"
    assert terms["terms_document"]["document_title_verified"] is True
    assert terms["clause_detection_version"] == "alpha-vantage-terms-heading-context-v1"
    assert terms["reviewed_by"] is terms["reviewed_at"] is None
    assert terms["scope_assessments"]["local_storage"]["status"] == "manual_review_required"
    assert terms["scope_assessments"]["public_display"]["status"] == "not_authorized"
    assert terms["scope_assessments"]["redistribution"]["status"] == "not_authorized"


def test_terms_pdf_uses_actual_pages_hashes_and_clauses(tmp_path: Path) -> None:
    payload = _terms_pdf()
    validation = validate_terms_bytes(payload, "application/pdf")
    assert validation["valid_provider_dataset"] is True
    assert validation["terms_inspection"]["page_count"] == 3
    assert len(validation["terms_inspection"]["page_text_sha256"]) == 3
    assert validate_terms_bytes(b"not-a-pdf", "application/pdf")["valid_provider_dataset"] is False
    missing = validate_terms_bytes(_terms_pdf(include_clauses=False), "application/pdf")
    assert missing["valid_provider_dataset"] is False
    assert missing["terms_inspection"]["clauses"]["termination"]["status"] == "missing"


def test_terms_clause_detection_rejects_incidental_disclaimer_words() -> None:
    writer = PdfWriter()
    _pdf_page(
        writer,
        "TERMS OF SERVICE\nAlpha Vantage\n2. Grant of License.\n"
        "For personal, non-commercial use.",
    )
    _pdf_page(
        writer,
        "Alpha Vantage\nExcept where prohibited by law.\nSuccessors and assigns may benefit.",
    )
    output = io.BytesIO()
    writer.write(output)
    inspection = validate_terms_bytes(output.getvalue(), "application/pdf")["terms_inspection"]
    assert inspection["clause_detection_version"] == "alpha-vantage-terms-heading-context-v1"
    assert inspection["clauses"]["use_restrictions"]["status"] == "missing"
    assert inspection["clauses"]["assignment_or_transfer"]["status"] == "missing"
    assert inspection["clauses"]["termination"]["status"] == "missing"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Standard API rate limit is 25 requests per day; subscribe for premium access.", "provider_rate_or_plan_limit"),
        ("", "provider_restriction_unknown"),
        ("Invalid API key. Please provide a valid API key.", "provider_authentication_error"),
        ("Invalid symbol or parameter for this function.", "provider_request_error"),
    ],
)
def test_provider_error_semantic_classification_never_copies_text(message: str, expected: str) -> None:
    marker = f"{message} SECRET-MARKER"
    result = validate_dataset_bytes(
        json.dumps({"Information": marker if message else ""}).encode(),
        "application/json", function="TIME_SERIES_DAILY", ticker="NVDA",
    )
    assert result["validation_code"] == "provider_error_envelope"
    assert result["provider_restriction_class"] == expected
    assert result["provider_restriction_rule_version"] == "alpha-vantage-error-semantics-v1"
    assert "SECRET-MARKER" not in json.dumps(result)


def test_ledger_duplicate_order_and_generation_time_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, run_id = _make_run(tmp_path, monkeypatch)
    ledger_path = private / f"raw/alpha_vantage/{run_id}/collection-run.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["results"][1] = dict(ledger["results"][0])
    _write_json(ledger_path, ledger)
    with pytest.raises(EvidenceInventoryError, match="duplicate request IDs"):
        adjudication.adjudicate_alpha_run(
            project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T13:00:00Z",
        )

    _, run_id = _make_run(tmp_path / "second", monkeypatch)
    with pytest.raises(EvidenceInventoryError, match="precedes collection completion"):
        adjudication.adjudicate_alpha_run(
            project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T11:59:59Z",
        )


def test_ledger_capture_mismatch_and_query_persistence_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, run_id = _make_run(tmp_path, monkeypatch)
    capture_path = private / f"raw/alpha_vantage/{run_id}/AVGO_TIME_SERIES_DAILY.response.raw.manifest.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    capture["ticker"] = "NVDA"
    _write_json(capture_path, capture)
    with pytest.raises(EvidenceInventoryError, match="ledger/capture ticker mismatch"):
        adjudication.adjudicate_alpha_run(
            project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T13:00:00Z",
        )

    private, run_id = _make_run(tmp_path / "query", monkeypatch)
    capture_path = private / f"raw/alpha_vantage/{run_id}/AVGO_TIME_SERIES_DAILY.response.raw.manifest.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    capture["final_url"] = "https://www.alphavantage.co/query?apikey=forbidden"
    _write_json(capture_path, capture)
    with pytest.raises(EvidenceInventoryError, match="query-free endpoint"):
        adjudication.adjudicate_alpha_run(
            project_root=tmp_path, run_id=run_id, generated_at="2026-10-16T13:00:00Z",
        )


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
