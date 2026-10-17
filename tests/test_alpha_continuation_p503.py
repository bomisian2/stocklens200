from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

from scripts import collect_alpha_continuation as cli
from stocklens.personal import alpha_continuation_collector as collector
from stocklens.personal.alpha_evidence_validation import validate_dataset_bytes


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _estimate_row(
    *, date: str = "2027-01-31", horizon: str = "fiscal quarter",
) -> dict[str, Any]:
    return {
        "date": date,
        "horizon": horizon,
        "eps_estimate_average": "1.50",
        "eps_estimate_high": "1.80",
        "eps_estimate_low": "1.20",
        "eps_estimate_analyst_count": "30.0000",
        "revenue_estimate_average": "40000000000",
        "revenue_estimate_high": "42000000000",
        "revenue_estimate_low": "38000000000",
        "revenue_estimate_analyst_count": "28.0000",
        "eps_estimate_average_7_days_ago": None,
        "eps_estimate_average_30_days_ago": "1.45",
        "eps_estimate_average_60_days_ago": "1.40",
        "eps_estimate_average_90_days_ago": "1.35",
        "eps_estimate_revision_up_trailing_7_days": "2.0000",
        "eps_estimate_revision_down_trailing_7_days": "0.0000",
        "eps_estimate_revision_up_trailing_30_days": "5.0000",
        "eps_estimate_revision_down_trailing_30_days": "1.0000",
    }


def _estimates_payload(ticker: str = "NVDA") -> bytes:
    return json.dumps({"symbol": ticker, "estimates": [_estimate_row()]}).encode()


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status_code = status
        self.url = "https://www.alphavantage.co/query"
        self.headers = {"Content-Type": content_type, **(headers or {})}

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self.body

    def close(self) -> None:
        return None


class FakeSession:
    def __init__(self, outcome: FakeResponse | BaseException | None = None) -> None:
        self.outcome = outcome or FakeResponse(_estimates_payload())
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class StepClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=100)
        return current


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _predecessor_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_nvda_estimates: bool = False,
) -> tuple[Path, dict[str, str]]:
    private = tmp_path / "private_data"
    run_root = private / "raw" / "alpha_vantage" / collector.PREVIOUS_RUN_ID
    run_root.mkdir(parents=True)
    avgo_raw = private / collector.AVGO_RAW_RELATIVE_PATH
    avgo_raw.write_bytes(b'{"symbol":"AVGO","estimates":[]}')
    avgo_hash = _sha(avgo_raw)
    avgo_manifest = private / collector.AVGO_MANIFEST_RELATIVE_PATH
    _write_json(avgo_manifest, {
        "request_id": "avgo-estimates",
        "function": "EARNINGS_ESTIMATES",
        "ticker": "AVGO",
        "response_sha256": avgo_hash,
        "api_key_persisted": False,
        "request_query_persisted": False,
    })
    avgo_manifest_hash = _sha(avgo_manifest)
    result_ids = ["nvda-daily", "avgo-estimates"]
    if include_nvda_estimates:
        result_ids.append("nvda-estimates")
    ledger = private / collector.PREVIOUS_LEDGER_RELATIVE_PATH
    _write_json(ledger, {
        "run_id": collector.PREVIOUS_RUN_ID,
        "run_state": "failed",
        "failure_code": "estimate_periods_missing",
        "calls_attempted": 2,
        "calls_completed": 2,
        "automatic_retries": 0,
        "results": [{"request_id": request_id} for request_id in result_ids],
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    })
    ledger_hash = _sha(ledger)
    adjudication = private / collector.P502_ADJUDICATION_RELATIVE_PATH
    _write_json(adjudication, {
        "adjudication_version": "alpha-vantage-supplemental-adjudication-p5.0.2-v1",
        "run_id": collector.PREVIOUS_RUN_ID,
        "collection_run": {"source_hash": ledger_hash},
        "requests": [{
            "request_id": "avgo-estimates",
            "source_hash": avgo_hash,
            "capture_manifest_hash": avgo_manifest_hash,
        }],
        "historical_availability_verified": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    })
    hashes = {
        "expected_ledger_sha256": ledger_hash,
        "expected_avgo_raw_sha256": avgo_hash,
        "expected_avgo_manifest_sha256": avgo_manifest_hash,
        "expected_adjudication_sha256": _sha(adjudication),
    }
    monkeypatch.setattr(
        collector, "resolve_repository_private_data", lambda *args, **kwargs: private,
    )
    return private, hashes


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: FakeSession | None = None,
    include_nvda_estimates: bool = False,
    run_id: str = "p503-fixture-run",
) -> tuple[Path, FakeSession, dict[str, str], dict[str, Any]]:
    private, hashes = _predecessor_fixture(
        tmp_path, monkeypatch, include_nvda_estimates=include_nvda_estimates,
    )
    client = session or FakeSession()
    report = collector.collect_alpha_continuation_evidence(
        project_root=tmp_path,
        api_key="secret-not-persisted",
        run_id=run_id,
        approval_reference="future-independent-qa-p5.0.3-approval",
        personal_noncommercial_confirmed=True,
        current_provider_limit_review_confirmed=True,
        max_calls=1,
        session=client,
        clock=StepClock(),
        **hashes,
    )
    return private, client, hashes, report


