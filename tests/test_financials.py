from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from stocklens.financials import (
    FinancialDataError,
    KNOWN_CIKS,
    SecClient,
    build_cik_map,
    extract_financial_periods,
    merge_cik_maps,
    validate_ticker_cik,
)
from stocklens.sec_files import SecFileProvider, normalized_periods_sha256, sha256_file


def _fact(label: str, rows: list[dict], unit: str = "USD") -> dict:
    return {"label": label, "description": label, "units": {unit: rows}}


def _annual(year: int, value: float, *, filed: str, accn: str, start: str | None = None) -> dict:
    row = {
        "end": f"{year}-12-31",
        "val": value,
        "accn": accn,
        "fy": year,
        "fp": "FY",
        "form": "10-K",
        "filed": filed,
    }
    if start:
        row["start"] = start
    return row


def test_build_cik_map_normalizes_share_class_symbols() -> None:
    payload = {"0": {"ticker": "BRK-B", "cik_str": 1067983}, "1": {"ticker": "GOOGL", "cik_str": "1652044"}}
    assert build_cik_map(payload) == {"BRK.B": 1067983, "GOOGL": 1652044}


def test_official_sample_ciks_are_fixed_and_conflicts_fail_closed() -> None:
    assert KNOWN_CIKS == {
        "GOOGL": 1652044,
        "AMAT": 6951,
        "AVGO": 1730168,
        "QCOM": 804328,
    }
    validate_ticker_cik("AMAT", 6951)
    try:
        merge_cik_maps(KNOWN_CIKS, {"AMAT": 697900}, provider="test")
    except FinancialDataError as exc:
        assert exc.diagnostic.category == "ticker_cik_mismatch"
        assert exc.diagnostic.endpoint == "AMAT"
    else:
        raise AssertionError("conflicting AMAT CIK must be rejected")


def test_extract_periods_is_point_in_time_and_uses_latest_split_adjusted_comparative() -> None:
    older = _annual(2023, 5.0, filed="2024-02-01", accn="old", start="2023-01-01")
    restated = _annual(2023, 2.5, filed="2025-02-01", accn="new", start="2023-01-01")
    future = _annual(2024, 3.0, filed="2026-02-01", accn="future", start="2024-01-01")
    base = [_annual(2023, 100.0, filed="2024-02-01", accn="old", start="2023-01-01")]
    facts = {
        "entityName": "Example",
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", base),
            "NetIncomeLoss": _fact("Income", [_annual(2023, 25.0, filed="2024-02-01", accn="old", start="2023-01-01")]),
            "EarningsPerShareDiluted": _fact("EPS", [older, restated, future], "USD/shares"),
            "WeightedAverageNumberOfDilutedSharesOutstanding": _fact("Shares", [_annual(2023, 10.0, filed="2024-02-01", accn="old", start="2023-01-01")], "shares"),
            "NetCashProvidedByUsedInOperatingActivities": _fact("CFO", [_annual(2023, 30.0, filed="2024-02-01", accn="old", start="2023-01-01")]),
            "PaymentsToAcquirePropertyPlantAndEquipment": _fact("Capex", [_annual(2023, 7.0, filed="2024-02-01", accn="old", start="2023-01-01")]),
        }},
    }
    submissions = {"filings": {"recent": {"accessionNumber": ["old", "new", "future"], "acceptanceDateTime": ["2024-02-01T21:00:00Z", "2025-02-01T21:00:00Z", "2026-02-01T21:00:00Z"]}}}
    rows = extract_financial_periods(
        ticker="EX",
        cik=1,
        companyfacts=facts,
        submissions=submissions,
        as_of=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].diluted_eps == 2.5
    assert rows[0].fcf == 23.0
    assert rows[0].available_at_utc == "2025-02-01T21:00:00+00:00"
    assert rows[0].availability_basis == "sec_acceptance_timestamp"
    assert rows[0].availability_estimated is False


def test_future_acceptance_is_excluded_even_when_filed_date_is_not_future() -> None:
    old = _annual(2023, 100.0, filed="2024-02-01", accn="old", start="2023-01-01")
    future_amendment = _annual(2023, 999.0, filed="2024-02-01", accn="future", start="2023-01-01")
    facts = {
        "entityName": "Example",
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", [old, future_amendment]),
            "NetIncomeLoss": _fact("Income", [{**old, "val": 25.0}]),
            "WeightedAverageNumberOfDilutedSharesOutstanding": _fact("Shares", [{**old, "val": 10.0}], "shares"),
            "NetCashProvidedByUsedInOperatingActivities": _fact("CFO", [{**old, "val": 30.0}]),
        }},
    }
    submissions = {"filings": {"recent": {
        "accessionNumber": ["old", "future"],
        "acceptanceDateTime": ["2024-02-01T21:00:00Z", "2025-02-01T21:00:00Z"],
    }}}
    rows = extract_financial_periods(
        ticker="EX", cik=1, companyfacts=facts, submissions=submissions,
        as_of=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].revenue == 100.0
    assert rows[0].available_at_utc == "2024-02-01T21:00:00+00:00"


