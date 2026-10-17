from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from stocklens.personal.top10_bridge import (
    APPROVED_CONSENSUS_SHA256,
    APPROVED_MACRO_SHA256,
    Top10BridgeError,
    build_forward_consensus_candidate,
    load_forward_consensus_snapshot,
    load_market_environment_proxy,
)
from stocklens.personal.v23_adapter import build_self_history_market_premium


SERIES_FIXTURE = Path(__file__).parent / "fixtures" / "v23_self_history_series.json"


def _write(path: Path, payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest().upper()


def _consensus_payload() -> dict:
    return {
        "generatedAt": "2026-10-14T12:52:02.418Z",
        "source": "Nasdaq Analyst Research earnings forecast API",
        "methodology": "annual aggregate; separate from guidance and actuals",
        "records": {
            "AVGO": {
                "ticker": "AVGO", "collectedAt": "2026-10-14T12:52:02.418Z",
                "fiscalEnd": "Oct 2026", "consensusEps": 10.37, "highEps": 12.38,
                "lowEps": 10.06, "analystCount": 13, "revisionsUp4w": 1,
                "revisionsDown4w": 0, "revisionState": "상향 우세",
                "nextFiscalEnd": "Oct 2027", "nextConsensusEps": 17.45,
                "sourceUrl": "https://api.nasdaq.com/api/analyst/AVGO/earnings-forecast",
            },
            "NVDA": {
                "ticker": "NVDA", "collectedAt": "2026-10-14T12:52:02.418Z",
                "fiscalEnd": "Jan 2027", "consensusEps": 9.25, "highEps": 9.71,
                "lowEps": 9.01, "analystCount": 17, "revisionsUp4w": 1,
                "revisionsDown4w": 0, "revisionState": "상향 우세",
                "nextFiscalEnd": "Jan 2028", "nextConsensusEps": 15.53,
                "sourceUrl": "https://api.nasdaq.com/api/analyst/NVDA/earnings-forecast",
            },
        },
    }


def _macro_payload() -> dict:
    values = {
        "tenYear": (5.22, -0.05, 0.22, 5.17, 4.91, True, True, "%"),
        "oil": (94.7, 5.88, -10.52, 93.47, 89.67, True, True, "USD"),
        "vix": (15.27, 1.73, -11.22, 15.46, 15.45, False, False, "pt"),
        "qqq": (743.57, -2.12, 5.54, 741.34, 726.55, True, True, "USD"),
        "rsp": (213.3, 0.46, -0.31, 211.37, 216.18, True, False, "USD"),
        "hyg": (77.09, -0.23, -1.65, 77.65, 78.7, False, False, "USD"),
    }
    return {
        "generatedAt": "2026-10-13T22:35:27.276Z",
        "state": "중립", "score": 3, "interpretation": "종목 조건 우선",
        "methodology": "six confirmed daily market proxies",
        "indicators": {
            key: {
                "label": key, "asOf": "2026-10-13", "value": row[0],
                "change5": row[1], "change20": row[2], "ma20": row[3],
                "ma50": row[4], "above20": row[5], "above50": row[6],
                "unit": row[7], "source": "Yahoo Finance 확정 일봉",
            }
            for key, row in values.items()
        },
    }


def _self_history(ticker: str) -> tuple[dict, float, str]:
    payload = json.loads(SERIES_FIXTURE.read_text(encoding="utf-8"))[ticker]
    price = payload["eps"] * payload["current_per"]
    record = {
        "ticker": ticker,
        "ledger_sha256": "a" * 64,
        "historical_per": [
            {"filing_date": row[0], "ttm_per": {"value": row[1]}}
            for row in payload["rows"]
        ],
        "current": {
            "price": {"value": price, "as_of": payload["as_of"], "available_at": payload["as_of"]},
            "ttm_eps": {"value": payload["eps"], "available_at": payload["as_of"]},
            "ttm_per": {"value": payload["current_per"]},
            "reported_current_percentile": {"value": payload["reported_percentile"]},
        },
    }
    return build_self_history_market_premium(record), price, payload["as_of"]


def test_approved_hash_constants_are_pinned() -> None:
    assert APPROVED_MACRO_SHA256 == "662310097AE7C2C5F56B7A563E65B3A5C3FDB84D772FFEA59F5093F59AA75FAE"
    assert APPROVED_CONSENSUS_SHA256 == "BCFBF53FCDB7891166A714C490C48EA4B91D2B27B935768A137A95B3C31AD478"


def test_market_proxy_exact_values_and_separate_status(tmp_path: Path) -> None:
    path = tmp_path / "macro.json"
    digest = _write(path, _macro_payload())
    before = path.read_bytes()
    result = load_market_environment_proxy(path, expected_sha256=digest)

    assert path.read_bytes() == before
    assert result["state"] == "중립"
    assert result["indicators"]["tenYear"]["value"] == 5.22
    assert result["indicators"]["qqq"]["change20"] == 5.54
    assert result["status"] == "cross_project_market_proxy"
    assert result["operational_eligible"] is False
    assert result["per_band_policy"] == "no automatic PER-band change"
    assert len(result["missing_official_inputs"]) == 3
    assert any("신용 위험선호 약화" in row for row in result["rule_trace"])


def test_snapshot_missing_schema_and_hash_change_are_blocked_or_reread(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(Top10BridgeError, match="does not exist"):
        load_forward_consensus_snapshot(missing)

    path = tmp_path / "consensus.json"
    old_hash = _write(path, _consensus_payload())
    first = load_forward_consensus_snapshot(path, expected_sha256=old_hash)
    changed = _consensus_payload()
    changed["records"]["AVGO"]["consensusEps"] = 10.5
    new_hash = _write(path, changed)
    with pytest.raises(Top10BridgeError, match="SHA-256"):
        load_forward_consensus_snapshot(path, expected_sha256=old_hash)
    second = load_forward_consensus_snapshot(path, expected_sha256=new_hash)
    assert first["source_hash"] != second["source_hash"]
    assert second["tickers"]["AVGO"]["eps_base"] == 10.5

    invalid = _consensus_payload()
    del invalid["records"]["NVDA"]["analystCount"]
    invalid_hash = _write(path, invalid)
    with pytest.raises(Top10BridgeError, match="analystCount"):
        load_forward_consensus_snapshot(path, expected_sha256=invalid_hash)


@pytest.mark.parametrize(
    ("ticker", "base_eps", "low_eps", "high_eps", "analysts", "next_eps"),
    [
        ("AVGO", 10.37, 10.06, 12.38, 13, 17.45),
        ("NVDA", 9.25, 9.01, 9.71, 17, 15.53),
    ],
)
def test_forward_candidate_exact_values_dates_and_ttm_separation(
    tmp_path: Path,
    ticker: str,
    base_eps: float,
    low_eps: float,
    high_eps: float,
    analysts: int,
    next_eps: float,
) -> None:
    path = tmp_path / "consensus.json"
    digest = _write(path, _consensus_payload())
    record = load_forward_consensus_snapshot(path, expected_sha256=digest)["tickers"][ticker]
    self_history, price, price_as_of = _self_history(ticker)
    baseline = copy.deepcopy(self_history)

    result = build_forward_consensus_candidate(
        record,
        current_price=price,
        current_price_as_of=price_as_of,
        self_history=self_history,
    )

    assert self_history == baseline
    assert result["eps"] == {"low": low_eps, "base": base_eps, "high": high_eps}
    assert result["analyst_count"] == analysts
    assert result["next_consensus_eps"] == next_eps
    assert result["forward_per"]["base"] == pytest.approx(price / base_eps)
    assert result["range_low"] == pytest.approx(low_eps * baseline["full_history_percentiles"]["P35"])
    assert result["range_high"] == pytest.approx(high_eps * baseline["full_history_percentiles"]["P50"])
    assert len(result["matrix"]) == 6
    assert result["price_as_of"] == "2026-09-11"
    assert result["consensus_collected_at"] == "2026-10-14T12:52:02.418Z"
    assert result["basis"] == "unverified_street_basis"
    assert result["status"] == "forward_consensus_candidate"
    assert result["operational_eligible"] is False
    assert "ttm" not in result["calculation_basis"].lower()


def test_forward_candidate_rejects_basis_mixing(tmp_path: Path) -> None:
    path = tmp_path / "consensus.json"
    digest = _write(path, _consensus_payload())
    record = load_forward_consensus_snapshot(path, expected_sha256=digest)["tickers"]["AVGO"]
    record["basis"] = "company_guidance"
    history, price, price_as_of = _self_history("AVGO")
    with pytest.raises(Top10BridgeError, match="unverified_street_basis"):
        build_forward_consensus_candidate(
            record,
            current_price=price,
            current_price_as_of=price_as_of,
            self_history=history,
        )
