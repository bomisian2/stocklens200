from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from stocklens.personal.market_regime import (
    MarketRegimeError,
    build_market_regime,
    build_qqq_proxy_observations,
    build_regime_adjusted_candidate,
    load_market_regime_snapshot,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _self_history() -> dict:
    return {
        "source_hash": "9" * 64,
        "as_of": "2026-09-11",
        "available_at": "2026-09-11",
        "sample_gate_passed": True,
        "current_ttm_eps": 10.0,
        "full_history_percentiles": {
            "P25": 20.0,
            "P35": 25.0,
            "P50": 30.0,
            "P70": 40.0,
        },
    }


@pytest.mark.parametrize(
    ("fixture_name", "economy", "inflation", "risk", "percentiles"),
    [
        ("market_regime_favorable.json", "expansion", "stable_disinflation", "risk_on", ["P50", "P70"]),
        ("market_regime_neutral.json", "neutral_mixed", "neutral_mixed", "neutral", ["P35", "P50"]),
        ("market_regime_adverse.json", "slowdown", "inflationary", "risk_off", ["P25", "P35"]),
    ],
)
def test_fixture_regimes_and_adjusted_bands(
    fixture_name: str,
    economy: str,
    inflation: str,
    risk: str,
    percentiles: list[str],
) -> None:
    payload = _load(fixture_name)
    regime = build_market_regime(payload["observations"], analysis_as_of=payload["analysis_as_of"])
    candidate = build_regime_adjusted_candidate(_self_history(), regime)

    assert regime["economy_regime"]["regime"] == economy
    assert regime["inflation_regime"]["regime"] == inflation
    assert regime["risk_regime"]["regime"] == risk
    assert regime["overall_coverage"] >= 0.60
    assert candidate["basis_percentiles"] == percentiles
    assert candidate["operational_eligible"] is False


def test_deterministic_result_for_same_snapshot() -> None:
    payload = _load("market_regime_favorable.json")
    first = build_market_regime(payload["observations"], analysis_as_of=payload["analysis_as_of"])
    second = build_market_regime(payload["observations"], analysis_as_of=payload["analysis_as_of"])
    assert first == second


def test_future_available_observation_is_excluded_without_lookahead() -> None:
    payload = _load("market_regime_favorable.json")
    payload["observations"][0]["available_at"] = "2026-10-15T00:00:00+00:00"
    regime = build_market_regime(payload["observations"], analysis_as_of=payload["analysis_as_of"])
    assert regime["excluded_observations"]["policy_rate_pct"] == "look_ahead_blocked"
    assert "policy_rate_pct" in regime["economy_regime"]["missing_inputs"]


def test_stale_observation_is_excluded() -> None:
    payload = _load("market_regime_favorable.json")
    vix = next(row for row in payload["observations"] if row["metric_id"] == "vix_index")
    vix["as_of"] = "2026-09-01"
    vix["available_at"] = "2026-09-01T22:00:00+00:00"
    regime = build_market_regime(payload["observations"], analysis_as_of=payload["analysis_as_of"])
    assert regime["excluded_observations"]["vix_index"].startswith("stale_")


def test_invalid_unit_and_missing_hash_are_rejected() -> None:
    payload = _load("market_regime_favorable.json")
    wrong_unit = deepcopy(payload["observations"])
    wrong_unit[0]["unit"] = "ratio"
    with pytest.raises(MarketRegimeError, match="unit must be"):
        build_market_regime(wrong_unit, analysis_as_of=payload["analysis_as_of"])

    missing_hash = deepcopy(payload["observations"])
    del missing_hash[0]["source_hash"]
    with pytest.raises(MarketRegimeError, match="missing fields"):
        build_market_regime(missing_hash, analysis_as_of=payload["analysis_as_of"])


def test_missing_inputs_use_proxy_low_confidence_and_keep_p35_p50() -> None:
    regime = build_market_regime([], analysis_as_of="2026-10-14T00:00:00+00:00")
    candidate = build_regime_adjusted_candidate(_self_history(), regime)
    assert regime["status"] == "proxy_low_confidence"
    assert candidate["status"] == "proxy_low_confidence"
    assert candidate["basis_percentiles"] == ["P35", "P50"]
    assert candidate["low"] == 250.0
    assert candidate["high"] == 300.0


def test_regime_candidate_does_not_mutate_self_history_or_hash() -> None:
    payload = _load("market_regime_adverse.json")
    regime = build_market_regime(payload["observations"], analysis_as_of=payload["analysis_as_of"])
    self_history = _self_history()
    before = deepcopy(self_history)
    build_regime_adjusted_candidate(self_history, regime)
    assert self_history == before
    assert self_history["source_hash"] == "9" * 64


def test_qqq_local_proxy_builder_and_loader(tmp_path: Path) -> None:
    dates = pd.bdate_range("2026-05-20", periods=100).strftime("%Y-%m-%d")
    closes = pd.Series([100.0 + index * 0.5 for index in range(100)])
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes + 1,
            "low": closes - 1,
            "close": closes,
            "volume": 1_000_000,
        }
    )
    csv_path = tmp_path / "QQQ.csv"
    frame.to_csv(csv_path, index=False)
    file_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "QQQ.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sha256": file_hash,
                "market_date": dates[-1],
                "collected_at_utc": "2026-10-13T04:24:32+00:00",
            }
        ),
        encoding="utf-8",
    )

    observations = build_qqq_proxy_observations(csv_path, manifest_path)
    assert {row["metric_id"] for row in observations} == {
        "qqq_trend_63d_ratio",
        "qqq_realized_vol_20d_ratio",
        "qqq_drawdown_observed_ratio",
    }
    regime = build_market_regime(observations, analysis_as_of="2026-10-14T00:00:00+00:00")
    assert regime["status"] == "proxy_low_confidence"
    assert regime["risk_regime"]["used_inputs"]
    assert regime["inflation_regime"]["used_inputs"] == []

    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({"observations": observations}), encoding="utf-8")
    loaded = load_market_regime_snapshot(
        snapshot_path,
        analysis_as_of="2026-10-14T00:00:00+00:00",
    )
    assert loaded == regime
