from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from stocklens.personal.analysis import compute_fundamental_indicators
from stocklens.personal.catalog import PILOT_IR_DOCUMENTS
from stocklens.personal.financial_audit import METRIC_KEYS, OFFICIAL_FACT_CONTRACTS, audit_normalized_snapshot
from stocklens.personal.ir_financials import _ttm_values
from stocklens.personal.market_data import AlphaVantagePersonalClient, compute_relative_strength, compute_technical_snapshot


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


def test_technical_snapshot_computes_all_descriptive_indicators_with_252_sessions() -> None:
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=260, freq="B").strftime("%Y-%m-%d"),
        "close": [100 + index * 0.2 for index in range(260)],
        "volume": [1_000_000 + index * 1_000 for index in range(260)],
    })
    result = compute_technical_snapshot(frame)
    assert result["status"] == "verified"
    assert result["ema200"] is not None
    assert result["drawdown_from_52w_high"] is not None
    assert result["volume_trend_20d_vs_prior_20d"] is not None
    assert result["is_buy_signal"] is False


def test_relative_strength_requires_aligned_qqq_history() -> None:
    dates = pd.date_range("2025-01-01", periods=260, freq="B").strftime("%Y-%m-%d")
    stock = pd.DataFrame({"date": dates, "close": [100 + index for index in range(260)]})
    qqq = pd.DataFrame({"date": dates, "close": [100 + index * 0.5 for index in range(260)]})
    result = compute_relative_strength(stock, qqq)
    assert result["status"] == "verified"
    assert result["relative_return_63d"] > 0
    assert result["relative_return_252d"] > 0
    assert result["is_buy_signal"] is False


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
    result = AlphaVantagePersonalClient(
        "top-secret",
        session=session,
        now=datetime(2026, 10, 9, 21, tzinfo=timezone.utc),
    ).collect_daily("amat", tmp_path)

    assert result.status == "verified"
    assert result.as_of == "2026-10-09"
    manifest = (tmp_path / "market" / "AMAT.manifest.json").read_text(encoding="utf-8")
    csv_text = (tmp_path / "market" / "AMAT.csv").read_text(encoding="utf-8")
    assert "top-secret" not in manifest + csv_text
    assert json.loads(manifest)["redistribution_authorized"] is False
    assert session.params["apikey"] == "top-secret"
    assert session.params["outputsize"] == "compact"
    assert json.loads(manifest)["price_policy"] == "raw_as_traded_unadjusted"


def test_fundamental_indicators_do_not_invent_market_valuation() -> None:
    financial = {
        "ticker": "AMAT", "status": "verified",
        "periods": [
            {"kind": "fiscal_year", "metrics": {"revenue": 100.0}},
            {"kind": "ttm", "period_end": "2026-07-26", "metrics": {"revenue": 120.0, "operating_income": 30.0, "fcf": 12.0}},
        ],
        "balance_sheet": {"cash_and_equivalents": 10.0, "short_term_investments": 2.0, "long_term_investments": 3.0, "short_term_debt": 1.0, "long_term_debt": 4.0},
        "guidance": {},
    }
    result = compute_fundamental_indicators(financial)
    assert result["metrics"]["ttm_operating_margin"]["value"] == 0.25
    assert result["metrics"]["known_net_cash"]["value_usd_millions"] == 10.0
    assert result["valuation_inputs"]["market_cap"]["status"] == "unavailable"
    assert result["valuation_inputs"]["fair_value"]["status"] == "hold"


def test_official_fact_contract_catches_no_mismatches() -> None:
    ticker = "AMAT"
    contract = OFFICIAL_FACT_CONTRACTS[ticker]
    payloads = {document.key: f"raw-{document.key}".encode() for document in PILOT_IR_DOCUMENTS[ticker]}
    periods = []
    for label, values in contract["periods"].items():
        periods.append({"label": label, "unit": "USD millions except per-share data", "metrics": dict(zip(METRIC_KEYS, values, strict=True))})
    annual = dict(zip(METRIC_KEYS, contract["periods"]["FY2025"], strict=True))
    current = dict(zip(METRIC_KEYS, contract["periods"]["9M FY2026"], strict=True))
    prior = dict(zip(METRIC_KEYS, contract["prior_ytd"], strict=True))
    periods.append({"label": "TTM through Q3 FY2026", "period_end": contract["ttm_period_end"], "metrics": _ttm_values(annual, current, prior)})
    documents = []
    import hashlib
    for document in PILOT_IR_DOCUMENTS[ticker]:
        raw = payloads[document.key]
        documents.append({"key": document.key, "url": document.url, "published_at": document.published_at, "period_end": document.period_end, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    snapshot = {"ticker": ticker, "periods": periods, "balance_sheet": contract["balance_sheet"], "guidance": contract["guidance"], "documents": documents}
    audit = audit_normalized_snapshot(snapshot, payloads)
    assert audit["status"] == "verified"
    assert audit["checks_passed"] == audit["checks_total"]
