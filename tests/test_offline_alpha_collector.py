from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from stocklens.personal import offline_alpha_collector as collector


def _daily(symbol: str) -> bytes:
    return json.dumps({
        "Meta Data": {"2. Symbol": symbol},
        "Time Series (Daily)": {
            "2026-10-13": {
                "1. open": "100", "2. high": "103", "3. low": "99",
                "4. close": "102", "5. volume": "1000",
            }
        },
    }).encode()


def _estimates(symbol: str, *, basis: str | None = "GAAP") -> bytes:
    value = {
        "symbol": symbol,
        "annualEarnings": [{
            "fiscalDateEnding": "2027-10-31",
            "numberAnalystsEstimatedEps": "25",
        }],
        "quarterlyEarnings": [],
    }
    if basis is not None:
        value["accountingBasis"] = basis
    return json.dumps(value).encode()


class FakeResponse:
    def __init__(
        self, body: bytes, *, url: str, content_type: str, status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.url = url
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self._body[:3]
        yield self._body[3:]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, overrides: dict[str, FakeResponse] | None = None) -> None:
        self.calls: list[dict] = []
        self.overrides = overrides or {}

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if "terms_of_service" in url:
            return self.overrides.get("terms") or FakeResponse(
                b"<html>terms</html>", url=url, content_type="text/html; charset=utf-8",
            )
        function = kwargs.get("params", {}).get("function")
        symbol = kwargs.get("params", {}).get("symbol")
        key = f"{symbol}-{function}"
        if key in self.overrides:
            return self.overrides[key]
        body = _daily(symbol) if function == "TIME_SERIES_DAILY" else _estimates(symbol)
        return FakeResponse(
            body,
            url="https://www.alphavantage.co/query?apikey=must-not-persist",
            content_type="application/json",
        )


class StepClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=250)
        return current


def _patch_private(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    private = tmp_path / "private_data"
    private.mkdir()
    monkeypatch.setattr(collector, "resolve_repository_private_data", lambda *args, **kwargs: private)
    return private


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, run_id: str = "fixture-run",
    session: FakeSession | None = None, clock: StepClock | None = None,
) -> tuple[Path, FakeSession, dict]:
    private = _patch_private(monkeypatch, tmp_path)
    client = session or FakeSession()
    report = collector.collect_approved_alpha_evidence(
        project_root=tmp_path,
        api_key="super-secret-key",
        run_id=run_id,
        personal_noncommercial_confirmed=True,
        max_calls=5,
        session=client,
        clock=clock or StepClock(),
    )
    return private, client, report


def test_exact_plan_contains_only_approved_five_requests() -> None:
    plan = collector.exact_request_plan()
    assert len(plan) == 5
    assert [(row["function"], row["ticker"]) for row in plan[1:]] == [
        ("TIME_SERIES_DAILY", "AVGO"),
        ("TIME_SERIES_DAILY", "NVDA"),
        ("EARNINGS_ESTIMATES", "AVGO"),
        ("EARNINGS_ESTIMATES", "NVDA"),
    ]
    assert all(row["host"] == "www.alphavantage.co" for row in plan)


def test_fixture_run_uses_immutable_run_directory_and_never_persists_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, session, report = _run(tmp_path, monkeypatch)
    assert report["calls_attempted"] == report["calls_completed"] == 5
    assert report["approved_actual_http_request_budget"] == 5
    assert report["automatic_retries"] == 0
    assert report["parser_attempted"] is report["decision_created"] is False
    assert len(session.calls) == 5
    run_root = private / "raw/alpha_vantage/fixture-run"
    assert (run_root / "collection-run.json").is_file()
    assert (run_root / "terms-of-service.response.raw").is_file()
    assert (run_root / "AVGO_TIME_SERIES_DAILY.response.raw").is_file()
    assert not (private / "raw/alpha_vantage/terms-of-service.html").exists()
    files = [path for path in private.rglob("*") if path.is_file()]
    combined = b"\n".join(path.read_bytes() for path in files)
    assert b"super-secret-key" not in combined
    assert b"must-not-persist" not in combined
    rights = json.loads(
        (private / "rights/alpha_vantage/fixture-run/provider-terms-manifest.json")
        .read_text(encoding="utf-8")
    )
    assert rights["review_result"] == "manual_review_required"
    assert rights["parser_candidate"] is False


