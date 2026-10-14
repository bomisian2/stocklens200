from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from stocklens.personal.macro_snapshot import (
    CBOE_VIX_DOWNLOAD_URL,
    CONNECT_TIMEOUT_SECONDS,
    FRED_SERIES,
    READ_TIMEOUT_SECONDS,
    OfficialMacroSnapshotError,
    collect_official_macro_snapshot,
    fred_series_url,
    fred_series_window,
    normalize_official_macro_responses,
    parse_cboe_vix_csv,
    parse_fred_csv,
    parse_fred_series_csv,
)
from stocklens.personal.market_regime import build_market_regime, load_market_regime_snapshot


FIXTURES = Path(__file__).parent / "fixtures"
FRED_COMBINED = (FIXTURES / "official_macro_fred.csv").read_bytes()
VIX = (FIXTURES / "official_macro_vix.csv").read_bytes()
ANALYSIS_DATE = date(2026, 9, 9)
ANALYSIS_TIME = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _series_fixture(series: str, *, encoding: str = "utf-8") -> bytes:
    reader = csv.DictReader(io.StringIO(FRED_COMBINED.decode("utf-8")))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["observation_date", series])
    writer.writeheader()
    for row in reader:
        writer.writerow({"observation_date": row["observation_date"], series: row[series]})
    return output.getvalue().encode(encoding)


# Nine deterministic per-series fixtures derived from one fixed, reviewed source table.
FRED_SERIES_FIXTURES = {series: _series_fixture(series) for series in FRED_SERIES}


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        status_code: int = 200,
        url: str = "",
        headers: dict[str, str] | None = None,
        stream_error_after_chunks: int | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.url = url
        self.headers = headers or {"Content-Type": "text/csv"}
        self.stream_error_after_chunks = stream_error_after_chunks

    def iter_content(self, chunk_size: int) -> object:
        step = max(1, min(chunk_size, 32))
        chunks = [self.content[index : index + step] for index in range(0, len(self.content), step)]
        for index, chunk in enumerate(chunks):
            if self.stream_error_after_chunks is not None and index >= self.stream_error_after_chunks:
                raise requests.ReadTimeout("fixture stream timeout")
            yield chunk

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse | BaseException]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        response = self.responses[url]
        if isinstance(response, BaseException):
            raise response
        return response


def _responses(
    *,
    failures: dict[str, FakeResponse | BaseException] | None = None,
    include_vix: bool = True,
) -> dict[str, FakeResponse | BaseException]:
    result: dict[str, FakeResponse | BaseException] = {}
    failures = failures or {}
    for series in FRED_SERIES:
        url = fred_series_url(series, ANALYSIS_DATE)
        result[url] = failures.get(series, FakeResponse(FRED_SERIES_FIXTURES[series], url=url))
    if include_vix:
        result[CBOE_VIX_DOWNLOAD_URL] = failures.get(
            "cboe_vix", FakeResponse(VIX, url=CBOE_VIX_DOWNLOAD_URL)
        )
    return result


def _write_qqq_fixture(root: Path) -> tuple[Path, Path]:
    market = root / "market"
    market.mkdir(parents=True, exist_ok=True)
    csv_path = market / "QQQ.csv"
    rows = ["date,open,high,low,close,volume"]
    start = date(2026, 7, 1)
    for index in range(70):
        observed = start + timedelta(days=index)
        close = 500.0 + index
        rows.append(f"{observed.isoformat()},{close-1},{close+1},{close-2},{close},1000000")
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    manifest_path = market / "QQQ.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sha256": digest,
                "market_date": (start + timedelta(days=69)).isoformat(),
                "collected_at_utc": ANALYSIS_TIME.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return csv_path, manifest_path


def test_nine_split_fred_fixtures_parse_and_windows_are_minimal() -> None:
    assert set(FRED_SERIES_FIXTURES) == set(FRED_SERIES)
    for series, payload in FRED_SERIES_FIXTURES.items():
        assert parse_fred_series_csv(payload, series)
        start, end = fred_series_window(series, ANALYSIS_DATE)
        assert end == ANALYSIS_DATE
        if series in {"CPIAUCSL", "PCEPI", "UNRATE", "PAYEMS"}:
            assert start == date(2024, 9, 1)
        else:
            assert (end - start).days == 180
        url = fred_series_url(series, ANALYSIS_DATE)
        assert "fred.stlouisfed.org/graph/fredgraph.csv" in url
        assert f"id={series}" in url


