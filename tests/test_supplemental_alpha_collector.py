from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from stocklens.personal import supplemental_alpha_collector as collector


def _pdf_page(writer: PdfWriter, lines: list[str]) -> None:
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
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream.set_data(" ".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = stream


def _terms_pdf() -> bytes:
    writer = PdfWriter()
    _pdf_page(writer, [
        "TERMS OF SERVICE", "Alpha Vantage", "2. Grant of License.",
        "For personal, non-commercial use.",
    ])
    _pdf_page(writer, ["Alpha Vantage", "4. Use Restrictions.", "15. Assignment."])
    _pdf_page(writer, ["Alpha Vantage", "7. Termination."])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _daily() -> bytes:
    return json.dumps({
        "Meta Data": {"2. Symbol": "NVDA"},
        "Time Series (Daily)": {
            "2026-10-16": {
                "1. open": "200", "2. high": "205", "3. low": "198",
                "4. close": "203", "5. volume": "1000",
            }
        },
    }).encode()


def _estimates(ticker: str) -> bytes:
    return json.dumps({
        "symbol": ticker,
        "accountingBasis": "GAAP",
        "annualEarnings": [{
            "fiscalDateEnding": "2027-10-31",
            "numberAnalystsEstimatedEps": "20",
        }],
        "quarterlyEarnings": [],
    }).encode()


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://www.alphavantage.co/query",
        status: int = 200,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.url = url
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self.body

    def close(self) -> None:
        return None


class PartialFailureResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        del chunk_size
        yield b"partial-before-connection-loss"
        raise requests.exceptions.ConnectionError("fixture partial response interrupted")


class FakeSession:
    def __init__(self, first_response: bytes | None = None) -> None:
        self.calls: list[dict] = []
        self.first_response = first_response

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        params = kwargs["params"]
        if len(self.calls) == 1 and self.first_response is not None:
            return FakeResponse(self.first_response)
        if params["function"] == "TIME_SERIES_DAILY":
            return FakeResponse(_daily())
        return FakeResponse(_estimates(params["symbol"]))


class SequenceSession:
    def __init__(self, outcomes: list[FakeResponse | BaseException]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class StepClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 17, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=100)
        return current


def _private_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    private = tmp_path / "private_data"
    terms = private / collector.BASELINE_TERMS_RELATIVE_PATH
    terms.parent.mkdir(parents=True)
    payload = _terms_pdf()
    terms.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest().upper()
    capture = {
        "request_id": "alpha-vantage-terms",
        "response_sha256": digest,
        "content_type": "application/pdf",
        "final_url": "https://www.alphavantage.co/terms_of_service/",
        "request_query_persisted": False,
        "api_key_persisted": False,
    }
    terms.with_suffix(terms.suffix + ".manifest.json").write_text(
        json.dumps(capture), encoding="utf-8",
    )
    monkeypatch.setattr(collector, "resolve_repository_private_data", lambda *args, **kwargs: private)
    monkeypatch.setattr(collector, "BASELINE_TERMS_SHA256", digest)
    return private, digest


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: FakeSession | SequenceSession | None = None,
) -> tuple[Path, FakeSession | SequenceSession, list[float], dict]:
    private, digest = _private_fixture(tmp_path, monkeypatch)
    client = session or FakeSession()
    sleeps: list[float] = []
    report = collector.collect_alpha_supplemental_evidence(
        project_root=tmp_path,
        api_key="secret-not-persisted",
        run_id="p5-fixture-run",
        approval_reference="independent-qa-future-approval",
        personal_noncommercial_confirmed=True,
        current_provider_limit_review_confirmed=True,
        max_calls=3,
        request_interval_seconds=60,
        expected_terms_sha256=digest,
        session=client,
        clock=StepClock(),
        sleeper=sleeps.append,
    )
    return private, client, sleeps, report


def test_exact_supplemental_plan_has_only_three_missing_requests() -> None:
    plan = collector.exact_supplemental_request_plan()
    assert [(row["function"], row["ticker"]) for row in plan] == [
        ("TIME_SERIES_DAILY", "NVDA"),
        ("EARNINGS_ESTIMATES", "AVGO"),
        ("EARNINGS_ESTIMATES", "NVDA"),
    ]
    assert all(row["host"] == "www.alphavantage.co" and row["path"] == "/query" for row in plan)
    assert all(row["secret_parameter_names"] == ["apikey"] for row in plan)


def test_supplemental_fixture_is_sequential_hash_linked_and_never_promotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, session, sleeps, report = _run(tmp_path, monkeypatch)
    assert collector.supplemental_report_is_complete(report) is True
    assert report["calls_attempted"] == report["calls_completed"] == 3
    assert report["concurrency"] == 1
    assert report["automatic_retries"] == 0
    assert sleeps == [60.0, 60.0]
    assert len(session.calls) == 3
    assert not any("terms" in row["url"] for row in session.calls)
    assert not any(row["params"].get("symbol") == "AVGO" and row["params"]["function"] == "TIME_SERIES_DAILY" for row in session.calls)
    run_root = private / "raw/alpha_vantage/p5-fixture-run"
    assert (run_root / "supplemental-collection-run.json").is_file()
    assert not (run_root / "terms-of-service.response.raw").exists()
    assert report["baseline_terms"]["source_sha256"] == collector.BASELINE_TERMS_SHA256
    assert report["parser_attempted"] is report["candidate_created"] is False
    assert report["snapshot_created"] is report["decision_created"] is False
    assert report["operational_eligible"] is False
    manifests = list(run_root.glob("*.manifest.json"))
    assert manifests
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["collector_version"]
        == "alpha-vantage-offline-evidence-v2"
        for path in manifests
    )
    combined = b"\n".join(path.read_bytes() for path in private.rglob("*") if path.is_file())
    assert b"secret-not-persisted" not in combined