def test_later_pit_run_never_overwrites_prior_run_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    first = collector.collect_approved_alpha_evidence(
        project_root=tmp_path, api_key="secret", run_id="pit-one",
        personal_noncommercial_confirmed=True, max_calls=5, session=FakeSession(), clock=StepClock(),
    )
    first_path = private / "raw/alpha_vantage/pit-one/AVGO_TIME_SERIES_DAILY.response.raw"
    first_hash = first_path.read_bytes()
    second = collector.collect_approved_alpha_evidence(
        project_root=tmp_path, api_key="secret", run_id="pit-two",
        personal_noncommercial_confirmed=True, max_calls=5, session=FakeSession(), clock=StepClock(),
    )
    assert first["run_id"] == "pit-one" and second["run_id"] == "pit-two"
    assert first_path.read_bytes() == first_hash
    assert (private / "raw/alpha_vantage/pit-two/AVGO_TIME_SERIES_DAILY.response.raw").is_file()


def test_each_request_uses_response_completion_as_available_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, _, _ = _run(tmp_path, monkeypatch, clock=StepClock())
    manifests = sorted((private / "raw/alpha_vantage/fixture-run").glob("*.manifest.json"))
    completed: list[str] = []
    for path in manifests:
        value = json.loads(path.read_text(encoding="utf-8"))
        timestamps = [
            datetime.fromisoformat(value[key].replace("Z", "+00:00"))
            for key in ("request_started_at", "headers_received_at", "response_completed_at")
        ]
        assert timestamps[0] <= timestamps[1] <= timestamps[2]
        assert value["available_at"] == value["response_completed_at"]
        assert value["timestamp_order_valid"] is True
        completed.append(value["response_completed_at"])
    assert len(set(completed)) == 5


@pytest.mark.parametrize("budget", [0, 4, 6, 25])
def test_any_call_budget_other_than_exact_five_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int,
) -> None:
    _patch_private(monkeypatch, tmp_path)
    session = FakeSession()
    with pytest.raises(collector.OfflineCollectorError, match="exactly five"):
        collector.collect_approved_alpha_evidence(
            project_root=tmp_path, api_key="secret", run_id=f"budget-{budget}",
            personal_noncommercial_confirmed=True, max_calls=budget, session=session,
        )
    assert session.calls == []


def test_all_output_collisions_are_checked_before_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    collision = private / "rights/alpha_vantage/collision/provider-terms-manifest.json"
    collision.parent.mkdir(parents=True)
    collision.write_text("{}", encoding="utf-8")
    session = FakeSession()
    with pytest.raises(collector.OfflineCollectorError, match="collision"):
        collector.collect_approved_alpha_evidence(
            project_root=tmp_path, api_key="secret", run_id="collision",
            personal_noncommercial_confirmed=True, max_calls=5, session=session,
        )
    assert session.calls == []


@pytest.mark.parametrize("second_run", ["same-run", "different-run"])
def test_global_exclusive_lock_blocks_same_and_different_concurrent_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second_run: str,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    session = FakeSession()
    with collector._ExclusiveCollectorLock(private, "same-run", "2026-10-16T08:00:00Z"):
        with pytest.raises(collector.OfflineCollectorError, match="already active"):
            collector.collect_approved_alpha_evidence(
                project_root=tmp_path, api_key="secret", run_id=second_run,
                personal_noncommercial_confirmed=True, max_calls=5, session=session,
            )
    assert session.calls == []


class InterruptedResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        del chunk_size
        yield b"partial"
        raise requests.exceptions.ConnectionError("secret network detail")


class KeyboardInterruptedResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        del chunk_size
        yield b"partial-before-interrupt"
        raise KeyboardInterrupt


