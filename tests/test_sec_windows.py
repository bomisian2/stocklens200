from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from stocklens.financials import FinancialDataError, KNOWN_CIKS
from stocklens.sec_files import SecFileProvider, sha256_file
from stocklens.sec_windows import SAMPLE_TICKERS, SecWindowsDownloader, analyze_raw_sec_files


def _annual(year: int, value: float, *, ticker: str) -> dict:
    filed_year = year + 1
    return {
        "start": f"{year}-01-01",
        "end": f"{year}-12-31",
        "val": value,
        "accn": f"{ticker}-{year}",
        "fy": year,
        "fp": "FY",
        "form": "10-K",
        "filed": f"{filed_year}-02-01",
    }


def _fact(rows: list[dict], unit: str = "USD") -> dict:
    return {"label": "test", "description": "test", "units": {unit: rows}}


def _companyfacts(ticker: str, cik: int) -> dict:
    years = (2021, 2022, 2023)
    return {
        "cik": cik,
        "entityName": ticker,
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": _fact([
                _annual(year, 100_000 + (year - 2021) * 10_000, ticker=ticker)
                for year in years
            ]),
            "OperatingIncomeLoss": _fact([
                _annual(year, 28_000, ticker=ticker) for year in years
            ]),
            "NetIncomeLoss": _fact([
                _annual(year, 22_000, ticker=ticker) for year in years
            ]),
            "EarningsPerShareDiluted": _fact([
                _annual(year, 5.0, ticker=ticker) for year in years
            ], "USD/shares"),
            "WeightedAverageNumberOfDilutedSharesOutstanding": _fact([
                _annual(year, 4_000, ticker=ticker) for year in years
            ], "shares"),
            "NetCashProvidedByUsedInOperatingActivities": _fact([
                _annual(year, 30_000, ticker=ticker) for year in years
            ]),
            "PaymentsToAcquirePropertyPlantAndEquipment": _fact([
                _annual(year, 10_000, ticker=ticker) for year in years
            ]),
        }},
    }


def _submissions(ticker: str, cik: int) -> dict:
    years = (2021, 2022, 2023)
    return {
        "cik": str(cik),
        "tickers": [ticker],
        "filings": {"recent": {
            "accessionNumber": [f"{ticker}-{year}" for year in years],
            "acceptanceDateTime": [f"{year + 1}-02-01T21:00:00Z" for year in years],
            "form": ["10-K" for _ in years],
        }},
    }


class _Response:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status_code = status
        self.content = json.dumps(payload or {}).encode("utf-8")
        self.headers = {
            "Server": "test",
            "Content-Type": "application/json",
            "Set-Cookie": "must-not-be-recorded",
        }

    def close(self) -> None:
        return None


class _Session:
    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> _Response:
        self.calls.append(url)
        if self.status != 200:
            return _Response(self.status)
        if "company_tickers" in url:
            return _Response(200, {
                str(index): {"ticker": ticker, "cik_str": KNOWN_CIKS[ticker]}
                for index, ticker in enumerate(SAMPLE_TICKERS)
            })
        ticker = next(
            ticker for ticker, cik in KNOWN_CIKS.items() if f"CIK{cik:010d}" in url
        )
        if "companyfacts" in url:
            return _Response(200, _companyfacts(ticker, KNOWN_CIKS[ticker]))
        return _Response(200, _submissions(ticker, KNOWN_CIKS[ticker]))


def _downloader(session: _Session) -> SecWindowsDownloader:
    ticks = iter(range(100))
    return SecWindowsDownloader(
        user_agent=(
            "StockLens200/2.2 "
            "(https://github.com/bomisian2/stocklens200; "
            "mailto:bomisian2@users.noreply.github.com)"
        ),
        session=session,
        sleep=lambda _: None,
        monotonic=lambda: float(next(ticks)),
        now=lambda: datetime(2024, 3, 1, 12, tzinfo=timezone.utc),
    )


def test_first_403_stops_after_the_single_probe_and_leaves_no_raw_files(tmp_path: Path) -> None:
    session = _Session(status=403)
    downloader = _downloader(session)
    try:
        downloader.collect(
            output_root=tmp_path,
            as_of=datetime(2024, 3, 1, tzinfo=timezone.utc),
        )
    except FinancialDataError as exc:
        assert exc.diagnostic.http_status == 403
        assert exc.diagnostic.endpoint == "companyfacts"
        assert "set-cookie" not in (exc.diagnostic.response_headers or {})
    else:
        raise AssertionError("HTTP 403 must stop the collection")
    assert len(session.calls) == 1
    assert downloader.request_count == 1
    assert downloader.company_requests_started is True
    assert list(tmp_path.iterdir()) == []


def test_successful_run_writes_hash_verified_files_and_uses_offline_provider(
    tmp_path: Path,
) -> None:
    session = _Session()
    downloader = _downloader(session)
    result = downloader.collect(
        output_root=tmp_path,
        as_of=datetime(2024, 3, 1, tzinfo=timezone.utc),
    )

    assert len(session.calls) == 9
    assert result["artifact_count"] == 9
    assert result["period_count"] == 12
    run_dir = Path(result["run_directory"])
    manifest_path = Path(result["manifest"])
    provider = SecFileProvider.from_manifest(manifest_path)
    for ticker in SAMPLE_TICKERS:
        rows = provider.fetch_periods(
            ticker, KNOWN_CIKS[ticker],
            as_of=datetime(2024, 3, 1, tzinfo=timezone.utc),
        )
        assert len(rows) == 3
        assert rows[-1].fcf == 20_000
        assert rows[-1].source == "sec_edgar_offline_import"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        assert sha256_file(run_dir / artifact["path"]) == artifact["sha256"]
    report = json.loads((run_dir / "collection_report.json").read_text(encoding="utf-8"))
    assert report["retry_count"] == 0
    assert report["total_request_count"] == 9


def test_raw_analysis_flags_amendments_and_changed_eps_comparatives() -> None:
    facts = _companyfacts("GOOGL", KNOWN_CIKS["GOOGL"])
    eps_rows = facts["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"]
    eps_rows.append({**eps_rows[-1], "val": 2.5, "form": "10-K/A", "accn": "amended"})
    submissions = _submissions("GOOGL", KNOWN_CIKS["GOOGL"])
    submissions["filings"]["recent"]["form"].append("10-K/A")
    analysis = analyze_raw_sec_files(
        ticker="GOOGL", cik=KNOWN_CIKS["GOOGL"],
        companyfacts=facts, submissions=submissions,
    )
    assert analysis["companyfacts_cik"] == KNOWN_CIKS["GOOGL"]
    assert analysis["amendment_form_count"] == 1
    assert analysis["eps_restatement_or_split_adjustment_candidate_years"] == [2023]
    assert all(analysis["metric_tag_coverage"].values()) is False

