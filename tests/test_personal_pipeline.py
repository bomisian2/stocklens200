from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from stocklens.personal.analysis import compute_fundamental_indicators, compute_personal_analysis
from stocklens.personal.catalog import PILOT_IR_DOCUMENTS
from stocklens.personal.financial_audit import METRIC_KEYS, OFFICIAL_FACT_CONTRACTS, audit_normalized_snapshot
from stocklens.personal.ir_financials import _ttm_values, collect_pilot_financials
from stocklens.personal.extended_financials import (
    _build_amd,
    _build_googl,
    _build_lrcx,
    _build_msft,
    _build_nvda,
)
from stocklens.personal.universe import PERSONAL_PILOT_UNIVERSE, pilot_tickers
from stocklens.personal.market_audit import audit_saved_market_data
from stocklens.personal.market_data import (
    AlphaVantagePersonalClient,
    compute_relative_strength,
    compute_technical_snapshot,
    diagnose_provider_payload,
)
from stocklens.personal.estimates import AlphaVantageEstimatesClient, normalize_alpha_vantage_estimates
from stocklens.personal.planning import build_alpha_vantage_call_plan
from scripts.personal_refresh import _merge_estimate_results, _merge_market_results


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


class TimeoutSession:
    def get(self, *args, **kwargs):
        import requests

        raise requests.exceptions.Timeout("secret provider detail")


def test_market_timeout_is_safely_classified_without_secret_detail(tmp_path) -> None:
    result = AlphaVantagePersonalClient("not-persisted", session=TimeoutSession()).collect_daily("QCOM", tmp_path)

    assert result.status == "blocked"
    assert result.diagnostic_code == "timeout"
    assert result.retryable is True
    assert "secret provider detail" not in result.message


def test_market_status_merge_preserves_last_good_snapshot() -> None:
    merged = _merge_market_results(
        [{"ticker": "AMAT", "status": "verified", "as_of": "2026-10-12"}],
        [{"ticker": "AMAT", "status": "blocked", "diagnostic_code": "rate_limit"}],
    )

    assert merged[0]["status"] == "verified"
    assert merged[0]["as_of"] == "2026-10-12"
    assert merged[0]["last_good_preserved"] is True
    assert merged[0]["last_attempt"]["diagnostic_code"] == "rate_limit"


def test_ten_company_call_plan_fits_prices_and_weekly_estimates_without_retries() -> None:
    plan = build_alpha_vantage_call_plan((*pilot_tickers(), "QQQ"), pilot_tickers())

    assert plan["market"]["calls"] == 11
    assert plan["estimates"]["calls"] == 10
    assert plan["total_calls_if_all_enabled"] == 21
    assert plan["reserve_calls"] == 4
    assert plan["within_published_limit"] is True
    assert plan["automatic_retries"] == 0
    assert plan["estimates"]["enabled"] is False


def _estimate_payload() -> dict:
    return {
        "symbol": "AMAT",
        "annualEarnings": [
            {
                "fiscalDateEnding": "2027-10-31",
                "horizon": "12month",
                "eps_estimate_average": "10.50",
                "eps_estimate_low": "9.00",
                "eps_estimate_high": "12.00",
                "eps_estimate_analyst_count": "22",
                "revenue_estimate_average": "34000000000",
                "revenue_estimate_low": "32000000000",
                "revenue_estimate_high": "36000000000",
                "revenue_estimate_analyst_count": "18",
                "eps_estimate_revision_up_trailing_7_days": "2",
            }
        ],
        "quarterlyEarnings": [
            {
                "fiscalDateEnding": "2027-01-31",
                "eps_estimate_average": "2.10",
                "eps_estimate_analyst_count": "20",
                "revenue_estimate_average": "8000000000",
                "revenue_estimate_analyst_count": "17",
            }
        ],
    }


def test_estimate_normalization_keeps_consensus_separate_and_non_operational() -> None:
    result = normalize_alpha_vantage_estimates(
        _estimate_payload(), ticker="AMAT", collected_at_utc="2026-10-13T00:00:00+00:00"
    )

    assert result["data_kind"] == "analyst_consensus"
    assert result["issuer_guidance_mixed"] is False
    assert result["provider_snapshot_time"] is None
    assert result["operational_eligible"] is False
    assert len(result["periods"]) == 2
    assert result["periods"][0]["eps"]["analyst_count"] == 22


class EstimateResponse:
    status_code = 200

    def json(self):
        return _estimate_payload()


class EstimateSession:
    def __init__(self) -> None:
        self.params = None

    def get(self, *args, **kwargs):
        self.params = kwargs["params"]
        return EstimateResponse()


class MissingEstimateResponse:
    status_code = 200

    def json(self):
        return {"Information": "Dataset is unavailable for this request"}