def test_missing_acceptance_uses_marked_conservative_next_day_end() -> None:
    row = _annual(2023, 100.0, filed="2024-02-01", accn="missing", start="2023-01-01")
    facts = {"entityName": "Example", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", [row]),
        "NetIncomeLoss": _fact("Income", [{**row, "val": 25.0}]),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _fact("Shares", [{**row, "val": 10.0}], "shares"),
        "NetCashProvidedByUsedInOperatingActivities": _fact("CFO", [{**row, "val": 30.0}]),
    }}}
    too_early = extract_financial_periods(
        ticker="EX", cik=1, companyfacts=facts, submissions={},
        as_of=datetime(2024, 2, 2, 12, tzinfo=timezone.utc),
    )
    assert too_early == []
    rows = extract_financial_periods(
        ticker="EX", cik=1, companyfacts=facts, submissions={},
        as_of=datetime(2024, 2, 3, tzinfo=timezone.utc),
    )
    assert rows[0].available_at_utc == "2024-02-02T23:59:59+00:00"
    assert rows[0].availability_estimated is True
    assert rows[0].availability_basis == "estimated_end_of_next_utc_day_from_filed_date"
    assert rows[0].validation_state == "degraded"


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers = {"Server": "AkamaiGHost", "Content-Type": "text/html", "Set-Cookie": "must-not-log"}

    def json(self) -> dict:
        return {}


class _Session:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    def get(self, *args, **kwargs):
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return _Response(status)


def test_sec_403_is_not_retried_and_diagnostic_does_not_leak_headers() -> None:
    session = _Session([403, 200])
    client = SecClient(session=session, attempts=4, minimum_interval=0, sleep=lambda _: None)
    try:
        client.get_json("https://data.sec.gov/api/xbrl/companyfacts/CIK0001652044.json")
    except FinancialDataError as exc:
        diagnostic = exc.diagnostic.to_dict()
    else:
        raise AssertionError("403 must fail")
    assert session.calls == 1
    assert diagnostic["category"] == "http"
    assert diagnostic["http_status"] == 403
    assert diagnostic["domain"] == "data.sec.gov"
    assert "set-cookie" not in diagnostic["response_headers"]


def test_sec_transient_error_is_bounded_and_can_recover() -> None:
    session = _Session([429, 200])
    client = SecClient(session=session, attempts=4, minimum_interval=0, sleep=lambda _: None)
    assert client.get_json("https://data.sec.gov/submissions/CIK0001652044.json") == {}
    assert session.calls == 2


def test_official_offline_manifest_uses_same_point_in_time_rules(tmp_path: Path) -> None:
    row = _annual(2023, 100.0, filed="2024-02-01", accn="a", start="2023-01-01")
    facts = {"entityName": "Example", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", [row]),
        "NetIncomeLoss": _fact("Income", [{**row, "val": 25.0}]),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _fact("Shares", [{**row, "val": 10.0}], "shares"),
        "NetCashProvidedByUsedInOperatingActivities": _fact("CFO", [{**row, "val": 30.0}]),
    }}}
    submissions = {"cik": "1", "tickers": ["EX"], "filings": {"recent": {
        "accessionNumber": ["a"], "acceptanceDateTime": ["2024-02-01T21:00:00Z"],
    }}}
    facts_zip = tmp_path / "companyfacts.zip"
    submissions_zip = tmp_path / "submissions.zip"
    with zipfile.ZipFile(facts_zip, "w") as archive:
        archive.writestr("CIK0000000001.json", json.dumps(facts))
    with zipfile.ZipFile(submissions_zip, "w") as archive:
        archive.writestr("CIK0000000001.json", json.dumps(submissions))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "provider": "sec_edgar_official_files",
        "obtained_at_utc": "2024-03-01T12:00:00+00:00",
        "fixture": False,
        "artifacts": [
            {
                "role": "companyfacts", "path": facts_zip.name,
                "source_url": "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
                "sha256": sha256_file(facts_zip),
            },
            {
                "role": "submissions", "path": submissions_zip.name,
                "source_url": "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip",
                "sha256": sha256_file(submissions_zip),
            },
        ],
    }), encoding="utf-8")
    provider = SecFileProvider.from_manifest(manifest)
    rows = provider.fetch_periods(
        "EX", 1, as_of=datetime(2024, 3, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].source == "sec_edgar_offline_import"
    assert rows[0].availability_estimated is False
    assert len(rows[0].source_artifact_sha256) == 64
    provenance = provider.provenance(periods=rows)
    assert provenance["normalized_sha256"] == normalized_periods_sha256(rows)
    assert provenance["fixture"] is False


def test_offline_provider_rejects_ticker_not_supported_by_official_identity(tmp_path: Path) -> None:
    facts = tmp_path / "facts.json"
    submissions = tmp_path / "submissions.json"
    facts.write_text(json.dumps({"cik": 1, "entityName": "Example", "facts": {}}), encoding="utf-8")
    submissions.write_text(json.dumps({"cik": "1", "tickers": ["RIGHT"]}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "provider": "sec_edgar_official_files",
        "obtained_at_utc": "2024-03-01T12:00:00+00:00",
        "fixture": False,
        "artifacts": [
            {
                "role": "companyfacts", "path": facts.name,
                "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
                "sha256": sha256_file(facts), "cik": 1,
            },
            {
                "role": "submissions", "path": submissions.name,
                "source_url": "https://data.sec.gov/submissions/CIK0000000001.json",
                "sha256": sha256_file(submissions), "cik": 1,
            },
        ],
    }), encoding="utf-8")
    provider = SecFileProvider.from_manifest(manifest)
    try:
        provider.fetch_periods(
            "WRONG", 1, as_of=datetime(2024, 3, 1, tzinfo=timezone.utc)
        )
    except FinancialDataError as exc:
        assert exc.diagnostic.category == "ticker_cik_identity_unverified"
    else:
        raise AssertionError("ticker absent from official identity data must be rejected")


