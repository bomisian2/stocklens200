from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from stocklens.personal.analysis import compute_fundamental_indicators, compute_personal_analysis
from stocklens.personal.catalog import PILOT_IR_DOCUMENTS
from stocklens.personal.financial_audit import METRIC_KEYS, OFFICIAL_FACT_CONTRACTS, audit_normalized_snapshot
from stocklens.personal.ir_financials import _ttm_values, collect_pilot_financials
from stocklens.personal.market_audit import audit_saved_market_data
from stocklens.personal.market_data import (
    AlphaVantagePersonalClient,
    compute_relative_strength,
    compute_technical_snapshot,
    diagnose_provider_payload,
)


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


def test_provider_diagnostics_are_safe_and_specific() -> None:
    assert diagnose_provider_payload({"Note": "API call frequency and rate limit reached"}) == (
        "rate_limit", True, "Note"
    )
    assert diagnose_provider_payload({"Information": "This endpoint is premium"}) == (
        "entitlement_required", False, "Information"
    )
    assert diagnose_provider_payload({"Error Message": "Invalid API call for this symbol"}) == (
        "invalid_request_or_symbol", False, "Error Message"
    )


class DiagnosticResponse:
    status_code = 200

    def json(self):
        return {"Note": "API call frequency and rate limit reached; secret response details"}


class DiagnosticSession:
    def get(self, *args, **kwargs):
        return DiagnosticResponse()


def test_blocked_market_call_preserves_prior_cache_and_does_not_store_provider_body(tmp_path) -> None:
    market_dir = tmp_path / "market"
    market_dir.mkdir(parents=True)
    previous = "date,open,high,low,close,volume\n2026-10-09,1,2,1,2,3\n"
    (market_dir / "AVGO.csv").write_text(previous, encoding="utf-8")

    result = AlphaVantagePersonalClient("not-persisted", session=DiagnosticSession()).collect_daily("AVGO", tmp_path)

    assert result.status == "blocked"
    assert result.diagnostic_code == "rate_limit"
    assert result.retryable is True
    assert "secret response details" not in result.message
    assert (market_dir / "AVGO.csv").read_text(encoding="utf-8") == previous


class HttpFailureResponse:
    status_code = 403
    content = b"withheld"


class HttpFailureSession:
    def get(self, *args, **kwargs):
        return HttpFailureResponse()


def test_failed_financial_refresh_preserves_last_good_snapshots(tmp_path) -> None:
    financial_dir = tmp_path / "normalized" / "financials"
    financial_dir.mkdir(parents=True)
    before = {}
    for ticker in PILOT_IR_DOCUMENTS:
        payload = json.dumps({"ticker": ticker, "status": "verified", "sentinel": "last-good"})
        path = financial_dir / f"{ticker}.json"
        path.write_text(payload, encoding="utf-8")
        before[ticker] = path.read_bytes()

    result = collect_pilot_financials(tmp_path, session=HttpFailureSession())

    assert result["financial_tickers_verified"] == 0
    assert result["financial_tickers_available"] == 3
    for ticker in PILOT_IR_DOCUMENTS:
        assert (financial_dir / f"{ticker}.json").read_bytes() == before[ticker]