class MissingEstimateSession:
    def get(self, *args, **kwargs):
        return MissingEstimateResponse()


def test_estimate_client_writes_only_ignored_local_contract_without_key(tmp_path) -> None:
    session = EstimateSession()
    result = AlphaVantageEstimatesClient(
        "estimate-secret", session=session, now=datetime(2026, 10, 13, tzinfo=timezone.utc)
    ).collect("amat", tmp_path)

    assert result.status == "research_only"
    assert result.periods == 2
    raw = (tmp_path / "estimates" / "raw" / "AMAT.json").read_text(encoding="utf-8")
    normalized = (tmp_path / "estimates" / "normalized" / "AMAT.json").read_text(encoding="utf-8")
    assert "estimate-secret" not in raw + normalized
    saved = json.loads(normalized)
    assert saved["source"]["redistribution_authorized"] is False
    assert saved["operational_eligible"] is False
    assert session.params["function"] == "EARNINGS_ESTIMATES"


def test_estimate_client_uses_dataset_specific_missing_diagnostic(tmp_path) -> None:
    result = AlphaVantageEstimatesClient("estimate-secret", session=MissingEstimateSession()).collect("AMAT", tmp_path)

    assert result.status == "blocked"
    assert result.diagnostic_code == "estimate_periods_missing"
    assert not (tmp_path / "estimates").exists()


def test_estimate_status_merge_preserves_last_good_local_snapshot() -> None:
    merged = _merge_estimate_results(
        [{"ticker": "AMAT", "status": "research_only", "periods": 2}],
        [{"ticker": "AMAT", "status": "blocked", "diagnostic_code": "rate_limit"}],
    )

    assert merged[0]["status"] == "research_only"
    assert merged[0]["last_good_preserved"] is True
    assert merged[0]["last_attempt"]["diagnostic_code"] == "rate_limit"


def test_actual_consensus_contract_can_compute_only_research_forward_per() -> None:
    financial = {
        "ticker": "AMAT", "status": "verified",
        "periods": [
            {"kind": "fiscal_year", "label": "FY2025", "metrics": {"revenue": 100.0, "diluted_eps": 8.0}},
            {"kind": "quarter", "label": "Q3 FY2026", "period_end": "2026-07-26", "metrics": {"diluted_shares": 800.0}},
            {"kind": "ttm", "period_end": "2026-07-26", "metrics": {"revenue": 120.0, "operating_income": 30.0, "fcf": 12.0}},
        ],
        "balance_sheet": {"cash_and_equivalents": 1.0, "short_term_debt": 0.0, "long_term_debt": 0.0},
        "guidance": {}, "documents": [],
    }
    market = pd.DataFrame({"date": ["2026-10-12"] * 50, "close": [100.0] * 50})
    estimates = normalize_alpha_vantage_estimates(
        _estimate_payload(), ticker="AMAT", collected_at_utc="2026-10-13T00:00:00+00:00"
    )

    result = compute_personal_analysis(financial, market, market, {"market_date": "2026-10-12"}, estimates)

    assert result["future_estimates"]["status"] == "research_only"
    assert round(result["future_estimates"]["forward_per_reference"], 4) == round(100 / 10.5, 4)
    assert result["future_estimates"]["operational_eligible"] is False
    assert result["decision"]["is_buy_signal"] is False


def test_past_consensus_period_is_not_used_as_forward_per() -> None:
    financial = {
        "ticker": "AMAT", "status": "verified",
        "periods": [
            {"kind": "fiscal_year", "label": "FY2025", "metrics": {"revenue": 100.0, "diluted_eps": 8.0}},
            {"kind": "ttm", "period_end": "2026-07-26", "metrics": {"revenue": 120.0, "operating_income": 30.0, "fcf": 12.0}},
        ],
        "balance_sheet": {}, "guidance": {}, "documents": [],
    }
    market = pd.DataFrame({"date": ["2026-10-12"], "close": [100.0]})
    payload = _estimate_payload()
    payload["annualEarnings"][0]["fiscalDateEnding"] = "2025-10-31"
    estimates = normalize_alpha_vantage_estimates(
        payload, ticker="AMAT", collected_at_utc="2026-10-13T00:00:00+00:00"
    )

    result = compute_personal_analysis(financial, market, None, {"market_date": "2026-10-12"}, estimates)

    assert result["future_estimates"]["next_annual"] is None
    assert result["future_estimates"]["forward_per_reference"] is None


