from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from stocklens.personal import supplemental_alpha_adjudication as adjudication
from stocklens.personal.alpha_evidence_validation import validate_dataset_bytes


AVAILABLE_AT = "2026-10-17T06:29:22.342127Z"


def _row(day: str = "2027-01-31", horizon: str = "fiscal quarter") -> dict:
    return {
        "date": day,
        "horizon": horizon,
        "eps_estimate_average": "2.50",
        "eps_estimate_high": "2.80",
        "eps_estimate_low": "2.20",
        "eps_estimate_analyst_count": "31.0000",
        "revenue_estimate_average": "15000000000",
        "revenue_estimate_high": "16000000000",
        "revenue_estimate_low": "14000000000",
        "revenue_estimate_analyst_count": "28.00",
        "eps_estimate_average_7_days_ago": "2.45",
        "eps_estimate_average_30_days_ago": "2.40",
        "eps_estimate_average_60_days_ago": "2.35",
        "eps_estimate_average_90_days_ago": "2.30",
        "eps_estimate_revision_up_trailing_7_days": "1",
        "eps_estimate_revision_down_trailing_7_days": None,
        "eps_estimate_revision_up_trailing_30_days": "3",
        "eps_estimate_revision_down_trailing_30_days": "0",
    }


def _modern_payload(ticker: str = "AVGO") -> dict:
    return {
        "symbol": ticker,
        "estimates": [
            _row("2026-07-31", "fiscal quarter"),
            _row("2026-10-31", "fiscal quarter"),
            _row("2027-01-31", "fiscal quarter"),
            _row("2027-10-31", "fiscal year"),
        ],
    }


def _validate(payload: dict, ticker: str = "AVGO") -> dict:
    return validate_dataset_bytes(
        json.dumps(payload).encode(),
        "application/json",
        function="EARNINGS_ESTIMATES",
        ticker=ticker,
        available_at=AVAILABLE_AT,
    )


def test_current_estimates_array_structure_is_valid_but_never_promoted() -> None:
    result = _validate(_modern_payload())
    assert result["valid_provider_dataset"] is True
    assert result["provider_schema_version"] == "alpha-vantage-estimates-array-v1"
    assert result["record_count"] == 4
    assert result["fiscal_quarter_count"] == 3
    assert result["fiscal_year_count"] == 1
    assert result["forward_period_candidate_count"] == 3
    assert result["accounting_basis"] == "unknown"
    assert result["parser_candidate_basis_eligible"] is False
    assert result["parser_candidate"] is False
    assert result["historical_availability_verified"] is False
    assert result["fy1_ntm_mapping_eligible"] is False
    assert result["decision_created"] is False
    assert result["operational_eligible"] is False


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda value: value.update(symbol="NVDA"), "ticker_mismatch"),
        (lambda value: value.update(estimates=[]), "estimate_periods_missing"),
        (lambda value: value["estimates"][0].update(date="2026-02-30"), "estimate_date_invalid"),
        (lambda value: value["estimates"][0].update(horizon="NTM"), "estimate_horizon_invalid"),
        (lambda value: value["estimates"][0].pop("eps_estimate_average"), "estimate_required_field_missing"),
        (lambda value: value["estimates"][0].update(eps_estimate_average="NaN"), "estimate_numeric_invalid"),
        (lambda value: value["estimates"][0].update(revenue_estimate_high="Infinity"), "estimate_numeric_invalid"),
        (lambda value: value["estimates"][0].update(eps_estimate_analyst_count="-1"), "estimate_analyst_count_invalid"),
        (lambda value: value["estimates"][0].update(unexpected="value"), "estimate_field_not_allowed"),
    ],
)
def test_current_estimates_array_invalid_variants_are_blocked(mutate, expected_code: str) -> None:
    payload = _modern_payload()
    mutate(payload)
    result = _validate(payload)
    assert result["valid_provider_dataset"] is False
    assert result["validation_code"] == expected_code


def test_provider_error_envelope_is_not_confused_with_estimates_array() -> None:
    result = _validate({"Information": "redacted fixture restriction"})
    assert result["valid_provider_dataset"] is False
    assert result["validation_code"] == "provider_error_envelope"