def test_local_market_audit_makes_no_network_claim_and_labels_legacy_failures(tmp_path) -> None:
    import hashlib

    market_dir = tmp_path / "market"
    market_dir.mkdir(parents=True)
    for ticker in ("AMAT", "QQQ"):
        csv_payload = b"date,open,high,low,close,volume\n2026-10-09,100,102,99,101,12345\n"
        (market_dir / f"{ticker}.csv").write_bytes(csv_payload)
        (market_dir / f"{ticker}.manifest.json").write_text(
            json.dumps(
                {
                    "market_date": "2026-10-09",
                    "freshness_status": "verified",
                    "price_policy": "raw_as_traded_unadjusted",
                    "adjusted_prices_available": False,
                    "sha256": hashlib.sha256(csv_payload).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "market": {
                    "results": [
                        {"ticker": "AMAT", "status": "verified"},
                        {"ticker": "AVGO", "status": "blocked", "message": "old generic message"},
                        {"ticker": "QCOM", "status": "blocked", "message": "old generic message"},
                        {"ticker": "QQQ", "status": "verified"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    audit = audit_saved_market_data(tmp_path)

    assert audit["network_calls_made"] == 0
    rows = {row["ticker"]: row for row in audit["results"]}
    assert rows["AMAT"]["data_status"] == "verified"
    assert rows["QQQ"]["data_status"] == "verified"
    assert rows["AVGO"]["diagnostic_code"] == "legacy_unclassified_response"
    assert rows["QCOM"]["diagnostic_code"] == "legacy_unclassified_response"


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


def test_integrated_analysis_keeps_research_references_out_of_operational_decision() -> None:
    dates = pd.bdate_range("2026-05-20", periods=100).strftime("%Y-%m-%d")
    closes = pd.Series([400 + index for index in range(100)], dtype=float)
    market = pd.DataFrame({
        "date": dates, "open": closes - 1, "high": closes + 2,
        "low": closes - 2, "close": closes, "volume": [1_000_000 + index for index in range(100)],
    })
    benchmark = market.assign(close=pd.Series([500 + index * 0.5 for index in range(100)]))
    financial = {
        "ticker": "AMAT", "status": "verified",
        "periods": [
            {"kind": "fiscal_year", "label": "FY2025", "metrics": {"revenue": 100.0, "diluted_eps": 8.0}},
            {"kind": "quarter", "label": "Q3 FY2026", "metrics": {"diluted_shares": 800.0}},
            {"kind": "ttm", "period_end": "2026-07-26", "metrics": {"revenue": 120.0, "operating_income": 30.0, "fcf": 12.0}},
        ],
        "balance_sheet": {
            "cash_and_equivalents": 10.0, "short_term_investments": 2.0,
            "long_term_investments": 3.0, "short_term_debt": 1.0, "long_term_debt": 4.0,
        },
        "guidance": {},
    }

    result = compute_personal_analysis(
        financial, market, benchmark,
        {"freshness_status": "verified", "market_date": dates[-1], "price_policy": "raw_as_traded_unadjusted"},
    )

    references = result["valuation_references"]
    assert references["status"] == "research_only"
    assert references["proxy_equity_value_usd_millions"] == 499.0 * 800.0
    assert references["formal_market_cap"] is None
    assert references["share_basis_as_of"] is None
    assert "weighted-average" in references["share_basis"]
    assert references["formal_per"] is None
    assert references["fair_value"] is None
    assert references["reverse_dcf"]["status"] == "underdetermined"
    assert result["signal_research"]["independent_validated_strategies"] == 0
    assert result["decision"] == {
        "status": "hold",
        "is_buy_signal": False,
        "reason": "Technical indicators are descriptive; fair value and independently validated signals are unavailable",
    }


def test_issuer_guidance_stays_separate_by_accounting_basis() -> None:
    base_periods = [
        {"kind": "fiscal_year", "label": "FY2025", "metrics": {"revenue": 100.0, "diluted_eps": 2.0}},
        {"kind": "quarter", "label": "Q3 FY2026", "period_end": "2026-07-26", "metrics": {"revenue": 50.0, "diluted_shares": 10.0}},
        {"kind": "ttm", "period_end": "2026-07-26", "metrics": {"revenue": 120.0, "operating_income": 30.0, "fcf": 12.0}},
    ]
    financial = {
        "ticker": "AMAT", "status": "verified", "periods": base_periods,
        "balance_sheet": {"cash_and_equivalents": 1.0, "short_term_investments": 0.0, "long_term_investments": 0.0, "short_term_debt": 0.0, "long_term_debt": 0.0},
        "guidance": {"period": "Q4 FY2026", "revenue_midpoint": 60.0, "revenue_plus_minus": 5.0, "non_gaap_eps_midpoint": 3.0, "non_gaap_eps_plus_minus": 0.2},
        "documents": [],
    }

    result = compute_fundamental_indicators(financial)["guidance_analysis"]

    assert abs(result["comparison"]["midpoint"] - 0.2) < 1e-12
    assert result["rows"][1]["basis"] == "issuer_guidance_non_gaap"
    assert "혼합하지 않음" in result["accounting_warning"]


def test_financial_only_analysis_for_avgo_and_qcom_does_not_require_price() -> None:
    for ticker, guidance in (
        ("AVGO", {"period": "Q4 FY2026", "revenue_approx": 140.0, "non_gaap_operating_margin_percent": 66.0}),
        ("QCOM", {"period": "Q4 FY2026", "revenue_low": 95.0, "revenue_high": 105.0, "gaap_eps_low": 1.2, "gaap_eps_high": 1.4}),
    ):
        financial = {
            "ticker": ticker, "status": "verified",
            "periods": [
                {"kind": "fiscal_year", "label": "FY2025", "metrics": {"revenue": 100.0, "diluted_eps": 2.0}},
                {"kind": "quarter", "label": "Q3 FY2026", "period_end": "2026-07-26", "metrics": {"revenue": 100.0, "diluted_shares": 10.0}},
                {"kind": "ttm", "period_end": "2026-07-26", "metrics": {"revenue": 120.0, "operating_income": 30.0, "fcf": 12.0}},
            ],
            "balance_sheet": {"cash_and_equivalents": 1.0, "short_term_debt": 0.0, "long_term_debt": 0.0},
            "guidance": guidance, "documents": [],
        }

        result = compute_personal_analysis(financial)

        assert result["fundamental"]["status"] == "verified"
        assert result["valuation_references"]["status"] == "unavailable"
        assert result["fundamental"]["guidance_analysis"]["rows"]
        assert result["decision"]["status"] == "hold"
        assert result["signal_research"]["history_sessions"] == 0
        assert "매수" not in result["practical_summary"]["one_line"]


def test_technical_snapshot_adds_observed_ranges_without_claiming_52_week_history() -> None:
    frame = pd.DataFrame({
        "close": [100 + index for index in range(100)],
        "low": [99 + index for index in range(100)],
        "high": [101 + index for index in range(100)],
        "volume": [1_000 + index for index in range(100)],
    })

    result = compute_technical_snapshot(frame)

    assert result["return_20d"] > 0
    assert result["range_20d"] == {"low": 179.0, "high": 200.0}
    assert result["ema200"] is None
    assert result["drawdown_from_52w_high"] is None
    assert result["is_buy_signal"] is False


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
