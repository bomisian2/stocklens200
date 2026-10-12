from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from stocklens.financials import FinancialDataError
from stocklens.ir_files import IssuerIrFileProvider
from stocklens.sec_files import sha256_file


def _manifest(tmp_path: Path, *, fcf: float = 70.0, available: str = "2025-02-05T23:59:59+00:00") -> Path:
    report = tmp_path / "alphabet-report.pdf"
    report.write_bytes(b"%PDF-1.4\nissuer IR fixture\n%%EOF")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "provider": "issuer_ir_official_files",
        "obtained_at_utc": "2025-03-01T12:00:00+00:00",
        "fixture": False,
        "artifacts": [{
            "role": "financial_report_pdf",
            "path": report.name,
            "ticker": "GOOGL",
            "cik": 1652044,
            "source_url": "https://s206.q4cdn.com/example/alphabet-report.pdf",
            "sha256": sha256_file(report),
        }],
        "periods": [{
            "ticker": "GOOGL",
            "cik": 1652044,
            "company_name": "Alphabet Inc.",
            "fiscal_year": 2024,
            "period_type": "FY",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "filed_at": "2025-02-04",
            "available_at_utc": available,
            "availability_basis": "estimated_end_of_next_utc_day_from_issuer_release_date",
            "availability_estimated": True,
            "form": "issuer_annual_report",
            "accession": "",
            "currency": "USD",
            "taxonomy": "us-gaap",
            "revenue": 350.0,
            "operating_income": 112.0,
            "net_income": 100.0,
            "pretax_income": 120.0,
            "income_tax": 20.0,
            "diluted_eps": 8.04,
            "diluted_shares": 12.45,
            "cfo": 125.0,
            "capex": 55.0,
            "fcf": fcf,
            "cash": 23.0,
            "total_debt": 11.0,
            "equity": 325.0,
            "sbc": 23.0,
            "depreciation_amortization": 15.0,
            "buybacks": 62.0,
            "artifact": report.name,
            "validation_notes": "fixture values",
        }],
    }), encoding="utf-8")
    return manifest


def test_issuer_ir_manifest_converts_hash_verified_report_to_financial_period(tmp_path: Path) -> None:
    provider = IssuerIrFileProvider.from_manifest(_manifest(tmp_path))
    rows = provider.fetch_periods(
        "GOOGL", 1652044, as_of=datetime(2025, 3, 1, tzinfo=timezone.utc)
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.fcf == 70.0
    assert row.source == "alphabet_official_ir"
    assert row.validation_state == "degraded"
    assert row.availability_estimated is True
    assert len(row.source_artifact_sha256) == 64
    provenance = provider.provenance(periods=rows)
    assert provenance["normalized_period_count"] == 1
    assert provenance["fixture"] is False


def test_issuer_ir_manifest_excludes_period_not_yet_available(tmp_path: Path) -> None:
    provider = IssuerIrFileProvider.from_manifest(_manifest(tmp_path))
    rows = provider.fetch_periods(
        "GOOGL", 1652044, as_of=datetime(2025, 2, 5, 12, tzinfo=timezone.utc)
    )
    assert rows == []


def test_issuer_ir_manifest_rejects_bad_fcf_arithmetic(tmp_path: Path) -> None:
    provider = IssuerIrFileProvider.from_manifest(_manifest(tmp_path, fcf=69.0))
    try:
        provider.fetch_periods(
            "GOOGL", 1652044, as_of=datetime(2025, 3, 1, tzinfo=timezone.utc)
        )
    except FinancialDataError as exc:
        assert exc.diagnostic.category == "offline_schema"
    else:
        raise AssertionError("bad FCF arithmetic must be rejected")


def test_issuer_ir_manifest_rejects_unapproved_host_and_hash_mismatch(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["artifacts"][0]["source_url"] = "https://example.com/alphabet.pdf"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    try:
        IssuerIrFileProvider.from_manifest(manifest)
    except ValueError as exc:
        assert "allow-listed official IR host" in str(exc)
    else:
        raise AssertionError("non-issuer host must be rejected")

    payload["artifacts"][0]["source_url"] = "https://s206.q4cdn.com/example/alphabet.pdf"
    payload["artifacts"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    try:
        IssuerIrFileProvider.from_manifest(manifest)
    except ValueError as exc:
        assert "SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("hash mismatch must be rejected")


def test_issuer_ir_manifest_rejects_file_with_pdf_extension_but_no_pdf_signature(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    report = tmp_path / "alphabet-report.pdf"
    report.write_bytes(b"not a pdf")
    payload["artifacts"][0]["sha256"] = sha256_file(report)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    try:
        IssuerIrFileProvider.from_manifest(manifest)
    except ValueError as exc:
        assert "PDF signature" in str(exc)
    else:
        raise AssertionError("non-PDF content must be rejected")