class UnexpectedSession(FakeSession):
    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        raise RuntimeError("sensitive unexpected detail")


class DiskFailureResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        del chunk_size
        yield b"partial-before-disk-error"
        raise OSError("sensitive disk detail")


@pytest.mark.parametrize(
    "response,raises,expected_state",
    [
        (InterruptedResponse(b"", url=collector.TERMS_URL, content_type="text/html"), None, "failed"),
        (KeyboardInterruptedResponse(b"", url=collector.TERMS_URL, content_type="text/html"), KeyboardInterrupt, "interrupted"),
    ],
)
def test_attempt_ledger_and_partial_bytes_survive_failure_or_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse, raises: type[BaseException] | None, expected_state: str,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    session = FakeSession({"terms": response})
    kwargs = dict(
        project_root=tmp_path, api_key="secret", run_id="interrupted",
        personal_noncommercial_confirmed=True, max_calls=5, session=session, clock=StepClock(),
    )
    if raises:
        with pytest.raises(raises):
            collector.collect_approved_alpha_evidence(**kwargs)
    else:
        collector.collect_approved_alpha_evidence(**kwargs)
    run_root = private / "raw/alpha_vantage/interrupted"
    ledger = json.loads((run_root / "collection-run.json").read_text(encoding="utf-8"))
    assert ledger["calls_attempted"] == 1
    assert ledger["calls_completed"] == 0
    assert ledger["run_state"] == expected_state
    assert (run_root / "terms-of-service.response.raw.partial").read_bytes().startswith(b"partial")
    combined = b"\n".join(path.read_bytes() for path in run_root.rglob("*") if path.is_file())
    assert b"secret network detail" not in combined


@pytest.mark.parametrize(
    ("session", "failure_code"),
    [
        (UnexpectedSession(), "unexpected_error"),
        (FakeSession({"terms": DiskFailureResponse(b"", url=collector.TERMS_URL, content_type="text/html")}),
         "local_io_error"),
    ],
)
def test_unexpected_and_disk_failures_are_redacted_in_persistent_run_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: FakeSession, failure_code: str,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    collector.collect_approved_alpha_evidence(
        project_root=tmp_path, api_key="secret", run_id="fault-ledger",
        personal_noncommercial_confirmed=True, max_calls=5, session=session, clock=StepClock(),
    )
    run_root = private / "raw/alpha_vantage/fault-ledger"
    capture = json.loads(
        (run_root / "terms-of-service.response.raw.manifest.json").read_text(encoding="utf-8")
    )
    ledger = json.loads((run_root / "collection-run.json").read_text(encoding="utf-8"))
    assert capture["failure_code"] == failure_code
    assert ledger["calls_attempted"] == 1
    assert ledger["calls_completed"] == 0
    persisted = b"\n".join(path.read_bytes() for path in run_root.rglob("*") if path.is_file())
    assert b"sensitive unexpected detail" not in persisted
    assert b"sensitive disk detail" not in persisted


@pytest.mark.parametrize("envelope", [{"Note": "limit"}, {"Information": "info"}, {"Error Message": "bad"}])
def test_http_200_provider_error_envelopes_are_not_valid_datasets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, envelope: dict,
) -> None:
    response = FakeResponse(
        json.dumps(envelope).encode(),
        url="https://www.alphavantage.co/query",
        content_type="application/json",
    )
    private, _, report = _run(
        tmp_path, monkeypatch, session=FakeSession({"AVGO-TIME_SERIES_DAILY": response}),
    )
    manifest = json.loads(
        (private / "raw/alpha_vantage/fixture-run/AVGO_TIME_SERIES_DAILY.response.raw.manifest.json")
        .read_text(encoding="utf-8")
    )
    assert manifest["valid_provider_dataset"] is False
    assert manifest["failure_code"] == "provider_error_envelope"
    assert report["run_state"] == "failed"