def test_legacy_combined_parser_and_normalization_remain_reproducible() -> None:
    fred = parse_fred_csv(FRED_COMBINED)
    observed_on, vix = parse_cboe_vix_csv(VIX)
    observations = normalize_official_macro_responses(
        FRED_COMBINED, VIX, collected_at=ANALYSIS_TIME.isoformat()
    )
    assert len(fred["DTWEXBGS"]) == 70
    assert observed_on.isoformat() == "2026-09-08"
    assert vix == 16.4
    assert len(observations) == 11
    assert observations == normalize_official_macro_responses(
        FRED_COMBINED, VIX, collected_at=ANALYSIS_TIME.isoformat()
    )


def test_utf8_and_utf16_single_series_fixtures() -> None:
    utf8_values = parse_fred_series_csv(FRED_SERIES_FIXTURES["DFF"], "DFF")
    utf16_bytes = _series_fixture("DFF", encoding="utf-16")
    assert utf16_bytes.startswith((b"\xff\xfe", b"\xfe\xff"))
    assert parse_fred_series_csv(utf16_bytes, "DFF") == utf8_values


def test_complete_split_collection_promotes_after_all_ten_requests(tmp_path: Path) -> None:
    qqq_csv, qqq_manifest = _write_qqq_fixture(tmp_path)
    session = FakeSession(_responses())
    result = collect_official_macro_snapshot(
        tmp_path,
        session=session,  # type: ignore[arg-type]
        now=ANALYSIS_TIME,
        qqq_csv_path=qqq_csv,
        qqq_manifest_path=qqq_manifest,
    )
    snapshot = json.loads(Path(result.snapshot_path).read_text(encoding="utf-8"))
    assert result.requests_made == 10
    assert len(session.calls) == 10
    assert all(call[1]["timeout"] == (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS) for call in session.calls)
    assert all(call[1]["stream"] is True for call in session.calls)
    assert snapshot["collection_status"] == "complete"
    assert snapshot["coverage_gate"]["overall"] >= 0.60
    assert all(value >= 0.60 for value in snapshot["coverage_gate"]["axes"].values())
    assert len(result.source_hashes) == 10
    assert len(list((tmp_path / "macro" / "raw").glob("*.csv"))) == 10
    attempt = json.loads(
        (tmp_path / "macro" / "attempts" / result.attempt_id / "attempt.json").read_text(encoding="utf-8")
    )
    assert attempt["status"] == "promoted"
    assert attempt["max_concurrency"] == 1
    assert attempt["automatic_retry"] is False


def test_canary_timeout_stops_all_followup_calls(tmp_path: Path) -> None:
    dff_url = fred_series_url("DFF", ANALYSIS_DATE)
    session = FakeSession({dff_url: requests.ReadTimeout("canary timeout")})
    snapshot_path = tmp_path / "macro" / "market_regime_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b'{"status":"last-good"}')
    with pytest.raises(OfficialMacroSnapshotError, match="DFF canary failed") as caught:
        collect_official_macro_snapshot(tmp_path, session=session, now=ANALYSIS_TIME)  # type: ignore[arg-type]
    assert len(session.calls) == 1
    assert caught.value.details["requests_made"] == 1
    assert snapshot_path.read_bytes() == b'{"status":"last-good"}'
    assert not (tmp_path / "macro" / "raw").exists()


def test_canary_only_success_uses_one_request_and_never_promotes(tmp_path: Path) -> None:
    dff_url = fred_series_url("DFF", ANALYSIS_DATE)
    session = FakeSession({dff_url: FakeResponse(FRED_SERIES_FIXTURES["DFF"], url=dff_url)})
    result = collect_official_macro_snapshot(
        tmp_path,
        session=session,  # type: ignore[arg-type]
        now=ANALYSIS_TIME,
        canary_only=True,
    )
    assert result.status == "canary_verified"
    assert result.requests_made == 1
    assert len(session.calls) == 1
    assert not (tmp_path / "macro" / "raw").exists()
    assert not (tmp_path / "macro" / "market_regime_snapshot.json").exists()