def test_plan_contains_only_nvda_earnings_estimates() -> None:
    assert collector.exact_continuation_request_plan() == [{
        "position": 1,
        "request_id": "nvda-estimates",
        "provider": "Alpha Vantage",
        "scheme": "https",
        "host": "www.alphavantage.co",
        "path": "/query",
        "function": "EARNINGS_ESTIMATES",
        "ticker": "NVDA",
        "fixed_parameters": {},
        "secret_parameter_names": ["apikey"],
    }]


def test_show_plan_makes_no_call(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["collect_alpha_continuation.py", "--show-plan"])
    monkeypatch.setattr(
        cli, "collect_alpha_continuation_evidence",
        lambda **kwargs: pytest.fail("show-plan attempted collection"),
    )
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert '"maximum_actual_http_requests": 1' in output
    assert '"request_id": "nvda-estimates"' in output
    assert '"execution_approved": false' in output


def test_exact_one_success_is_hash_linked_and_never_promotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, session, hashes, report = _run(tmp_path, monkeypatch)
    assert collector.continuation_report_is_complete(report) is True
    assert report["calls_attempted"] == report["calls_completed"] == 1
    assert report["automatic_retries"] == 0
    assert len(session.calls) == 1
    assert session.calls[0]["params"]["function"] == "EARNINGS_ESTIMATES"
    assert session.calls[0]["params"]["symbol"] == "NVDA"
    assert session.calls[0]["allow_redirects"] is False
    assert report["predecessor_evidence"]["supplemental_ledger"]["source_sha256"] == hashes["expected_ledger_sha256"]
    result = report["results"][0]
    assert result["accounting_basis"] == "unknown"
    assert result["historical_availability_verified"] is False
    assert result["historical_record_classification"] == "current_snapshot_records_for_past_periods"
    assert result["fy1_ntm_mapping_eligible"] is False
    assert result["parser_candidate"] is False
    assert result["snapshot_created"] is False
    assert result["decision_created"] is False
    assert result["operational_eligible"] is False
    run_root = private / "raw" / "alpha_vantage" / "p503-fixture-run"
    raw = run_root / "NVDA_EARNINGS_ESTIMATES.response.raw"
    manifest = raw.with_suffix(raw.suffix + ".manifest.json")
    ledger = run_root / "continuation-collection-run.json"
    assert raw.is_file() and manifest.is_file() and ledger.is_file()
    manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
    ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    assert manifest_value["response_sha256"] == _sha(raw)
    assert ledger_value["predecessor_evidence"] == report["predecessor_evidence"]
    combined = b"\n".join(path.read_bytes() for path in private.rglob("*") if path.is_file())
    assert b"secret-not-persisted" not in combined
    assert b"apikey=" not in combined.lower()