def test_empty_daily_series_is_not_valid_provider_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        json.dumps({"Meta Data": {"2. Symbol": "AVGO"}, "Time Series (Daily)": {}}).encode(),
        url="https://www.alphavantage.co/query", content_type="application/json",
    )
    private, _, _ = _run(
        tmp_path, monkeypatch, session=FakeSession({"AVGO-TIME_SERIES_DAILY": response}),
    )
    value = json.loads(
        (private / "raw/alpha_vantage/fixture-run/AVGO_TIME_SERIES_DAILY.response.raw.manifest.json")
        .read_text(encoding="utf-8")
    )
    assert value["valid_provider_dataset"] is False
    assert value["validation_code"] == "daily_structure_missing"


def test_estimate_basis_absence_is_recorded_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        _estimates("AVGO", basis=None), url="https://www.alphavantage.co/query",
        content_type="application/json",
    )
    private, _, _ = _run(
        tmp_path, monkeypatch, session=FakeSession({"AVGO-EARNINGS_ESTIMATES": response}),
    )
    value = json.loads(
        (private / "raw/alpha_vantage/fixture-run/AVGO_EARNINGS_ESTIMATES.response.raw.manifest.json")
        .read_text(encoding="utf-8")
    )
    assert value["valid_provider_dataset"] is True
    assert value["accounting_basis"] == "unknown"
    assert value["parser_candidate_basis_eligible"] is False


def test_terms_wrong_content_type_fails_fast_before_data_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(b"not terms", url=collector.TERMS_URL, content_type="text/plain")
    private, session, report = _run(tmp_path, monkeypatch, session=FakeSession({"terms": response}))
    assert len(session.calls) == 1
    assert report["calls_attempted"] == report["calls_completed"] == 1
    assert report["failure_code"] == "terms_validation_failed"
    assert not (private / "rights/alpha_vantage/fixture-run/provider-terms-manifest.json").exists()


def test_redirect_is_not_automatically_followed_and_external_location_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        b"", url=collector.TERMS_URL, content_type="text/html", status=302,
        headers={"Location": "https://attacker.example/steal"},
    )
    _, session, report = _run(tmp_path, monkeypatch, session=FakeSession({"terms": response}))
    assert len(session.calls) == 1
    assert session.calls[0]["allow_redirects"] is False
    assert report["calls_attempted"] == 1
    assert report["calls_completed"] == 0
    assert report["failure_code"] == "terms_validation_failed"


def test_allowed_redirect_counts_against_actual_five_request_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RedirectSession(FakeSession):
        def get(self, url: str, **kwargs):
            self.calls.append({"url": url, **kwargs})
            if "terms_of_service" in url:
                return FakeResponse(
                    b"", url=url, content_type="text/html", status=302,
                    headers={"Location": "/terms_final.pdf"},
                )
            if "terms_final.pdf" in url:
                return FakeResponse(b"%PDF fixture", url=url, content_type="application/pdf")
            function = kwargs.get("params", {}).get("function")
            symbol = kwargs.get("params", {}).get("symbol")
            body = _daily(symbol) if function == "TIME_SERIES_DAILY" else _estimates(symbol)
            return FakeResponse(body, url=url, content_type="application/json")

    _, session, report = _run(tmp_path, monkeypatch, session=RedirectSession())
    assert len(session.calls) == 5
    assert report["calls_attempted"] == 5
    assert report["calls_completed"] == 5
    assert report["run_state"] == "failed"
    assert any(row["failure_code"] == "collector_policy_error" for row in report["results"])


def test_confirmation_and_api_key_are_required_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_private(monkeypatch, tmp_path)
    for key, confirmed, message in (("secret", False, "must be confirmed"), ("", True, "before any request")):
        session = FakeSession()
        with pytest.raises(collector.OfflineCollectorError, match=message):
            collector.collect_approved_alpha_evidence(
                project_root=tmp_path, api_key=key, run_id=f"blocked-{confirmed}",
                personal_noncommercial_confirmed=confirmed, max_calls=5, session=session,
            )
        assert session.calls == []
