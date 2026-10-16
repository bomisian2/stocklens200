from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_provider_rights import validate_provider_rights
from scripts import run_streamlit_apptests as app_runner


ROOT = Path(__file__).resolve().parents[1]


def test_pr_ci_is_offline_and_separates_code_from_snapshot_integrity() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "code-tests (offline)" in workflow
    assert "stored-snapshot-integrity (offline)" in workflow
    assert 'python -m pytest -q -m "not streamlit"' in workflow
    assert "python scripts/run_streamlit_apptests.py --timeout-seconds 180" in workflow
    assert 'python-version: "3.12"' in workflow
    assert "python scripts/validate_snapshot.py" in workflow
    assert "scripts/update_data.py" not in workflow
    assert "--strict" not in workflow


def test_streamlit_runner_pins_python_and_uses_process_timeout() -> None:
    runner = (ROOT / "scripts" / "run_streamlit_apptests.py").read_text(encoding="utf-8")
    assert "SUPPORTED_PYTHON = (3, 12)" in runner
    assert "subprocess.run" in runner
    assert "deadline = time.monotonic() + args.timeout_seconds" in runner
    assert "deadline - time.monotonic()" in runner
    assert "TimeoutExpired" in runner
    assert "EXPECTED_APPTEST_COUNT = 6" in runner
    assert '"--collect-only"' in runner
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


@pytest.mark.parametrize("collected_count", [0, 5, 7])
def test_streamlit_runner_returns_two_when_collection_count_is_not_six(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collected_count: int,
) -> None:
    nodes = "\n".join(f"tests/test_app.py::test_{index}" for index in range(collected_count))
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=nodes, stderr="")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_streamlit_apptests.py", "--timeout-seconds", "180"])
    monkeypatch.setattr(app_runner.subprocess, "run", fake_run)
    assert app_runner.main() == 2
    assert len(calls) == 1


def test_streamlit_runner_runs_tests_only_after_exactly_six_unique_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = "\n".join(f"tests/test_app.py::test_{index}" for index in range(6))
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "--collect-only" in command:
            return subprocess.CompletedProcess(command, 0, stdout=nodes, stderr="")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_streamlit_apptests.py", "--timeout-seconds", "180"])
    monkeypatch.setattr(app_runner.subprocess, "run", fake_run)
    assert app_runner.main() == 0
    assert len(calls) == 2


def test_provider_diagnostic_is_manual_rights_gated_and_not_scheduled() -> None:
    workflow = (ROOT / ".github" / "workflows" / "provider-diagnostics.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "schedule:" not in workflow
    assert "check_provider_rights.py" in workflow
    assert workflow.index("check_provider_rights.py") < workflow.index("scripts/update_data.py")
    assert "contents: read" in workflow


def test_scheduled_refresh_checks_rights_before_network_collection() -> None:
    workflow = (ROOT / ".github" / "workflows" / "refresh-data.yml").read_text(encoding="utf-8")

    assert "--purpose scheduled_refresh" in workflow
    assert workflow.index("check_provider_rights.py") < workflow.index("scripts/update_data.py")


def test_current_rights_manifest_blocks_external_provider_diagnostics() -> None:
    manifest = json.loads((ROOT / "config" / "provider-rights.json").read_text(encoding="utf-8"))

    with pytest.raises(SystemExit, match="does not authorize external collection"):
        validate_provider_rights(
            manifest, purpose="manual_diagnostic", operator_confirmed=True
        )


def test_operator_confirmation_is_required_even_if_manifest_allows_calls() -> None:
    manifest = {
        "automated_external_collection_authorized": True,
        "providers": {"example": {"diagnostic_call_authorized": True}},
    }

    with pytest.raises(SystemExit, match="operator confirmation is false"):
        validate_provider_rights(
            manifest, purpose="manual_diagnostic", operator_confirmed=False
        )


def test_scheduled_refresh_is_also_blocked_by_current_rights_manifest() -> None:
    manifest = json.loads((ROOT / "config" / "provider-rights.json").read_text(encoding="utf-8"))

    with pytest.raises(SystemExit, match="does not authorize external collection"):
        validate_provider_rights(manifest, purpose="scheduled_refresh")
