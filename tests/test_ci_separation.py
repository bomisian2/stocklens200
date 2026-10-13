from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_provider_rights import validate_provider_rights


ROOT = Path(__file__).resolve().parents[1]


def test_pr_ci_is_offline_and_separates_code_from_snapshot_integrity() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "code-tests (offline)" in workflow
    assert "stored-snapshot-integrity (offline)" in workflow
    assert "python -m pytest -q" in workflow
    assert "python scripts/validate_snapshot.py" in workflow
    assert "scripts/update_data.py" not in workflow
    assert "--strict" not in workflow


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