def test_stream_timeout_preserves_partial_bytes_and_hash(tmp_path: Path) -> None:
    dff_url = fred_series_url("DFF", ANALYSIS_DATE)
    response = FakeResponse(
        FRED_SERIES_FIXTURES["DFF"], url=dff_url, stream_error_after_chunks=1
    )
    session = FakeSession({dff_url: response})
    with pytest.raises(OfficialMacroSnapshotError, match="DFF canary failed"):
        collect_official_macro_snapshot(tmp_path, session=session, now=ANALYSIS_TIME)  # type: ignore[arg-type]
    attempt_path = tmp_path / "macro" / "attempts" / "20260909T000000000000Z"
    attempt = json.loads((attempt_path / "attempt.json").read_text(encoding="utf-8"))
    evidence = attempt["responses"]["fred_DFF"]
    partial = attempt_path / evidence["attempt_raw_path"]
    assert evidence["incomplete"] is True
    assert evidence["actual_bytes"] == len(partial.read_bytes()) > 0
    assert evidence["sha256"] == hashlib.sha256(partial.read_bytes()).hexdigest()


def test_vix_failure_can_still_promote_partial_snapshot_with_fred_and_qqq(tmp_path: Path) -> None:
    qqq_csv, qqq_manifest = _write_qqq_fixture(tmp_path)
    failures = {
        "cboe_vix": FakeResponse(b"temporarily unavailable", status_code=503, url=CBOE_VIX_DOWNLOAD_URL)
    }
    session = FakeSession(_responses(failures=failures))
    result = collect_official_macro_snapshot(
        tmp_path,
        session=session,  # type: ignore[arg-type]
        now=ANALYSIS_TIME,
        qqq_csv_path=qqq_csv,
        qqq_manifest_path=qqq_manifest,
    )
    snapshot = json.loads(Path(result.snapshot_path).read_text(encoding="utf-8"))
    assert snapshot["collection_status"] == "partial"
    assert snapshot["normalization_errors"]["cboe_vix"] == "HTTPError"
    assert all(value >= 0.60 for value in snapshot["coverage_gate"]["axes"].values())
    assert snapshot["operational_eligible"] is False


def test_one_fred_series_failure_can_promote_if_each_axis_stays_above_gate(tmp_path: Path) -> None:
    qqq_csv, qqq_manifest = _write_qqq_fixture(tmp_path)
    dgs2_url = fred_series_url("DGS2", ANALYSIS_DATE)
    failures = {"DGS2": FakeResponse(b"bad", status_code=503, url=dgs2_url)}
    session = FakeSession(_responses(failures=failures))
    result = collect_official_macro_snapshot(
        tmp_path,
        session=session,  # type: ignore[arg-type]
        now=ANALYSIS_TIME,
        qqq_csv_path=qqq_csv,
        qqq_manifest_path=qqq_manifest,
    )
    snapshot = json.loads(Path(result.snapshot_path).read_text(encoding="utf-8"))
    assert snapshot["collection_status"] == "partial"
    assert snapshot["coverage_gate"]["axes"]["economy"] >= 0.60
    metric_ids = {row["metric_id"] for row in snapshot["observations"]}
    assert "treasury_2y_yield_pct" not in metric_ids
    assert "treasury_2s10s_spread_pp" not in metric_ids
    assert "treasury_10y_yield_pct" in metric_ids


def test_two_fred_series_failures_can_promote_when_all_axes_remain_above_gate(tmp_path: Path) -> None:
    qqq_csv, qqq_manifest = _write_qqq_fixture(tmp_path)
    failures = {
        "DGS2": FakeResponse(
            b"bad", status_code=503, url=fred_series_url("DGS2", ANALYSIS_DATE)
        ),
        "BAMLH0A0HYM2": FakeResponse(
            b"bad",
            status_code=503,
            url=fred_series_url("BAMLH0A0HYM2", ANALYSIS_DATE),
        ),
    }
    session = FakeSession(_responses(failures=failures))
    result = collect_official_macro_snapshot(
        tmp_path,
        session=session,  # type: ignore[arg-type]
        now=ANALYSIS_TIME,
        qqq_csv_path=qqq_csv,
        qqq_manifest_path=qqq_manifest,
    )
    snapshot = json.loads(Path(result.snapshot_path).read_text(encoding="utf-8"))
    assert snapshot["collection_status"] == "partial"
    assert all(value >= 0.60 for value in snapshot["coverage_gate"]["axes"].values())
    assert snapshot["coverage_gate"]["axes"]["risk"] < 0.80


