from __future__ import annotations

import sys
from typing import Any

import pytest

from scripts import collect_alpha_supplemental as cli


def _result(request_id: str, *, valid: bool = True) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "http_status": 200,
        "capture_state": "captured" if valid else "invalid_provider_dataset",
        "valid_provider_dataset": valid,
        "failure_code": None if valid else "provider_error_envelope",
        "provider_restriction_class": None,
        "timestamp_order_valid": True,
        "response_sha256": "A" * 64,
        "byte_length": 100,
    }


def _report(**overrides: Any) -> dict[str, Any]:
    request_ids = [row["request_id"] for row in cli.exact_supplemental_request_plan()]
    value: dict[str, Any] = {
        "run_id": "p5-fixture",
        "logical_operation_count": 3,
        "approved_actual_http_request_budget": 3,
        "request_interval_seconds": 60,
        "concurrency": 1,
        "calls_attempted": 3,
        "calls_completed": 3,
        "automatic_retries": 0,
        "run_state": "completed",
        "failure_code": None,
        "results": [_result(request_id) for request_id in request_ids],
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    value.update(overrides)
    return value


def _run_cli(monkeypatch: pytest.MonkeyPatch, report: dict[str, Any]) -> int:
    monkeypatch.setattr(sys, "argv", [
        "collect_alpha_supplemental.py",
        "--execute",
        "--run-id", "p5-fixture",
        "--approval-reference", "future-independent-qa-approval",
        "--max-calls", "3",
        "--request-interval-seconds", "60",
        "--confirm-personal-noncommercial",
        "--confirm-current-provider-limit-reviewed",
    ])
    monkeypatch.setattr(cli, "api_key_from_environment", lambda: "secret")
    monkeypatch.setattr(cli, "collect_alpha_supplemental_evidence", lambda **kwargs: report)
    return cli.main()


def test_supplemental_cli_show_plan_is_zero_call_and_exact(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["collect_alpha_supplemental.py", "--show-plan"])
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert '"maximum_actual_http_requests": 3' in output
    assert '"execution_approved": false' in output
    assert '"terms_request_included": false' in output
    assert '"stop_on_any_failed_capture": true' in output


def test_supplemental_cli_returns_zero_only_for_exact_completion(monkeypatch) -> None:
    assert _run_cli(monkeypatch, _report()) == 0


@pytest.mark.parametrize(
    "report",
    [
        _report(
            run_state="failed", failure_code="provider_rate_or_plan_limit",
            calls_attempted=1, calls_completed=1,
            results=[_result("nvda-daily", valid=False)],
        ),
        _report(run_state="failed", failure_code="timeout", calls_attempted=1, calls_completed=0),
        _report(run_state="failed", failure_code="data_collection_incomplete"),
    ],
    ids=["provider-restriction", "timeout", "incomplete"],
)
def test_supplemental_cli_returns_two_for_failure(monkeypatch, report) -> None:
    assert _run_cli(monkeypatch, report) == 2
