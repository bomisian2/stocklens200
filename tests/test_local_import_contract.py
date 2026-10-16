from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal import local_import_contract as contract


DECISION_TIME = "2026-10-17T12:00:00Z"
ROOT = Path(__file__).resolve().parents[1]


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    data_kind: str = "forward_estimates",
    evidence_class: str = "user_provided_real",
) -> tuple[Path, Path, dict]:
    private = tmp_path / "private_data"
    inbox = private / "imports" / "inbox"
    inbox.mkdir(parents=True)
    source = inbox / "AVGO.json"
    source.write_text(json.dumps({"records": [{"value": 10.5}]}), encoding="utf-8")
    manifest = {
        "schema_version": contract.SCHEMA_VERSION,
        "data_kind": data_kind,
        "evidence_class": evidence_class,
        "provider": "example-provider",
        "ticker": "AVGO",
        "source_file": "imports/inbox/AVGO.json",
        "source_format": "json",
        "source_hash": hashlib.sha256(source.read_bytes()).hexdigest().upper(),
        "as_of": "2026-10-16",
        "available_at": "2026-10-16T20:05:00Z",
        "captured_at": "2026-10-16T20:10:00Z",
        "accounting_basis": "GAAP",
        "horizon": "FY1",
        "currency": "USD",
        "per_share_unit": "currency_per_diluted_share",
        "analyst_count": 20,
        "revision_or_vintage": "2026-10-16-v1",
        "usage_scope": "personal_local_research",
        "price_adjustment_policy": "not_applicable",
        "rights": {
            "storage": "authorized",
            "derived_analysis": "authorized",
            "local_display": "authorized",
            "public_display": "not_authorized",
        },
    }
    if data_kind == "daily_prices":
        manifest.update({
            "accounting_basis": "not_applicable",
            "horizon": "not_applicable",
            "per_share_unit": "not_applicable",
            "analyst_count": None,
            "price_adjustment_policy": "unadjusted",
        })
    manifest_path = inbox / "AVGO.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(contract, "resolve_repository_private_data", lambda *args, **kwargs: private)
    return source, manifest_path, manifest


def _save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _validate(tmp_path: Path) -> dict:
    return contract.validate_provider_neutral_import(
        project_root=tmp_path,
        manifest_relative_path="imports/inbox/AVGO.manifest.json",
        decision_time=DECISION_TIME,
    )


def test_checked_in_schema_matches_runtime_contract() -> None:
    schema = json.loads(
        (ROOT / "config/provider-neutral-local-import-v1.schema.json").read_text(
            encoding="utf-8",
        )
    )
    assert schema["$id"] == contract.SCHEMA_VERSION
    assert set(schema["properties"]["horizon"]["enum"]) == {
        "FY1", "FY2", "NTM", "not_applicable",
    }
    assert set(schema["properties"]["price_adjustment_policy"]["enum"]) == {
        "adjusted", "unadjusted", "not_applicable",
    }
    assert {"storage", "derived_analysis", "local_display"}.issubset(
        schema["properties"]["rights"]["required"],
    )


def test_real_forward_estimate_import_is_validated_but_never_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, _ = _fixture(tmp_path, monkeypatch)
    result = _validate(tmp_path)
    assert result["status"] == "validated_local_import"
    assert result["source_hash"] == hashlib.sha256(source.read_bytes()).hexdigest().upper()
    assert result["accounting_basis"] == "GAAP"
    assert result["horizon"] == "FY1"
    assert result["real_data_eligible"] is True
    assert result["parser_candidate"] is result["decision_created"] is False
    assert result["operational_eligible"] is False


def test_unadjusted_daily_import_is_limited_to_recent_flow_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path, monkeypatch, data_kind="daily_prices")
    result = _validate(tmp_path)
    assert result["price_adjustment_policy"] == "unadjusted"
    assert result["allowed_uses"] == ["recent_price_flow_research", "integrity_diagnostics"]
    assert result["parser_candidate"] is False


def test_synthetic_fixture_is_never_reported_as_real_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path, monkeypatch, evidence_class="synthetic_fixture")
    result = _validate(tmp_path)
    assert result["status"] == "validated_synthetic_fixture"
    assert result["synthetic_fixture"] is True
    assert result["real_data_eligible"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("accounting_basis", "unknown", "basis is unknown"),
        ("horizon", "unknown", "horizon is unknown"),
        ("available_at", None, "available_at is missing"),
        ("available_at", "2026-10-18T00:00:00Z", "not available by decision_time"),
        ("source_hash", "0" * 64, "does not match source bytes"),
        ("usage_scope", "unknown", "usage_scope is not approved"),
    ],
)
def test_forward_import_fail_closed_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    _, manifest_path, manifest = _fixture(tmp_path, monkeypatch)
    manifest[field] = value
    _save(manifest_path, manifest)
    with pytest.raises(contract.LocalImportContractError, match=message):
        _validate(tmp_path)


@pytest.mark.parametrize("field", ["storage", "derived_analysis", "local_display"])
def test_import_blocks_unconfirmed_storage_or_analysis_rights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    _, manifest_path, manifest = _fixture(tmp_path, monkeypatch)
    manifest["rights"][field] = "manual_review_required"
    _save(manifest_path, manifest)
    with pytest.raises(contract.LocalImportContractError, match="not explicitly authorized"):
        _validate(tmp_path)


def test_import_blocks_unknown_public_display_right(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manifest_path, manifest = _fixture(tmp_path, monkeypatch)
    manifest["rights"]["public_display"] = "unknown"
    _save(manifest_path, manifest)
    with pytest.raises(contract.LocalImportContractError, match="public_display right is unknown"):
        _validate(tmp_path)


def test_daily_import_blocks_unknown_adjustment_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manifest_path, manifest = _fixture(tmp_path, monkeypatch, data_kind="daily_prices")
    manifest["price_adjustment_policy"] = "unknown"
    _save(manifest_path, manifest)
    with pytest.raises(contract.LocalImportContractError, match="adjusted/unadjusted policy is unknown"):
        _validate(tmp_path)


def test_import_paths_cannot_escape_private_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _ = _fixture(tmp_path, monkeypatch)
    with pytest.raises(contract.LocalImportContractError, match="stay inside private_data"):
        contract.validate_provider_neutral_import(
            project_root=tmp_path,
            manifest_relative_path="imports/../outside.json",
            decision_time=DECISION_TIME,
        )