def test_axis_coverage_failure_preserves_last_good(tmp_path: Path) -> None:
    qqq_csv, qqq_manifest = _write_qqq_fixture(tmp_path)
    snapshot_path = tmp_path / "macro" / "market_regime_snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b'{"status":"last-good"}')
    failures = {
        "CPIAUCSL": FakeResponse(b"bad", status_code=503, url=fred_series_url("CPIAUCSL", ANALYSIS_DATE)),
        "PCEPI": FakeResponse(b"bad", status_code=503, url=fred_series_url("PCEPI", ANALYSIS_DATE)),
    }
    session = FakeSession(_responses(failures=failures))
    with pytest.raises(OfficialMacroSnapshotError, match="axis coverage"):
        collect_official_macro_snapshot(
            tmp_path,
            session=session,  # type: ignore[arg-type]
            now=ANALYSIS_TIME,
            qqq_csv_path=qqq_csv,
            qqq_manifest_path=qqq_manifest,
        )
    assert snapshot_path.read_bytes() == b'{"status":"last-good"}'
    assert not (tmp_path / "macro" / "raw").exists()


def test_spread_uses_latest_same_date_only() -> None:
    from stocklens.personal.macro_snapshot import normalize_partial_macro_series

    hashes = {series: hashlib.sha256(series.encode()).hexdigest() for series in FRED_SERIES}
    series = {
        "DGS2": [(date(2026, 9, 7), 3.0), (date(2026, 9, 8), 3.1)],
        "DGS10": [(date(2026, 9, 7), 4.0), (date(2026, 9, 9), 4.2)],
    }
    observations, errors = normalize_partial_macro_series(
        series, hashes, collected_at=ANALYSIS_TIME.isoformat()
    )
    spread = next(row for row in observations if row["metric_id"] == "treasury_2s10s_spread_pp")
    assert spread["as_of"] == "2026-09-07"
    assert spread["value"] == 1.0
    assert "DGS2_DGS10_SPREAD" not in errors


def test_redirect_outside_approved_domains_is_blocked_without_raw(tmp_path: Path) -> None:
    dff_url = fred_series_url("DFF", ANALYSIS_DATE)
    session = FakeSession(
        {dff_url: FakeResponse(FRED_SERIES_FIXTURES["DFF"], url="https://example.invalid/dff.csv")}
    )
    with pytest.raises(OfficialMacroSnapshotError, match="DFF canary failed"):
        collect_official_macro_snapshot(tmp_path, session=session, now=ANALYSIS_TIME)  # type: ignore[arg-type]
    attempt_path = tmp_path / "macro" / "attempts" / "20260909T000000000000Z"
    assert list(attempt_path.glob("*.bin")) == []


def test_old_last_good_snapshot_is_preserved_but_marked_stale(tmp_path: Path) -> None:
    observations = normalize_official_macro_responses(
        FRED_COMBINED, VIX, collected_at=ANALYSIS_TIME.isoformat()
    )
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({"observations": observations}), encoding="utf-8")
    loaded = load_market_regime_snapshot(
        snapshot_path, analysis_as_of="2027-01-15T00:00:00+00:00"
    )
    assert loaded["status"] == "proxy_low_confidence"
    assert loaded["accepted_observations"] == []
    assert all(reason.startswith("stale_") for reason in loaded["excluded_observations"].values())


def test_future_as_of_is_not_accepted_as_current_snapshot() -> None:
    observations = normalize_official_macro_responses(
        FRED_COMBINED, VIX, collected_at="2026-08-01T00:00:00+00:00"
    )
    regime = build_market_regime(
        observations, analysis_as_of="2026-08-01T00:00:00+00:00"
    )
    assert regime["overall_coverage"] < 0.60
    assert "future_as_of_blocked" in set(regime["excluded_observations"].values())
