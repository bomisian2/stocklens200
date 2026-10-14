from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.v23_adapter import (
    APPROVED_LEDGER_SHA256,
    EXPECTED_HISTORICAL_ROWS,
    REQUIRED_EVIDENCE_KEYS,
    V23LedgerError,
    build_self_history_market_premium,
    compare_v23_with_stocklens,
    load_v23_public_ledger,
)


FIXTURE = Path(__file__).parent / "fixtures" / "v23_public_ledger_minimal.json"
SELF_HISTORY_FIXTURE = Path(__file__).parent / "fixtures" / "v23_self_history_series.json"
LOCAL_APPROVED_LEDGER = Path(r"D:\GPT\v23-audit-readonly-approved-20261014\data\public-ledger.json")


def _fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _expanded_payload() -> dict:
    payload = _fixture_payload()
    for ticker, count in EXPECTED_HISTORICAL_ROWS.items():
        seed = payload["tickers"][ticker]["historicalPer"][0]
        payload["tickers"][ticker]["historicalPer"] = [copy.deepcopy(seed) for _ in range(count)]
    return payload


def _write_payload(path: Path, payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _research_field(value, *, as_of: str, available_at: str | None = None) -> dict:
    return {
        "value": value,
        "source": "test fixture",
        "source_hash": "f" * 64,
        "as_of": as_of,
        "available_at": available_at or as_of,
        "unit": "test",
        "calculation_basis": "test fixture only",
        "status": "reconstructed_pit_research_only",
        "operational_eligible": False,
    }


def _self_history_record(
    ticker: str,
    rows: list[tuple[str, float]],
    *,
    as_of: str,
    eps: float | None,
    current_per: float,
    reported_percentile: float = 50.0,
) -> dict:
    return {
        "ticker": ticker,
        "ledger_sha256": "a" * 64,
        "current": {
            "price": _research_field(100.0, as_of=as_of),
            "ttm_eps": _research_field(eps, as_of=as_of, available_at=as_of),
            "ttm_per": _research_field(current_per, as_of=as_of),
            "reported_current_percentile": _research_field(reported_percentile, as_of=as_of),
        },
        "historical_per": [
            {
                "filing_date": filing_date,
                "ttm_per": _research_field(value, as_of=filing_date),
            }
            for filing_date, value in rows
        ],
    }


def _approved_series_record(ticker: str) -> dict:
    fixture = json.loads(SELF_HISTORY_FIXTURE.read_text(encoding="utf-8"))[ticker]
    return _self_history_record(
        ticker,
        [tuple(row) for row in fixture["rows"]],
        as_of=fixture["as_of"],
        eps=fixture["eps"],
        current_per=fixture["current_per"],
        reported_percentile=fixture["reported_percentile"],
    )


def test_adapter_validates_hash_schema_counts_and_never_mutates_original(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    expected_hash = _write_payload(path, _expanded_payload())
    before = path.read_bytes()

    result = load_v23_public_ledger(path, expected_sha256=expected_hash)

    assert path.read_bytes() == before
    assert result["ledger_sha256"] == expected_hash
    assert result["operational_eligible"] is False
    assert result["tickers"]["AVGO"]["historical_row_count"] == 21
    assert result["tickers"]["NVDA"]["historical_row_count"] == 36
    for ticker in ("AVGO", "NVDA"):
        record = result["tickers"][ticker]
        assert record["status"] == "reconstructed_pit_research_only"
        assert record["operational_eligible"] is False
        assert record["self_history"]["status"] == "reconstructed_pit_research_only"
        assert record["self_history"]["operational_eligible"] is False
        assert record["self_history"]["sample_gate_passed"] is True
        for value in record["current"].values():
            assert REQUIRED_EVIDENCE_KEYS <= value.keys()
            assert value["operational_eligible"] is False
        for row in record["historical_per"]:
            for value in (row["price"], row["ttm_eps"], row["ttm_per"], row["history_percentile"]):
                assert REQUIRED_EVIDENCE_KEYS <= value.keys()
                assert value["operational_eligible"] is False


def test_adapter_rejects_wrong_hash_and_future_price(tmp_path) -> None:
    payload = _expanded_payload()
    path = tmp_path / "ledger.json"
    actual_hash = _write_payload(path, payload)
    with pytest.raises(V23LedgerError, match="SHA-256"):
        load_v23_public_ledger(path, expected_sha256="0" * 64)

    payload["tickers"]["AVGO"]["historicalPer"][0]["priceDate"] = "2026-09-11"
    future_hash = _write_payload(path, payload)
    assert future_hash != actual_hash
    with pytest.raises(V23LedgerError, match="future date"):
        load_v23_public_ledger(path, expected_sha256=future_hash)


def test_missing_acceptance_timestamp_and_null_fcf_fields_remain_unavailable(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    expected_hash = _write_payload(path, _expanded_payload())
    result = load_v23_public_ledger(path, expected_sha256=expected_hash)

    for ticker in ("AVGO", "NVDA"):
        current = result["tickers"][ticker]["current"]
        assert current["acceptance_timestamp"]["value"] is None
        assert current["acceptance_timestamp"]["status"] == "unavailable"
        assert current["fcf_yield"]["value"] is None
        assert current["fcf_yield"]["status"] == "unavailable"
    assert result["tickers"]["NVDA"]["current"]["latest_reported_fcf"]["value"] is None
    assert result["tickers"]["NVDA"]["current"]["latest_reported_fcf"]["status"] == "unavailable"


def test_comparison_uses_exact_date_only_and_preserves_separate_definitions(tmp_path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger_hash = _write_payload(ledger_path, _expanded_payload())
    record = load_v23_public_ledger(ledger_path, expected_sha256=ledger_hash)["tickers"]["AVGO"]
    market_path = tmp_path / "AVGO.csv"
    market_path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-09-11,360,363,359,361.99,1000000\n",
        encoding="utf-8",
    )
    result = compare_v23_with_stocklens(record, market_path=market_path)

    assert result["comparison_status"] == "exact_date_match"
    assert result["date_gap_days"] == 0
    assert result["exact_date_price_difference"]["value"] == pytest.approx(0.000009765625)
    assert result["stocklens"]["ttm_eps"]["value"] is None
    assert result["stocklens"]["ttm_per"]["value"] is None
    assert result["v23"]["ttm_eps"]["value"] == pytest.approx(7.84)
    assert result["v23"]["ttm_per"]["value"] == pytest.approx(46.1721926319356)
    assert result["normal_interest_range"] is None
    assert result["operational_eligible"] is False


def test_date_mismatch_is_reported_without_a_synthetic_comparison(tmp_path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger_hash = _write_payload(ledger_path, _expanded_payload())
    record = load_v23_public_ledger(ledger_path, expected_sha256=ledger_hash)["tickers"]["AVGO"]
    market_path = tmp_path / "AVGO.csv"
    market_path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-09-14,364,365,360,362,1000000\n",
        encoding="utf-8",
    )
    result = compare_v23_with_stocklens(record, market_path=market_path)

    assert result["comparison_status"] == "date_mismatch"
    assert result["date_gap_days"] == 3
    assert result["exact_date_price_difference"]["value"] is None
    assert result["exact_date_price_difference"]["status"] == "unavailable"
    assert result["stocklens"]["price"]["as_of"] == "2026-09-14"
    assert result["v23"]["price"]["as_of"] == "2026-09-11"
    assert result["normal_interest_range"] is None


def test_actual_avgo_and_nvda_self_history_percentiles_are_fixed() -> None:
    avgo = build_self_history_market_premium(_approved_series_record("AVGO"))
    nvda = build_self_history_market_premium(_approved_series_record("NVDA"))

    assert avgo["sample_count_total"] == 21
    assert avgo["sample_count_used"] == 20
    assert avgo["excluded_count"] == 1
    assert avgo["full_history_percentiles"]["P25"] == pytest.approx(26.298558620355344)
    assert avgo["full_history_percentiles"]["P35"] == pytest.approx(31.431997604535702)
    assert avgo["full_history_percentiles"]["P50"] == pytest.approx(39.41570225551989)
    assert avgo["full_history_percentiles"]["P70"] == pytest.approx(48.366919858193505)
    assert avgo["full_history_percentiles"]["P85"] == pytest.approx(66.89305095236136)
    assert avgo["normal_interest_candidate"]["low"] == pytest.approx(246.4268612195599)
    assert avgo["normal_interest_candidate"]["high"] == pytest.approx(309.01910568327594)
    assert avgo["current_percentile"] == pytest.approx(65.0)
    assert avgo["current_percentile_crosscheck"]["delta_percentage_points"] == pytest.approx(0.0)
    assert avgo["current_location"] == "neutral_hold_candidate"

    assert nvda["sample_count_total"] == 36
    assert nvda["sample_count_used"] == 31
    assert nvda["excluded_count"] == 5
    assert nvda["full_history_percentiles"]["P25"] == pytest.approx(33.90222235181131)
    assert nvda["full_history_percentiles"]["P35"] == pytest.approx(39.25852144282254)
    assert nvda["full_history_percentiles"]["P50"] == pytest.approx(44.653060809284646)
    assert nvda["full_history_percentiles"]["P70"] == pytest.approx(53.65473169195072)
    assert nvda["full_history_percentiles"]["P85"] == pytest.approx(67.74243726267972)
    assert nvda["normal_interest_candidate"]["low"] == pytest.approx(310.5349046127263)
    assert nvda["normal_interest_candidate"]["high"] == pytest.approx(353.20571100144156)
    assert nvda["current_percentile"] == pytest.approx(16.129032258064516)
    assert nvda["current_percentile_crosscheck"]["delta_percentage_points"] == pytest.approx(0.0)
    assert nvda["current_location"] == "below_aggressive_interest_candidate"

    for result in (avgo, nvda):
        assert result["status"] == "reconstructed_pit_research_only"
        assert result["operational_eligible"] is False
        assert result["recent_5y_percentiles"]["sample_gate_passed"] is False
        for key in (
            "aggressive_interest_candidate",
            "normal_interest_candidate",
            "neutral_hold_candidate",
            "high_expectation_candidate",
        ):
            assert result[key]["operational_eligible"] is False


def test_self_history_prevents_future_rows_and_ignores_dcf_fields() -> None:
    record = _approved_series_record("AVGO")
    baseline = build_self_history_market_premium(record)
    future_row = {
        "filing_date": "2027-01-01",
        "ttm_per": _research_field(9999.0, as_of="2027-01-01"),
    }
    modified = copy.deepcopy(record)
    modified["historical_per"].append(future_row)
    modified["dcf_stress_value"] = 1.0
    modified["discount_rate"] = 0.99
    modified_result = build_self_history_market_premium(modified, as_of="2026-09-11")

    assert modified_result == baseline


def test_self_history_linear_quantiles_and_exclusions_are_explicit() -> None:
    rows = [(f"2025-01-{index:02d}", float(index)) for index in range(1, 21)]
    rows.extend([
        ("2025-02-01", 0.0),
        ("2025-02-02", -1.0),
        ("2025-02-03", float("nan")),
        ("2025-02-04", float("inf")),
        ("2025-02-05", 1000.0),
    ])
    result = build_self_history_market_premium(
        _self_history_record("TEST", rows, as_of="2026-01-01", eps=2.0, current_per=10.0),
    )

    assert result["sample_count_total"] == 25
    assert result["sample_count_used"] == 20
    assert result["excluded_count"] == 5
    assert result["exclusion_policy"]["invalid_count"] == 4
    assert result["exclusion_policy"]["outlier_count"] == 1
    assert result["full_history_percentiles"]["P25"] == pytest.approx(5.75)
    assert result["full_history_percentiles"]["P35"] == pytest.approx(7.65)
    assert result["full_history_percentiles"]["P50"] == pytest.approx(10.5)
    assert result["normal_interest_candidate"] == {
        "low": pytest.approx(15.3),
        "high": pytest.approx(21.0),
        "unit": "USD per share",
        "basis_percentiles": ["P35", "P50"],
        "status": "reconstructed_pit_research_only",
        "operational_eligible": False,
    }


def test_self_history_blocks_small_samples_and_null_eps_without_low_substitute() -> None:
    rows = [(f"2025-01-{index:02d}", float(index + 10)) for index in range(1, 20)]
    small = build_self_history_market_premium(
        _self_history_record("TEST", rows, as_of="2026-01-01", eps=2.0, current_per=20.0),
    )
    assert small["status"] == "blocked_insufficient_sample"
    assert small["sample_gate_passed"] is False
    assert small["normal_interest_candidate"]["low"] is None
    assert small["normal_interest_candidate"]["high"] is None

    enough_rows = [(f"2025-01-{index:02d}", float(index + 10)) for index in range(1, 21)]
    missing_eps = build_self_history_market_premium(
        _self_history_record("TEST", enough_rows, as_of="2026-01-01", eps=None, current_per=20.0),
    )
    assert missing_eps["sample_gate_passed"] is True
    for key in (
        "aggressive_interest_candidate",
        "normal_interest_candidate",
        "neutral_hold_candidate",
        "high_expectation_candidate",
    ):
        assert missing_eps[key]["low"] is None
        assert missing_eps[key]["high"] is None
        assert missing_eps[key]["status"] == "unavailable"
        assert missing_eps[key]["operational_eligible"] is False


@pytest.mark.skipif(not LOCAL_APPROVED_LEDGER.is_file(), reason="approved external ledger is local-only")
def test_approved_external_ledger_matches_pinned_hash_and_row_counts() -> None:
    before_hash = hashlib.sha256(LOCAL_APPROVED_LEDGER.read_bytes()).hexdigest()
    result = load_v23_public_ledger(LOCAL_APPROVED_LEDGER)
    after_hash = hashlib.sha256(LOCAL_APPROVED_LEDGER.read_bytes()).hexdigest()

    assert before_hash == APPROVED_LEDGER_SHA256 == after_hash
    assert result["tickers"]["AVGO"]["historical_row_count"] == 21
    assert result["tickers"]["NVDA"]["historical_row_count"] == 36
    assert result["tickers"]["AVGO"]["historical_period"] == {
        "start": "2020-12-18",
        "end": "2026-09-10",
    }
    assert result["tickers"]["NVDA"]["historical_period"] == {
        "start": "2016-03-17",
        "end": "2026-08-26",
    }
