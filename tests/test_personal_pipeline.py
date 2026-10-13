from __future__ import annotations

import json

import pandas as pd

from stocklens.personal.ir_financials import _ttm_values
from stocklens.personal.market_data import AlphaVantagePersonalClient, compute_technical_snapshot


def test_ttm_uses_annual_plus_current_ytd_minus_prior_ytd_and_withholds_eps() -> None:
    annual = {"revenue": 100.0, "operating_income": 20.0, "net_income": 15.0, "cfo": 18.0, "capex": 4.0, "fcf": 14.0}
    current = {"revenue": 90.0, "operating_income": 19.0, "net_income": 14.0, "cfo": 17.0, "capex": 5.0, "fcf": 12.0}
    prior = {"revenue": 70.0, "operating_income": 13.0, "net_income": 10.0, "cfo": 12.0, "capex": 3.0, "fcf": 9.0}

    result = _ttm_values(annual, current, prior)

    assert result["revenue"] == 120.0
    assert result["fcf"] == 17.0
    assert result["diluted_eps"] is None
    assert result["diluted_shares"] is None


def test_technical_snapshot_requires_history_and_does_not_make_signal() -> None:
    frame = pd.DataFrame({"close": [100 + index * 0.1 for index in range(60)]})
    result = compute_technical_snapshot(frame)
    assert result["status"] == "partial"
    assert "ema20" in result and "rsi14" in result
    assert "recommendation" not in result


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "Time Series (Daily)": {
                "2026-10-09": {
                    "1. open": "100",
                    "2. high": "102",
                    "3. low": "99",
                    "4. close": "101",
                    "5. volume": "12345",
                }
            }
        }


class FakeSession:
    def __init__(self) -> None:
        self.params = None

    def get(self, *args, **kwargs):
        self.params = kwargs["params"]
        return FakeResponse()


def test_alpha_vantage_personal_cache_never_stores_api_key(tmp_path) -> None:
    session = FakeSession()
    result = AlphaVantagePersonalClient("top-secret", session=session).collect_daily("amat", tmp_path)

    assert result.status == "verified"
    assert result.as_of == "2026-10-09"
    manifest = (tmp_path / "market" / "AMAT.manifest.json").read_text(encoding="utf-8")
    csv_text = (tmp_path / "market" / "AMAT.csv").read_text(encoding="utf-8")
    assert "top-secret" not in manifest + csv_text
    assert json.loads(manifest)["redistribution_authorized"] is False
    assert session.params["apikey"] == "top-secret"
