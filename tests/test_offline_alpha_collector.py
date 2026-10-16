from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from stocklens.personal import offline_alpha_collector as collector


class FakeResponse:
    def __init__(self, body: bytes, *, url: str, content_type: str, status: int = 200) -> None:
        self._body = body
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self._body[:3]
        yield self._body[3:]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if "terms_of_service" in url:
            return FakeResponse(b"<html>terms</html>", url=url, content_type="text/html")
        function = kwargs["params"]["function"]
        symbol = kwargs["params"]["symbol"]
        return FakeResponse(
            json.dumps({"function": function, "symbol": symbol}).encode(),
            url="https://www.alphavantage.co/query?apikey=must-not-persist",
            content_type="application/json",
        )


def _patch_private(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    private = tmp_path / "private_data"
    private.mkdir()
    monkeypatch.setattr(collector, "resolve_repository_private_data", lambda *args, **kwargs: private)
    return private


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


def test_fixture_run_preserves_raw_bytes_and_never_persists_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    session = FakeSession()
    report = collector.collect_approved_alpha_evidence(
        project_root=tmp_path,
        api_key="super-secret-key",
        run_id="fixture-run",
        personal_noncommercial_confirmed=True,
        max_calls=5,
        session=session,
        now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc),
    )
    assert report["calls_made"] == 5
    assert report["automatic_retries"] == 0
    assert report["parser_attempted"] is False
    assert report["decision_created"] is False
    assert len(session.calls) == 5
    files = [path for path in private.rglob("*") if path.is_file()]
    combined = b"\n".join(path.read_bytes() for path in files)
    assert b"super-secret-key" not in combined
    assert b"must-not-persist" not in combined
    assert (private / "raw/alpha_vantage/AVGO_TIME_SERIES_DAILY.response.raw").is_file()
    capture = json.loads(
        (private / "raw/alpha_vantage/AVGO_TIME_SERIES_DAILY.response.raw.manifest.json")
        .read_text(encoding="utf-8")
    )
    assert capture["http_status"] == 200
    assert capture["final_url"] == "https://www.alphavantage.co/query"
    assert capture["byte_length"] > 0
    assert len(capture["response_sha256"]) == 64
    assert capture["request_query_persisted"] is False


def test_duplicate_run_and_existing_targets_are_blocked_without_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    session = FakeSession()
    run_path = private / "raw/alpha_vantage/collection-run-duplicate.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text("{}", encoding="utf-8")
    with pytest.raises(collector.OfflineCollectorError, match="duplicate"):
        collector.collect_approved_alpha_evidence(
            project_root=tmp_path, api_key="secret", run_id="duplicate",
            personal_noncommercial_confirmed=True, max_calls=5, session=session,
        )
    assert session.calls == []


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


def test_personal_noncommercial_confirmation_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_private(monkeypatch, tmp_path)
    with pytest.raises(collector.OfflineCollectorError, match="must be confirmed"):
        collector.collect_approved_alpha_evidence(
            project_root=tmp_path, api_key="secret", run_id="unconfirmed",
            personal_noncommercial_confirmed=False, max_calls=5, session=FakeSession(),
        )


def test_missing_api_key_blocks_before_terms_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_private(monkeypatch, tmp_path)
    session = FakeSession()
    with pytest.raises(collector.OfflineCollectorError, match="before any request"):
        collector.collect_approved_alpha_evidence(
            project_root=tmp_path, api_key="", run_id="missing-key",
            personal_noncommercial_confirmed=True, max_calls=5, session=session,
        )
    assert session.calls == []


class InterruptedResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        del chunk_size
        yield b"partial"
        raise requests.exceptions.ConnectionError("secret network detail")


class InterruptedSession(FakeSession):
    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return InterruptedResponse(b"", url=url, content_type="text/html")


class KeyboardInterruptedResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        del chunk_size
        yield b"partial-before-interrupt"
        raise KeyboardInterrupt


class KeyboardInterruptedSession(FakeSession):
    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return KeyboardInterruptedResponse(b"", url=url, content_type="text/html")


def test_failed_stream_keeps_partial_bytes_and_redacted_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    session = InterruptedSession()
    report = collector.collect_approved_alpha_evidence(
        project_root=tmp_path, api_key="secret", run_id="partial",
        personal_noncommercial_confirmed=True, max_calls=5, session=session,
    )
    assert report["calls_made"] == 5
    partial = private / "raw/alpha_vantage/terms-of-service.html.partial"
    manifest = private / "raw/alpha_vantage/terms-of-service.html.manifest.json"
    assert partial.read_bytes() == b"partial"
    text = manifest.read_text(encoding="utf-8")
    assert "secret network detail" not in text
    assert json.loads(text)["failure_code"] == "connection_error"


def test_keyboard_interrupt_keeps_partial_bytes_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = _patch_private(monkeypatch, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        collector.collect_approved_alpha_evidence(
            project_root=tmp_path, api_key="secret", run_id="keyboard-interrupt",
            personal_noncommercial_confirmed=True, max_calls=5,
            session=KeyboardInterruptedSession(),
        )
    partial = private / "raw/alpha_vantage/terms-of-service.html.partial"
    manifest = private / "raw/alpha_vantage/terms-of-service.html.manifest.json"
    assert partial.read_bytes() == b"partial-before-interrupt"
    assert json.loads(manifest.read_text(encoding="utf-8"))["failure_code"] == "interrupted"
