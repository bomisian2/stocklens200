from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.consensus_basis_audit import (
    CONTRACT_VERSION,
    ConsensusBasisAuditError,
    audit_nvda_consensus_basis,
    load_consensus_basis_audit,
    validate_consensus_source_contract,
    write_consensus_basis_audit,
)


def _bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_bytes(value))
    return path


def _fixtures(tmp_path: Path) -> dict[str, Path]:
    nasdaq = {
        "generatedAt": "2026-10-19T22:35:57.807Z",
        "records": {
            "NVDA": {
                "ticker": "NVDA",
                "collectedAt": "2026-10-19T22:35:57.807Z",
                "providerId": "nasdaq-analyst-earnings-forecast",
                "fiscalEnd": "Jan 2027",
                "consensusEps": 9.30,
                "currency": "USD",
                "epsBasis": "unknown",
                "accountingBasis": "provider_adjusted_unspecified",
                "shareBasis": "provider per-share; split/ADR normalization unverified",
                "sourceUrl": "https://api.nasdaq.com/api/analyst/NVDA/earnings-forecast",
            }
        },
    }
    nasdaq_path = _write(tmp_path / "nasdaq.json", nasdaq)
    collector_path = tmp_path / "refresh-top100-consensus.mjs"
    collector_path.write_text(
        'epsBasis:"unknown"\n'
        'accountingBasis:"provider_adjusted_unspecified"\n'
        'shareBasis:"provider per-share; split/ADR normalization unverified"\n',
        encoding="utf-8",
    )

    alpha = {
        "symbol": "NVDA",
        "estimates": [
            {
                "date": "2027-01-31",
                "horizon": "fiscal year",
                "eps_estimate_average": "9.3124",
            }
        ],
    }
    alpha_path = _write(tmp_path / "alpha.raw", alpha)
    alpha_raw = alpha_path.read_bytes()
    manifest = {
        "response_sha256": _sha(alpha_raw),
        "available_at": "2026-10-18T00:38:27.040534Z",
    }
    manifest_path = _write(tmp_path / "alpha.manifest.json", manifest)
    adjudication = {
        "accounting_basis": "unknown",
        "historical_availability_verified": False,
        "operational_eligible": False,
        "source": {
            "raw_sha256": _sha(alpha_raw),
            "manifest_sha256": _sha(manifest_path.read_bytes()),
        },
    }
    adjudication_path = _write(tmp_path / "alpha.adjudication.json", adjudication)
    units_path = _write(
        tmp_path / "eps-unit-evidence.json",
        {"records": {"ASML": {}, "ARM": {}, "TSM": {}}},
    )
    return {
        "nasdaq_snapshot_path": nasdaq_path,
        "nasdaq_collector_path": collector_path,
        "alpha_raw_path": alpha_path,
        "alpha_manifest_path": manifest_path,
        "alpha_adjudication_path": adjudication_path,
        "eps_unit_evidence_path": units_path,
    }


def _audit(tmp_path: Path) -> dict[str, object]:
    return audit_nvda_consensus_basis(
        **_fixtures(tmp_path), generated_at="2026-10-26T00:00:00Z"
    )


def _verified_shape() -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "provider": "Example Licensed Consensus",
        "ticker": "NVDA",
        "evidence_kind": "street_consensus",
        "provider_native_raw_bytes": True,
        "source_url": "https://example.test/nvda-consensus",
        "source_hash": "A" * 64,
        "captured_at": "2026-10-19T22:35:57Z",
        "available_at": "2026-10-19T22:35:57Z",
        "fiscal_period": "FY ending 2027-01-31",
        "accounting_basis": "adjusted",
        "eps_share_basis": "diluted",
        "split_adjustment": "adjusted",
        "security_form": "common_stock",
        "adr_share_conversion": {
            "status": "not_applicable",
            "ratio": None,
            "source_hash": None,
        },
        "currency": "USD",
        "usage_rights": {
            "storage": "authorized",
            "derived_analysis": "authorized",
            "local_display": "authorized",
            "public_display": "not_authorized",
        },
    }


def test_offline_audit_keeps_proxy_and_verified_price_maps_separate(tmp_path: Path) -> None:
    result = _audit(tmp_path)
    assert result["proxy_price_map"]["consensus_eps"] == pytest.approx(9.30)
    assert result["alpha_crosscheck"]["average_eps"] == pytest.approx(9.3124)
    assert result["alpha_crosscheck"]["difference_pct"] == pytest.approx(0.1333333333)
    assert result["proxy_price_map"]["confidence_effect"] == "cross_provider_numeric_agreement_only"
    assert result["verified_price_map"]["status"] == "locked"
    assert result["verified_price_map"]["values"] == {}
    assert result["decision_state"]["verified_action"] is None
    assert result["decision_state"]["operational_eligible"] is False