@pytest.mark.parametrize(
    ("outcome", "failure_code"),
    [
        (requests.exceptions.Timeout("fixture timeout"), "timeout"),
        (requests.exceptions.ConnectionError("fixture connection"), "connection_error"),
        (FakeResponse(_estimates_payload(), status=500), "http_500"),
        (
            FakeResponse(json.dumps({"Information": "fixture restriction"}).encode()),
            "provider_restriction_unknown",
        ),
    ],
)
def test_failures_stop_after_exactly_one_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: FakeResponse | BaseException,
    failure_code: str,
) -> None:
    session = FakeSession(outcome)
    _, session, _, report = _run(tmp_path, monkeypatch, session=session)
    assert report["run_state"] == "failed"
    assert report["failure_code"] == failure_code
    assert report["calls_attempted"] == 1
    assert len(session.calls) == 1
    assert collector.continuation_report_is_complete(report) is False


def test_redirect_is_fail_closed_without_second_http_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(FakeResponse(
        b"", status=302,
        headers={"Location": "https://www.alphavantage.co/query"},
    ))
    _, session, _, report = _run(tmp_path, monkeypatch, session=session)
    assert len(session.calls) == 1
    assert report["calls_attempted"] == report["calls_completed"] == 1
    assert report["run_state"] == "failed"
    assert report["failure_code"] == "collector_policy_error"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"api_key": ""}, "API_KEY"),
        ({"max_calls": 0}, "exactly one"),
        ({"max_calls": 2}, "exactly one"),
        ({"approval_reference": ""}, "approval reference"),
    ],
)
def test_policy_preflight_blocks_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    message: str,
) -> None:
    _, hashes = _predecessor_fixture(tmp_path, monkeypatch)
    session = FakeSession()
    kwargs: dict[str, Any] = {
        "project_root": tmp_path,
        "api_key": "secret",
        "run_id": "blocked-run",
        "approval_reference": "future-approval",
        "personal_noncommercial_confirmed": True,
        "current_provider_limit_review_confirmed": True,
        "max_calls": 1,
        "session": session,
        "clock": StepClock(),
        **hashes,
    }
    kwargs.update(overrides)
    with pytest.raises(collector.OfflineCollectorError, match=message):
        collector.collect_alpha_continuation_evidence(**kwargs)
    assert session.calls == []


def test_predecessor_hash_mismatch_blocks_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, hashes = _predecessor_fixture(tmp_path, monkeypatch)
    session = FakeSession()
    hashes["expected_ledger_sha256"] = "0" * 64
    with pytest.raises(collector.OfflineCollectorError, match="ledger SHA-256 mismatch"):
        collector.collect_alpha_continuation_evidence(
            project_root=tmp_path, api_key="secret", run_id="blocked-run",
            approval_reference="future-approval",
            personal_noncommercial_confirmed=True,
            current_provider_limit_review_confirmed=True,
            max_calls=1, session=session, clock=StepClock(), **hashes,
        )
    assert session.calls == []


def test_existing_nvda_result_blocks_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, hashes = _predecessor_fixture(
        tmp_path, monkeypatch, include_nvda_estimates=True,
    )
    session = FakeSession()
    with pytest.raises(collector.OfflineCollectorError, match="request history mismatch"):
        collector.collect_alpha_continuation_evidence(
            project_root=tmp_path, api_key="secret", run_id="blocked-run",
            approval_reference="future-approval",
            personal_noncommercial_confirmed=True,
            current_provider_limit_review_confirmed=True,
            max_calls=1, session=session, clock=StepClock(), **hashes,
        )
    assert session.calls == []


