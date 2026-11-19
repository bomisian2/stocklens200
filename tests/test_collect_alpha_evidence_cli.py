from __future__ import annotations

import sys
from typing import Any

import pytest

from scripts import collect_alpha_evidence as cli


def _result(request_id: str, *, valid: bool = True) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "capture_state": "captured" if valid else "invalid_provider_dataset",
        "valid_provider_dataset": valid,
        "failure_code": None if valid else "provider_error_envelope",
    }


def _report(**overrides: Any) -> dict[str, Any]:
    request_ids = [row["request_id"] for row in cli.exact_request_plan()]
    value: dict[str, Any] = {
        "run_id": "fixture-run",
        "logical_operation_count": 5,
        "calls_attempted": 5,
        "calls_completed": 5,
        "approved_actual_http_request_budget": 5,
        "automatic_retries": 0,
        "run_state": "completed",
        "failure_code": None,
        "active_request_id": None,
        "results": [_result(request_id) for request_id in request_ids],
        "parser_attempted": False,
        "decision_created": False,
    }
    value.update(overrides)
    return value


def _run_cli(monkeypatch: pytest.MonkeyPatch, report: dict[str, Any]) -> int:
    monkeypatch.setattr(sys, "argv", [
        "collect_alpha_evidence.py", "--execute", "--run-id", "fixture-run",
        "--max-calls", "5", "--confirm-personal-noncommercial",
    ])
    monkeypatch.setattr(cli, "api_key_from_environment", lambda: "secret")
    monkeypatch.setattr(cli, "collect_approved_alpha_evidence", lambda **kwargs: report)
    return cli.main()


def test_cli_returns_zero_only_for_normal_five_operation_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _run_cli(monkeypatch, _report()) == 0


@pytest.mark.parametrize(
    "report",
    [
        _report(run_state="failed", failure_code="terms_validation_failed",
                calls_attempted=1, calls_completed=1, results=[_result("alpha-vantage-terms", valid=False)]),
        _report(run_state="failed", failure_code="timeout",
                calls_attempted=1, calls_completed=0, results=[]),
        _report(run_state="failed", failure_code="connection_error",
                calls_attempted=1, calls_completed=0, results=[]),
        _report(run_state="interrupted", failure_code="interrupted",
                calls_attempted=1, calls_completed=0, results=[]),
        _report(run_state="incomplete", failure_code="data_collection_incomplete",
                calls_attempted=4, calls_completed=4, results=[]),
        _report(run_state="failed", failure_code="data_collection_incomplete",
                results=[_result("alpha-vantage-terms"), _result("avgo-daily", valid=False)]),
        _report(run_state="failed", failure_code="collector_policy_error",
                calls_attempted=5, calls_completed=4),
    ],
    ids=[
        "terms-failure", "timeout", "connection-failure", "interrupted", "incomplete",
        "provider-error-envelope", "budget-exhausted",
    ],
)
def test_cli_returns_two_for_any_incomplete_or_failed_collection(
    monkeypatch: pytest.MonkeyPatch, report: dict[str, Any],
) -> None:
    assert _run_cli(monkeypatch, report) == 2
