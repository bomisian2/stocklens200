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
    compare_v23_with_stocklens,
    load_v23_public_ledger,
)


FIXTURE = Path(__file__).parent / "fixtures" / "v23_public_ledger_minimal.json"
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