def test_run_id_reuse_and_output_collision_block_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, hashes = _predecessor_fixture(tmp_path, monkeypatch)
    (private / "raw" / "alpha_vantage" / "collision-run").mkdir()
    session = FakeSession()
    with pytest.raises(collector.OfflineCollectorError, match="collision"):
        collector.collect_alpha_continuation_evidence(
            project_root=tmp_path, api_key="secret", run_id="collision-run",
            approval_reference="future-approval",
            personal_noncommercial_confirmed=True,
            current_provider_limit_review_confirmed=True,
            max_calls=1, session=session, clock=StepClock(), **hashes,
        )
    assert session.calls == []


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda rows: rows.append(dict(rows[0])), "estimate_period_duplicate"),
        (lambda rows: rows[0].update(eps_estimate_high="1.00"), "estimate_eps_range_invalid"),
        (lambda rows: rows[0].update(revenue_estimate_low="43000000000"), "estimate_revenue_range_invalid"),
        (lambda rows: rows[0].update(eps_estimate_average=float("nan")), "estimate_numeric_invalid"),
        (lambda rows: rows[0].update(eps_estimate_average=float("inf")), "estimate_numeric_invalid"),
        (lambda rows: rows[0].update(eps_estimate_average=True), "estimate_numeric_invalid"),
        (lambda rows: rows[0].update(eps_estimate_analyst_count=-1), "estimate_analyst_count_invalid"),
    ],
)
def test_estimates_array_semantics_fail_closed(mutate: Any, code: str) -> None:
    value = {"symbol": "NVDA", "estimates": [_estimate_row()]}
    mutate(value["estimates"])
    result = validate_dataset_bytes(
        json.dumps(value).encode(), "application/json",
        function="EARNINGS_ESTIMATES", ticker="NVDA",
        available_at="2026-10-17T12:00:00Z",
    )
    assert result["valid_provider_dataset"] is False
    assert result["validation_code"] == code


def _complete_cli_report() -> dict[str, Any]:
    return {
        "run_id": "fixture-run", "logical_operation_count": 1,
        "approved_actual_http_request_budget": 1, "concurrency": 1,
        "calls_attempted": 1, "calls_completed": 1, "automatic_retries": 0,
        "run_state": "completed", "failure_code": None,
        "results": [{
            "request_id": "nvda-estimates", "http_status": 200,
            "capture_state": "captured", "valid_provider_dataset": True,
            "failure_code": None, "provider_restriction_class": None,
            "timestamp_order_valid": True, "response_sha256": "A" * 64,
            "byte_length": 100, "accounting_basis": "unknown",
            "historical_availability_verified": False,
            "fy1_ntm_mapping_eligible": False, "parser_candidate": False,
            "snapshot_created": False, "decision_created": False,
            "operational_eligible": False,
        }],
        "parser_attempted": False, "candidate_created": False,
        "snapshot_created": False, "decision_created": False,
        "operational_eligible": False,
    }


def _run_cli(monkeypatch: pytest.MonkeyPatch, report: dict[str, Any]) -> int:
    monkeypatch.setattr(sys, "argv", [
        "collect_alpha_continuation.py", "--execute",
        "--run-id", "future-approved-run",
        "--approval-reference", "future-independent-qa-approval",
        "--max-calls", "1", "--confirm-personal-noncommercial",
        "--confirm-current-provider-limit-reviewed",
    ])
    monkeypatch.setattr(cli, "api_key_from_environment", lambda: "secret")
    monkeypatch.setattr(cli, "collect_alpha_continuation_evidence", lambda **kwargs: report)
    return cli.main()


def test_cli_returns_zero_only_for_exact_success(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_cli(monkeypatch, _complete_cli_report()) == 0


@pytest.mark.parametrize("failure_code", ["timeout", "connection_error", "http_500", "provider_restriction_unknown"])
def test_cli_returns_two_for_failed_capture(
    monkeypatch: pytest.MonkeyPatch, failure_code: str,
) -> None:
    report = _complete_cli_report()
    report.update(run_state="failed", failure_code=failure_code)
    assert _run_cli(monkeypatch, report) == 2


def test_cli_missing_key_blocks_before_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", [
        "collect_alpha_continuation.py", "--execute", "--run-id", "future-run",
        "--approval-reference", "future-approval", "--max-calls", "1",
        "--confirm-personal-noncommercial", "--confirm-current-provider-limit-reviewed",
    ])
    monkeypatch.setattr(cli, "api_key_from_environment", lambda: "")
    monkeypatch.setattr(
        cli, "collect_alpha_continuation_evidence",
        lambda **kwargs: pytest.fail("collector called without API key"),
    )
    assert cli.main() == 2