def test_official_security_structure_evidence_covers_splits_classes_and_adr_ratio() -> None:
    assert PERSONAL_PILOT_UNIVERSE["AVGO"]["corporate_action_evidence"][0]["ratio"] == 10.0
    assert PERSONAL_PILOT_UNIVERSE["NVDA"]["corporate_action_evidence"][0]["split_adjusted_trading_date"] == "2024-06-10"
    assert "Class B super-voting" in PERSONAL_PILOT_UNIVERSE["GOOGL"]["share_classes"]["unlisted"]
    assert PERSONAL_PILOT_UNIVERSE["TSM"]["adr_evidence"]["ads_to_common_share_ratio"] == 5.0


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
    assert references["point_in_time_share_evidence"]["kind"] == "common_stock_outstanding"
    point_reference = references["point_in_time_equity_reference"]
    assert point_reference["status"] == "time_mismatched_research_only"
    assert point_reference["formal_market_cap_eligible"] is False
    assert point_reference["equity_value_usd_millions"] == 499.0 * 793.597443
    assert point_reference["date_gap_days"] > 0
    assert references["reverse_dcf"]["status"] == "underdetermined"
    assert references["reverse_dcf"]["numeric_result"] is None
    assert references["reverse_dcf"]["reuse_assessment"] == "arithmetic_reusable_inputs_not_operationally_complete"
    assert references["reverse_dcf"]["missing_or_partial_inputs"] == [
        "share_denominator", "discount_rate", "terminal_growth"
    ]
    assert references["reverse_dcf"]["formal_fcff_input_map"]["normalized_operating_tax_rate"] == "missing"
    conditional = references["conditional_research_valuation"]
    assert conditional["status"] == "research_only"
    assert conditional["operational_eligible"] is False
    assert conditional["is_fair_value"] is False
    assert conditional["selected_base_value"] is None
    assert conditional["model_version"] == "levered_proxy_grid_v1"
    assert [row["growth_rate"] for row in conditional["scenario_values"]] == [0.0, 0.10, 0.20]
    assert conditional["scenario_values"][0]["research_per_issued_share"] < conditional["scenario_values"][1]["research_per_issued_share"]
    assert conditional["scenario_values"][1]["research_per_issued_share"] < conditional["scenario_values"][2]["research_per_issued_share"]
    assert conditional["implied_growth_range"] is None
    assert all(row["status"] == "not_bracketed" for row in conditional["implied_growth_sensitivity"])
    assert references["share_source_url"].startswith("https://ir.appliedmaterials.com/")
    assert references["proxy_p_fcf"] == references["proxy_equity_value_usd_millions"] / 12.0
    sensitivity = references["fcf_growth_sensitivity"]
    assert sensitivity["status"] == "research_only"
    assert [row["fcf_cagr"] for row in sensitivity["rows"]] == [0.0, 0.10, 0.15, 0.20, 0.25]
    assert sensitivity["rows"][0]["year5_fcf_usd_millions"] == 12.0
    assert sensitivity["operational_eligible"] is False
    questions = result["investment_questions"]
    assert questions["growth_expectation"]["status"] == "research_only"
    assert "5년간 연 15%" in questions["growth_expectation"]["answer"]
    assert questions["technical"]["status"] == "descriptive_only"
    assert result["signal_research"]["independent_validated_strategies"] == 0
    assert result["decision"] == {
        "status": "hold",
        "is_buy_signal": False,
        "reason": "Technical indicators are descriptive; fair value and independently validated signals are unavailable",
    }


def test_avgo_official_cover_shares_are_distinct_from_weighted_average_proxy() -> None:
    dates = pd.bdate_range("2026-05-20", periods=100).strftime("%Y-%m-%d")
    closes = pd.Series([255.73 + index for index in range(100)], dtype=float)
    market = pd.DataFrame({
        "date": dates, "open": closes - 1, "high": closes + 2,
        "low": closes - 2, "close": closes, "volume": [2_000_000 + index for index in range(100)],
    })
    financial = {
        "ticker": "AVGO", "status": "verified",
        "periods": [
            {"kind": "fiscal_year", "label": "FY2025", "metrics": {"revenue": 63887.0, "diluted_eps": 4.77}},
            {"kind": "quarter", "label": "Q3 FY2026", "period_end": "2026-08-02", "metrics": {"diluted_shares": 4887.0}},
            {"kind": "ttm", "period_end": "2026-08-02", "metrics": {"revenue": 89104.0, "operating_income": 42814.0, "capex": 1250.0, "fcf": 39403.0}},
        ],
        "balance_sheet": {"cash_and_equivalents": 23975.0, "short_term_debt": 2252.0, "long_term_debt": 57167.0},
        "guidance": {},
        "documents": [],
    }

    result = compute_personal_analysis(
        financial, market, pd.DataFrame(),
        {"freshness_status": "verified", "market_date": "2026-10-12", "price_policy": "raw_as_traded_unadjusted"},
    )

    references = result["valuation_references"]
    shares = references["point_in_time_share_evidence"]
    point_reference = references["point_in_time_equity_reference"]
    assert shares["value_millions"] == 4773.629865
    assert shares["as_of"] == "2026-08-28"
    assert shares["accession_number"] == "0001730168-26-000080"
    assert references["diluted_share_proxy_millions"] == 4887.0
    assert point_reference["date_gap_days"] == 45
    assert point_reference["status"] == "time_mismatched_research_only"
    assert point_reference["formal_market_cap_eligible"] is False
    assert references["formal_market_cap"] is None
    assert references["fair_value"] is None
    assert references["reverse_dcf"]["numeric_result"] is None
    conditional = references["conditional_research_valuation"]
    assert conditional["status"] == "research_only"
    assert conditional["issued_common_shares_millions"] == 4773.629865
    assert conditional["operational_eligible"] is False
    assert conditional["date_gap_days"] == 45
    assert conditional["selected_base_value"] is None