def test_nasdaq_normalized_snapshot_does_not_claim_provider_native_methodology(
    tmp_path: Path,
) -> None:
    result = _audit(tmp_path)
    finding = result["nasdaq_investigation"]
    assert finding["provider_native_raw_bytes_preserved"] is False
    assert finding["methodology_evidence_found"] is False
    assert finding["accounting_basis"] == "unknown"
    assert finding["eps_share_basis"] == "unknown"
    assert finding["split_adjustment"] == "unknown"
    assert finding["adr_share_conversion"] == "unknown"
    assert finding["forward_period_definition"] == "unknown_provider_fiscal_label_only"


def test_unit_evidence_is_scoped_and_cannot_validate_nvda(tmp_path: Path) -> None:
    result = _audit(tmp_path)
    units = result["ticker_scoped_unit_evidence"]
    assert units["covered_tickers"] == ["ARM", "ASML", "TSM"]
    assert units["nvda_covered"] is False
    assert "nvda_unit_evidence_missing" in result["verified_price_map"]["blockers"]


def test_unknown_basis_and_rights_block_verified_contract() -> None:
    contract = _verified_shape()
    contract["provider_native_raw_bytes"] = False
    contract["accounting_basis"] = "unknown"
    contract["eps_share_basis"] = "unknown"
    contract["split_adjustment"] = "unknown"
    contract["security_form"] = "unknown"
    contract["adr_share_conversion"] = {
        "status": "unknown",
        "ratio": None,
        "source_hash": None,
    }
    contract["usage_rights"]["derived_analysis"] = "manual_review_required"
    with pytest.raises(ConsensusBasisAuditError, match="verified consensus contract blocked"):
        validate_consensus_source_contract(contract, require_verified=True)


def test_complete_contract_can_be_validated_without_unlocking_current_audit() -> None:
    outcome = validate_consensus_source_contract(_verified_shape(), require_verified=True)
    assert outcome == {"verified_eligible": True, "blockers": []}


def test_issuer_actuals_and_guidance_cannot_substitute_for_street_consensus() -> None:
    for evidence_kind in ("issuer_actual", "issuer_guidance"):
        contract = _verified_shape()
        contract["evidence_kind"] = evidence_kind
        with pytest.raises(ConsensusBasisAuditError, match="cannot substitute"):
            validate_consensus_source_contract(contract, require_verified=False)


def test_contract_shape_matches_checked_in_json_schema() -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "decision-model-v2-consensus-source-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == set(_verified_shape())
    assert schema["additionalProperties"] is False


def test_audit_round_trip_and_source_tamper_are_fail_closed(tmp_path: Path) -> None:
    result = _audit(tmp_path)
    output = tmp_path / "audit.json"
    write_consensus_basis_audit(output, result)
    assert load_consensus_basis_audit(output) == result

    nasdaq_path = Path(result["sources"]["nasdaq_normalized_snapshot"]["path"])
    nasdaq = json.loads(nasdaq_path.read_text(encoding="utf-8"))
    nasdaq["records"]["NVDA"]["consensusEps"] = 99.0
    nasdaq_path.write_bytes(_bytes(nasdaq))
    with pytest.raises(ConsensusBasisAuditError, match="source hash mismatch"):
        load_consensus_basis_audit(output)


def test_numeric_agreement_cannot_unlock_verified_map(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    alpha = json.loads(paths["alpha_raw_path"].read_text(encoding="utf-8"))
    alpha["estimates"][0]["eps_estimate_average"] = "9.3000"
    paths["alpha_raw_path"].write_bytes(_bytes(alpha))
    raw_hash = _sha(paths["alpha_raw_path"].read_bytes())
    manifest = json.loads(paths["alpha_manifest_path"].read_text(encoding="utf-8"))
    manifest["response_sha256"] = raw_hash
    paths["alpha_manifest_path"].write_bytes(_bytes(manifest))
    adjudication = json.loads(paths["alpha_adjudication_path"].read_text(encoding="utf-8"))
    adjudication["source"]["raw_sha256"] = raw_hash
    adjudication["source"]["manifest_sha256"] = _sha(
        paths["alpha_manifest_path"].read_bytes()
    )
    paths["alpha_adjudication_path"].write_bytes(_bytes(adjudication))

    result = audit_nvda_consensus_basis(
        **paths, generated_at="2026-10-26T00:00:00Z"
    )
    assert result["alpha_crosscheck"]["difference_pct"] == 0
    assert result["verified_price_map"]["status"] == "locked"
    assert result["decision_state"]["verified_action"] is None


def test_calculation_hash_tamper_is_blocked(tmp_path: Path) -> None:
    result = _audit(tmp_path)
    output = tmp_path / "audit.json"
    write_consensus_basis_audit(output, result)
    tampered = copy.deepcopy(result)
    tampered["verified_price_map"]["status"] = "ready"
    output.write_bytes(_bytes(tampered))
    with pytest.raises(ConsensusBasisAuditError, match="calculation hash mismatch"):
        load_consensus_basis_audit(output)