def test_provider_restriction_envelope_stops_after_first_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = json.dumps({
        "Information": "The API call frequency or daily request limit has been reached.",
    }).encode()
    session = FakeSession(first_response=envelope)
    _, session, sleeps, report = _run(tmp_path, monkeypatch, session=session)
    assert report["run_state"] == "failed"
    assert report["failure_code"] == "provider_rate_or_plan_limit"
    assert report["calls_attempted"] == report["calls_completed"] == 1
    assert len(session.calls) == 1
    assert sleeps == []
    assert collector.supplemental_report_is_complete(report) is False


@pytest.mark.parametrize(
    ("outcome", "failure_code"),
    [
        (FakeResponse(_daily(), status=500), "http_500"),
        (requests.exceptions.Timeout("fixture timeout"), "timeout"),
        (requests.exceptions.ConnectionError("fixture connection error"), "connection_error"),
        (FakeResponse(b"not-json"), "invalid_json_body"),
        (FakeResponse(_daily(), content_type="text/plain"), "invalid_json_content_type"),
        (
            FakeResponse(json.dumps({
                "Meta Data": {"2. Symbol": "AVGO"},
                "Time Series (Daily)": {
                    "2026-10-16": {
                        "1. open": "200", "2. high": "205", "3. low": "198",
                        "4. close": "203", "5. volume": "1000",
                    }
                },
            }).encode()),
            "ticker_mismatch",
        ),
    ],
)
def test_first_request_failure_stops_before_sleep_or_second_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: FakeResponse | BaseException,
    failure_code: str,
) -> None:
    session = SequenceSession([outcome])
    _, session, sleeps, report = _run(tmp_path, monkeypatch, session=session)
    assert report["run_state"] == "failed"
    assert report["failure_code"] == failure_code
    assert report["calls_attempted"] == 1
    assert len(session.calls) == 1
    assert sleeps == []
    assert collector.supplemental_report_is_complete(report) is False


def test_second_request_failure_stops_before_second_sleep_and_third_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SequenceSession([
        FakeResponse(_daily()),
        FakeResponse(b"not-json"),
    ])
    _, session, sleeps, report = _run(tmp_path, monkeypatch, session=session)
    assert report["run_state"] == "failed"
    assert report["failure_code"] == "invalid_json_body"
    assert report["calls_attempted"] == report["calls_completed"] == 2
    assert len(session.calls) == 2
    assert sleeps == [60.0]
    assert collector.supplemental_report_is_complete(report) is False


def test_partial_first_response_is_preserved_and_stops_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SequenceSession([PartialFailureResponse(b"")])
    private, session, sleeps, report = _run(tmp_path, monkeypatch, session=session)
    assert report["run_state"] == "failed"
    assert report["failure_code"] == "connection_error"
    assert report["calls_attempted"] == 1
    assert report["calls_completed"] == 0
    assert len(session.calls) == 1
    assert sleeps == []
    partial = (
        private / "raw/alpha_vantage/p5-fixture-run"
        / "NVDA_TIME_SERIES_DAILY.response.raw.partial"
    )
    assert partial.read_bytes() == b"partial-before-connection-loss"


def test_redirect_budget_exhaustion_stops_the_first_logical_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    redirect = FakeResponse(
        b"",
        status=302,
        headers={"Location": "https://www.alphavantage.co/query"},
    )
    session = SequenceSession([redirect, redirect, redirect])
    _, session, sleeps, report = _run(tmp_path, monkeypatch, session=session)
    assert report["run_state"] == "failed"
    assert report["failure_code"] == "collector_policy_error"
    assert report["calls_attempted"] == report["calls_completed"] == 3
    assert len(session.calls) == 3
    assert sleeps == []
    assert collector.supplemental_report_is_complete(report) is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_calls": 2}, "exactly three"),
        ({"request_interval_seconds": 59}, "exactly 60"),
        ({"current_provider_limit_review_confirmed": False}, "limit review"),
        ({"approval_reference": ""}, "approval reference"),
    ],
)
def test_supplemental_preflight_fails_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict,
    message: str,
) -> None:
    _, digest = _private_fixture(tmp_path, monkeypatch)
    session = FakeSession()
    kwargs = {
        "project_root": tmp_path,
        "api_key": "secret",
        "run_id": "blocked-run",
        "approval_reference": "future-approval",
        "personal_noncommercial_confirmed": True,
        "current_provider_limit_review_confirmed": True,
        "max_calls": 3,
        "request_interval_seconds": 60,
        "expected_terms_sha256": digest,
        "session": session,
        "clock": StepClock(),
        "sleeper": lambda _: None,
    }
    kwargs.update(overrides)
    with pytest.raises(collector.OfflineCollectorError, match=message):
        collector.collect_alpha_supplemental_evidence(**kwargs)
    assert session.calls == []


def test_baseline_terms_hash_mismatch_blocks_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _private_fixture(tmp_path, monkeypatch)
    session = FakeSession()
    with pytest.raises(collector.OfflineCollectorError, match="terms SHA-256 mismatch"):
        collector.collect_alpha_supplemental_evidence(
            project_root=tmp_path,
            api_key="secret",
            run_id="blocked-run",
            approval_reference="future-approval",
            personal_noncommercial_confirmed=True,
            current_provider_limit_review_confirmed=True,
            expected_terms_sha256="0" * 64,
            session=session,
        )
    assert session.calls == []