def test_fundamental_trend_metrics_compare_ttm_with_fiscal_year_without_claiming_yoy() -> None:
    financial = {
        "ticker": "AVGO",
        "status": "verified",
        "periods": [
            {
                "kind": "fiscal_year",
                "label": "FY2025",
                "period_end": "2025-11-02",
                "metrics": {
                    "revenue": 100.0,
                    "operating_income": 25.0,
                    "fcf": 20.0,
                    "diluted_eps": 2.0,
                },
            },
            {
                "kind": "quarter",
                "label": "Q3 FY2026",
                "period_end": "2026-08-02",
                "metrics": {"revenue": 30.0, "diluted_shares": 10.0},
            },
            {
                "kind": "ttm",
                "label": "TTM through Q3 FY2026",
                "period_end": "2026-08-02",
                "metrics": {"revenue": 120.0, "operating_income": 36.0, "fcf": 30.0},
            },
        ],
        "balance_sheet": {
            "cash_and_equivalents": 1.0,
            "short_term_debt": 0.0,
            "long_term_debt": 0.0,
        },
        "guidance": {},
        "documents": [],
    }

    metrics = compute_fundamental_indicators(financial)["metrics"]

    assert abs(metrics["ttm_revenue_change_vs_fy2025"]["value"] - 0.2) < 1e-12
    assert abs(metrics["ttm_operating_income_change_vs_fy2025"]["value"] - 0.44) < 1e-12
    assert abs(metrics["ttm_fcf_change_vs_fy2025"]["value"] - 0.5) < 1e-12
    assert abs(metrics["ttm_operating_margin_change_bps_vs_fy2025"]["value"] - 500.0) < 1e-9
    assert abs(metrics["ttm_fcf_margin_change_bps_vs_fy2025"]["value"] - 500.0) < 1e-9
    assert metrics["ttm_fcf_change_vs_fy2025"]["note"] == "Rolling twelve-month change, not year-over-year growth"


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


def test_ten_company_registry_keeps_tsm_accounting_and_access_state_explicit() -> None:
    assert pilot_tickers() == ("AMAT", "AVGO", "QCOM", "GOOGL", "NVDA", "MSFT", "MU", "AMD", "TSM", "LRCX")
    assert PERSONAL_PILOT_UNIVERSE["TSM"]["accounting_standard"] == "TIFRS"
    assert PERSONAL_PILOT_UNIVERSE["TSM"]["currency"] == "TWD"
    assert PERSONAL_PILOT_UNIVERSE["TSM"]["financial_source_state"] == "blocked"


def test_extended_reviewed_snapshots_preserve_ttm_and_partial_boundaries() -> None:
    for snapshot in (_build_googl(), _build_amd(), _build_msft(), _build_lrcx()):
        ttm = next(row for row in snapshot["periods"] if row["kind"] == "ttm")
        metrics = ttm["metrics"]
        assert metrics["fcf"] == metrics["cfo"] - metrics["capex"]
        assert metrics["diluted_eps"] is None
    googl_ttm = next(row for row in _build_googl()["periods"] if row["kind"] == "ttm")
    assert googl_ttm["metrics"]["revenue"] == 445866
    assert googl_ttm["metrics"]["fcf"] == 53273
    nvda = _build_nvda()
    assert nvda["status"] == "partial"
    assert not any(row["kind"] == "ttm" for row in nvda["periods"])


def test_common_analysis_contract_runs_for_every_extended_ttm_company() -> None:
    for snapshot in (_build_googl(), _build_amd(), _build_msft(), _build_lrcx()):
        snapshot["documents"] = []
        result = compute_personal_analysis(snapshot)
        assert result["fundamental"]["status"] in {"verified", "partial"}
        assert result["valuation_references"]["operational_eligible"] is False
        assert result["signal_research"]["active_operational_signals"] == 0
        assert result["decision"]["status"] == "hold"

    nvda = _build_nvda()
    assert compute_fundamental_indicators(nvda)["status"] == "unavailable"