def test_legacy_and_current_shapes_are_separate_and_mixed_shape_is_blocked() -> None:
    legacy = {
        "symbol": "AVGO",
        "accountingBasis": "GAAP",
        "annualEarnings": [{
            "fiscalDateEnding": "2027-10-31",
            "numberAnalystsEstimatedEps": "20",
        }],
        "quarterlyEarnings": [],
    }
    legacy_result = _validate(legacy)
    assert legacy_result["valid_provider_dataset"] is True
    assert legacy_result["provider_schema_version"] == "alpha-vantage-estimates-legacy-v1"

    mixed = _modern_payload()
    mixed["annualEarnings"] = legacy["annualEarnings"]
    mixed_result = _validate(mixed)
    assert mixed_result["valid_provider_dataset"] is False
    assert mixed_result["validation_code"] == "estimate_schema_ambiguous"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _make_synthetic_run(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("private_data/\n", encoding="utf-8")
    private = tmp_path / "private_data"
    run_id = "p501-synthetic-p502"
    run_root = private / "raw" / "alpha_vantage" / run_id
    run_root.mkdir(parents=True)
    started = "2026-10-17T06:28:21.117512Z"
    finished = "2026-10-17T06:29:22.384726Z"
    specifications = [
        (
            "nvda-daily", "NVDA_TIME_SERIES_DAILY.response.raw", "TIME_SERIES_DAILY", "NVDA",
            {
                "Meta Data": {"2. Symbol": "NVDA"},
                "Time Series (Daily)": {
                    "2026-10-16": {
                        "1. open": "200", "2. high": "205", "3. low": "198",
                        "4. close": "203", "5. volume": "1000",
                    },
                },
            },
            "captured", "valid_unadjusted_daily", None,
            "2026-10-17T06:28:21.218927Z", "2026-10-17T06:28:21.619124Z",
            "2026-10-17T06:28:21.775956Z",
        ),
        (
            "avgo-estimates", "AVGO_EARNINGS_ESTIMATES.response.raw", "EARNINGS_ESTIMATES", "AVGO",
            _modern_payload(), "invalid_provider_dataset", "estimate_periods_missing",
            "estimate_periods_missing", "2026-10-17T06:29:21.815563Z",
            "2026-10-17T06:29:22.032063Z", AVAILABLE_AT,
        ),
    ]
    results: list[dict] = []
    original_hashes: dict[str, str] = {}
    for (
        request_id, raw_name, function, ticker, payload, capture_state,
        validation_code, failure_code, request_started, headers_received,
        response_completed,
    ) in specifications:
        raw = json.dumps(payload).encode()
        raw_path = run_root / raw_name
        raw_path.write_bytes(raw)
        digest = _sha(raw)
        capture = {
            "collector_version": "alpha-vantage-offline-evidence-v2",
            "request_id": request_id,
            "provider": "Alpha Vantage",
            "request_host": "www.alphavantage.co",
            "request_path": "/query",
            "function": function,
            "ticker": ticker,
            "request_started_at": request_started,
            "headers_received_at": headers_received,
            "response_completed_at": response_completed,
            "available_at": response_completed,
            "timestamp_order_valid": True,
            "http_status": 200,
            "final_url": "https://www.alphavantage.co/query",
            "redirect_chain": [],
            "content_type": "application/json",
            "byte_length": len(raw),
            "response_sha256": digest,
            "response_file": raw_name,
            "capture_state": capture_state,
            "failure_code": failure_code,
            "valid_provider_dataset": capture_state == "captured",
            "validation_code": validation_code,
            "automatic_retries": 0,
            "api_key_persisted": False,
            "request_query_persisted": False,
            "parser_attempted": False,
            "candidate_created": False,
            "snapshot_created": False,
            "decision_created": False,
            "operational_eligible": False,
        }
        capture_path = run_root / f"{raw_name}.manifest.json"
        _write_json(capture_path, capture)
        results.append(deepcopy(capture))
        original_hashes[raw_name] = _sha(raw_path.read_bytes())
        original_hashes[capture_path.name] = _sha(capture_path.read_bytes())
    ledger = {
        "collector_version": "alpha-vantage-supplemental-evidence-p5.0.1-v1",
        "run_id": run_id,
        "started_at": started,
        "finished_at": finished,
        "calls_attempted": 2,
        "calls_completed": 2,
        "automatic_retries": 0,
        "run_state": "failed",
        "failure_code": "estimate_periods_missing",
        "results": results,
        "parser_attempted": False,
        "candidate_created": False,
        "snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
    }
    ledger_path = run_root / "supplemental-collection-run.json"
    _write_json(ledger_path, ledger)
    original_hashes[ledger_path.name] = _sha(ledger_path.read_bytes())
    return private, run_id, original_hashes


def test_offline_adjudication_hash_links_without_mutating_or_promoting_synthetic_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, run_id, original_hashes = _make_synthetic_run(tmp_path)
    monkeypatch.setattr(
        adjudication, "resolve_repository_private_data", lambda *args, **kwargs: private,
    )
    result = adjudication.adjudicate_supplemental_run(
        project_root=tmp_path,
        run_id=run_id,
        generated_at="2026-10-17T07:00:00Z",
        evidence_origin="synthetic_fixture",
    )
    output = json.loads(result["adjudication_path"].read_text(encoding="utf-8"))
    assert output["evidence_origin"] == "synthetic_fixture"
    assert output["parser_candidate_count"] == 0
    assert output["decision_created"] is False
    assert output["operational_eligible"] is False
    estimates = next(row for row in output["requests"] if row["request_id"] == "avgo-estimates")
    assert estimates["provider_schema_version"] == "alpha-vantage-estimates-array-v1"
    assert estimates["accounting_basis"] == "unknown"
    assert estimates["historical_availability_verified"] is False
    assert estimates["forward_period_candidate_count"] == 3
    assert estimates["parser_candidate"] is False

    run_root = private / "raw" / "alpha_vantage" / run_id
    for name, digest in original_hashes.items():
        assert _sha((run_root / name).read_bytes()) == digest
