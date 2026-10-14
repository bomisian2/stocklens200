from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from stocklens.personal.macro_snapshot import (
    CBOE_VIX_DOWNLOAD_URL,
    FRED_DOWNLOAD_URL,
    OfficialMacroSnapshotError,
    collect_official_macro_snapshot,
    normalize_official_macro_responses,
    parse_cboe_vix_csv,
    parse_fred_csv,
)
from stocklens.personal.market_regime import build_market_regime, load_market_regime_snapshot


FIXTURES = Path(__file__).parent / "fixtures"
FRED = (FIXTURES / "official_macro_fred.csv").read_bytes()
VIX = (FIXTURES / "official_macro_vix.csv").read_bytes()


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, url: str = "") -> None:
        self.content = content
        self.status_code = status_code
        self.url = url
        self.headers = {"Content-Type": "text/csv"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        return self.responses[url]


def test_official_csv_parsers_and_normalization_are_reproducible() -> None:
    fred = parse_fred_csv(FRED)
    observed_on, vix = parse_cboe_vix_csv(VIX)
    observations = normalize_official_macro_responses(
        FRED,
        VIX,
        collected_at="2026-09-09T00:00:00+00:00",
    )

    assert len(fred["DTWEXBGS"]) == 70
    assert observed_on.isoformat() == "2026-09-08"
    assert vix == 16.4
    assert len(observations) == 11
    assert {row["metric_id"] for row in observations} >= {
        "policy_rate_pct",
        "treasury_2s10s_spread_pp",
        "cpi_yoy_trend_3m_pp",
        "pce_yoy_trend_3m_pp",
        "broad_dollar_return_63d_ratio",
        "hy_oas_pct",
        "vix_index",
    }
    assert not any("dxy" in row["metric_id"] for row in observations)
    assert all(row["availability_status"] == "current_snapshot_proxy" for row in observations)
    assert observations == normalize_official_macro_responses(
        FRED,
        VIX,
        collected_at="2026-09-09T00:00:00+00:00",
    )


def test_collector_writes_raw_hashes_and_coverage_above_gate(tmp_path: Path) -> None:
    session = FakeSession(
        {
            FRED_DOWNLOAD_URL: FakeResponse(FRED, url=FRED_DOWNLOAD_URL),
            CBOE_VIX_DOWNLOAD_URL: FakeResponse(VIX, url="https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"),
        }
    )
    result = collect_official_macro_snapshot(
        tmp_path,
        session=session,  # type: ignore[arg-type]
        now=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )

    snapshot_path = tmp_path / "macro" / "market_regime_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert session.calls == [FRED_DOWNLOAD_URL, CBOE_VIX_DOWNLOAD_URL]
    assert result.overall_coverage >= 0.60
    assert result.requests_made == 2
    assert result.http_results["fred"]["http_status"] == 200
    assert result.http_results["cboe_vix"]["final_url"].startswith("https://cdn-api.cboe.com/")
    assert snapshot["operational_eligible"] is False
    assert snapshot["availability_status"] == "current_snapshot_proxy"
    assert snapshot["regime"]["overall_coverage"] == result.overall_coverage
    assert (tmp_path / "macro" / "raw" / f"fred_macro.{result.source_hashes['fred']}.csv").read_bytes() == FRED
    assert (tmp_path / "macro" / "raw" / f"cboe_vix.{result.source_hashes['cboe_vix']}.csv").read_bytes() == VIX
    assert result.source_hashes == {
        "fred": hashlib.sha256(FRED).hexdigest(),
        "cboe_vix": hashlib.sha256(VIX).hexdigest(),
    }


def test_failed_collection_preserves_last_good_snapshot(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "macro" / "market_regime_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    last_good = b'{"status":"last-good"}'
    snapshot_path.write_bytes(last_good)
    session = FakeSession(
        {
            FRED_DOWNLOAD_URL: FakeResponse(FRED, url=FRED_DOWNLOAD_URL),
            CBOE_VIX_DOWNLOAD_URL: FakeResponse(b"", status_code=503, url=CBOE_VIX_DOWNLOAD_URL),
        }
    )

    with pytest.raises(OfficialMacroSnapshotError, match="last-good snapshot preserved"):
        collect_official_macro_snapshot(
            tmp_path,
            session=session,  # type: ignore[arg-type]
            now=datetime(2026, 9, 9, tzinfo=timezone.utc),
        )
    assert snapshot_path.read_bytes() == last_good
    assert not (tmp_path / "macro" / "raw").exists()


def test_duplicate_fred_observation_is_rejected() -> None:
    lines = FRED.decode("utf-8").splitlines()
    duplicated = ("\n".join([*lines, lines[-1]]) + "\n").encode("utf-8")
    with pytest.raises(OfficialMacroSnapshotError, match="Duplicate FRED observation"):
        parse_fred_csv(duplicated)


def test_old_last_good_snapshot_is_preserved_but_marked_stale(tmp_path: Path) -> None:
    observations = normalize_official_macro_responses(
        FRED,
        VIX,
        collected_at="2026-09-09T00:00:00+00:00",
    )
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({"observations": observations}), encoding="utf-8")

    loaded = load_market_regime_snapshot(
        snapshot_path,
        analysis_as_of="2027-01-15T00:00:00+00:00",
    )
    assert loaded["status"] == "proxy_low_confidence"
    assert loaded["accepted_observations"] == []
    assert all(reason.startswith("stale_") for reason in loaded["excluded_observations"].values())


def test_future_as_of_is_not_accepted_as_current_snapshot() -> None:
    observations = normalize_official_macro_responses(
        FRED,
        VIX,
        collected_at="2026-08-01T00:00:00+00:00",
    )
    regime = build_market_regime(
        observations,
        analysis_as_of="2026-08-01T00:00:00+00:00",
    )
    assert regime["overall_coverage"] < 0.60
    assert "future_as_of_blocked" in set(regime["excluded_observations"].values())


def test_redirect_outside_approved_domains_is_blocked(tmp_path: Path) -> None:
    session = FakeSession(
        {
            FRED_DOWNLOAD_URL: FakeResponse(FRED, url="https://example.invalid/fred.csv"),
            CBOE_VIX_DOWNLOAD_URL: FakeResponse(VIX, url=CBOE_VIX_DOWNLOAD_URL),
        }
    )
    with pytest.raises(OfficialMacroSnapshotError, match="outside the approved official domains"):
        collect_official_macro_snapshot(
            tmp_path,
            session=session,  # type: ignore[arg-type]
            now=datetime(2026, 9, 9, tzinfo=timezone.utc),
        )
    assert session.calls == [FRED_DOWNLOAD_URL]