def test_offline_manifest_rejects_fixture_and_hash_mismatch(tmp_path: Path) -> None:
    facts = tmp_path / "CIK0000000001.json"
    facts.write_text(json.dumps({"cik": 1, "facts": {}}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    base = {
        "schema_version": 1,
        "provider": "sec_edgar_official_files",
        "obtained_at_utc": "2024-03-01T12:00:00+00:00",
        "fixture": True,
        "artifacts": [{
            "role": "companyfacts", "path": facts.name,
            "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
            "sha256": sha256_file(facts), "cik": 1,
        }],
    }
    manifest.write_text(json.dumps(base), encoding="utf-8")
    try:
        SecFileProvider.from_manifest(manifest)
    except ValueError as exc:
        assert "fixture=false" in str(exc)
    else:
        raise AssertionError("fixture manifest must not be treated as operational data")

    base["fixture"] = False
    base["artifacts"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(base), encoding="utf-8")
    try:
        SecFileProvider.from_manifest(manifest)
    except ValueError as exc:
        assert "SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("hash mismatch must be rejected")


def test_offline_manifest_rejects_non_sec_source_url(tmp_path: Path) -> None:
    facts = tmp_path / "CIK0000000001.json"
    facts.write_text(json.dumps({"cik": 1, "facts": {}}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "provider": "sec_edgar_official_files",
        "obtained_at_utc": "2024-03-01T12:00:00+00:00",
        "fixture": False,
        "artifacts": [{
            "role": "companyfacts", "path": facts.name,
            "source_url": "https://example.com/companyfacts.json",
            "sha256": sha256_file(facts), "cik": 1,
        }],
    }), encoding="utf-8")
    try:
        SecFileProvider.from_manifest(manifest)
    except ValueError as exc:
        assert "official HTTPS SEC URL" in str(exc)
    else:
        raise AssertionError("non-SEC provenance URL must be rejected")


def test_non_annual_duration_is_rejected_during_fiscal_year_change() -> None:
    short = _annual(2024, 100.0, filed="2025-01-01", accn="a", start="2024-07-01")
    facts = {"entityName": "Short Year", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", [short]),
    }}}
    rows = extract_financial_periods(
        ticker="SHORT", cik=2, companyfacts=facts, submissions={},
        as_of=datetime(2025, 2, 1, tzinfo=timezone.utc),
    )
    assert rows == []


def test_latest_standalone_quarter_is_recorded_separately() -> None:
    quarter = {
        "start": "2024-07-01", "end": "2024-09-30", "val": 30.0, "accn": "q3",
        "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-10-25",
    }
    facts = {"entityName": "Quarterly", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", [quarter]),
        "NetIncomeLoss": _fact("Income", [{**quarter, "val": 5.0}]),
    }}}
    submissions = {"filings": {"recent": {"accessionNumber": ["q3"], "acceptanceDateTime": ["2024-10-25T20:05:00Z"]}}}
    rows = extract_financial_periods(
        ticker="QTR", cik=3, companyfacts=facts, submissions=submissions,
        as_of=datetime(2024, 11, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].period_type == "Q"
    assert rows[0].available_at_utc == "2024-10-25T20:05:00+00:00"

