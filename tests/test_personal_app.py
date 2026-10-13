from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest


def test_personal_app_renders_financials_without_market_signal(tmp_path, monkeypatch) -> None:
    financial_dir = tmp_path / "normalized" / "financials"
    financial_dir.mkdir(parents=True)
    financial = {
        "company": "Fixture Corp",
        "ticker": "FIX",
        "status": "verified",
        "collected_at_utc": "2026-10-13T00:00:00+00:00",
        "periods": [
            {
                "label": "FY2025",
                "period_end": "2025-12-31",
                "accounting_basis": "fixture_only",
                "metrics": {"revenue": 100.0, "diluted_eps": 1.0},
            }
        ],
        "balance_sheet": {"as_of": "2025-12-31", "cash_and_equivalents": 10.0},
        "guidance": {"status": "unavailable"},
        "documents": [
            {
                "key": "fixture",
                "published_at": "2026-01-01T00:00:00+00:00",
                "period_end": "2025-12-31",
                "distribution": "test fixture",
                "sha256": "0" * 64,
                "url": "https://example.invalid/fixture",
            }
        ],
    }
    (financial_dir / "FIX.json").write_text(json.dumps(financial), encoding="utf-8")
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "financial_tickers_verified": 1,
                "financial_periods_verified": 1,
                "market": {"status": "blocked"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))
    app_path = Path(__file__).parents[1] / "personal_app.py"

    app = AppTest.from_file(str(app_path)).run(timeout=20)

    assert not app.exception
    rendered = " ".join(item.value for item in [*app.title, *app.caption, *app.warning, *app.error])
    assert "개인 연구" in rendered
    assert "운영 판단: hold" in rendered
    assert "공식 목표가격" not in rendered
    assert any("개인용 시장 데이터 현황" in item.value for item in app.subheader)


def test_personal_app_integrates_amat_without_promoting_research_values(tmp_path, monkeypatch) -> None:
    financial_dir = tmp_path / "normalized" / "financials"
    market_dir = tmp_path / "market"
    financial_dir.mkdir(parents=True)
    market_dir.mkdir(parents=True)
    financial = {
        "company": "Applied Materials, Inc.", "ticker": "AMAT", "status": "verified",
        "collected_at_utc": "2026-10-13T00:00:00+00:00",
        "periods": [
            {"kind": "fiscal_year", "label": "FY2025", "period_end": "2025-10-26", "accounting_basis": "reported_gaap", "metrics": {"revenue": 100.0, "operating_income": 25.0, "net_income": 20.0, "diluted_eps": 8.0, "diluted_shares": 808.0, "cfo": 30.0, "capex": 10.0, "fcf": 20.0}},
            {"kind": "quarter", "label": "Q3 FY2026", "period_end": "2026-07-26", "accounting_basis": "reported_gaap", "metrics": {"revenue": 30.0, "operating_income": 9.0, "net_income": 7.0, "diluted_eps": 3.0, "diluted_shares": 800.0, "cfo": 9.0, "capex": 3.0, "fcf": 6.0}},
            {"kind": "ttm", "label": "TTM through Q3 FY2026", "period_end": "2026-07-26", "accounting_basis": "derived_from_reported_gaap", "metrics": {"revenue": 120.0, "operating_income": 30.0, "net_income": 24.0, "diluted_eps": None, "diluted_shares": None, "cfo": 36.0, "capex": 12.0, "fcf": 24.0}},
        ],
        "balance_sheet": {"cash_and_equivalents": 10.0, "short_term_investments": 2.0, "long_term_investments": 3.0, "short_term_debt": 1.0, "long_term_debt": 4.0},
        "guidance": {"period": "Q4 FY2026", "revenue_midpoint": 40.0, "revenue_plus_minus": 2.0, "non_gaap_eps_midpoint": 4.0, "non_gaap_eps_plus_minus": 0.2},
        "documents": [{"key": "fixture", "published_at": "2026-08-13T20:01:00+00:00", "period_end": "2026-07-26", "distribution": "fixture", "sha256": "0" * 64, "url": "https://example.invalid/fixture"}],
    }
    (financial_dir / "AMAT.json").write_text(json.dumps(financial), encoding="utf-8")
    dates = pd.bdate_range("2026-05-20", periods=100).strftime("%Y-%m-%d")
    prices = pd.Series([400 + index for index in range(100)], dtype=float)
    frame = pd.DataFrame({"date": dates, "open": prices - 1, "high": prices + 2, "low": prices - 2, "close": prices, "volume": 1_000_000})
    frame.to_csv(market_dir / "AMAT.csv", index=False)
    frame.assign(close=prices * 1.1).to_csv(market_dir / "QQQ.csv", index=False)
    (market_dir / "AMAT.manifest.json").write_text(json.dumps({
        "freshness_status": "verified", "market_date": dates[-1],
        "collected_at_utc": "2026-10-13T00:00:00+00:00", "provider": "fixture",
        "price_policy": "raw_as_traded_unadjusted", "classification": "test_fixture",
    }), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps({
        "financial_tickers_available": 1, "financial_periods_available": 3,
        "market": {"status": "partial", "results": [
            {"ticker": "AMAT", "status": "verified", "as_of": dates[-1], "rows": 100},
            {"ticker": "QQQ", "status": "verified", "as_of": dates[-1], "rows": 100},
        ]},
    }), encoding="utf-8")
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))

    app = AppTest.from_file(str(Path(__file__).parents[1] / "personal_app.py")).run(timeout=20)

    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["검증 EOD 종가"] == "$499.00"
    assert metrics["대용 TTM FCF yield"] == "0.0%"
    assert metrics["EMA20"] != "미확보"
    warnings = " ".join(item.value for item in app.warning)
    errors = " ".join(item.value for item in app.error)
    assert "연구 참고 전용" in warnings
    assert "운영 판단: hold" in errors
    assert "정식 적정가" in errors


def test_personal_app_renders_avgo_and_qcom_financial_analysis_without_prices(tmp_path, monkeypatch) -> None:
    financial_dir = tmp_path / "normalized" / "financials"
    financial_dir.mkdir(parents=True)
    guidance_by_ticker = {
        "AVGO": {"period": "Q4 FY2026", "revenue_approx": 140.0, "non_gaap_operating_margin_percent": 66.0},
        "QCOM": {"period": "Q4 FY2026", "revenue_low": 95.0, "revenue_high": 105.0, "gaap_eps_low": 1.2, "gaap_eps_high": 1.4},
    }
    for ticker in ("AVGO", "QCOM"):
        financial = {
            "company": ticker, "ticker": ticker, "status": "verified",
            "collected_at_utc": "2026-10-13T00:00:00+00:00",
            "periods": [
                {"kind": "fiscal_year", "label": "FY2025", "period_end": "2025-10-31", "accounting_basis": "reported_gaap", "metrics": {"revenue": 100.0, "operating_income": 25.0, "net_income": 20.0, "diluted_eps": 2.0, "diluted_shares": 10.0, "cfo": 30.0, "capex": 10.0, "fcf": 20.0}},
                {"kind": "quarter", "label": "Q3 FY2026", "period_end": "2026-07-31", "accounting_basis": "reported_gaap", "metrics": {"revenue": 30.0, "operating_income": 9.0, "net_income": 7.0, "diluted_eps": 0.7, "diluted_shares": 9.0, "cfo": 9.0, "capex": 3.0, "fcf": 6.0}},
                {"kind": "ttm", "label": "TTM through Q3 FY2026", "period_end": "2026-07-31", "accounting_basis": "derived_from_reported_gaap", "metrics": {"revenue": 120.0, "operating_income": 30.0, "net_income": 24.0, "diluted_eps": None, "diluted_shares": None, "cfo": 36.0, "capex": 12.0, "fcf": 24.0}},
            ],
            "balance_sheet": {"cash_and_equivalents": 10.0, "short_term_debt": 1.0, "long_term_debt": 4.0},
            "guidance": guidance_by_ticker[ticker],
            "documents": [{"key": "fixture", "published_at": "2026-08-13T20:01:00+00:00", "period_end": "2026-07-31", "distribution": "fixture", "sha256": "0" * 64, "url": "https://example.invalid/fixture"}],
        }
        (financial_dir / f"{ticker}.json").write_text(json.dumps(financial), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps({
        "financial_tickers_available": 2, "financial_periods_available": 6,
        "market": {"status": "blocked", "results": [
            {"ticker": "AVGO", "status": "blocked", "message": "safe"},
            {"ticker": "QCOM", "status": "blocked", "message": "safe"},
        ]},
    }), encoding="utf-8")
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))

    app = AppTest.from_file(str(Path(__file__).parents[1] / "personal_app.py")).run(timeout=20)
    assert not app.exception
    for ticker in ("AVGO", "QCOM"):
        app.selectbox[0].set_value(ticker).run(timeout=20)
        assert not app.exception
        rendered = " ".join(item.value for item in [*app.header, *app.subheader, *app.warning, *app.error, *app.markdown])
        assert ticker in rendered
        assert "가이던스" in rendered
        assert "운영 판단: hold" in rendered
        assert "정식 적정가" in rendered


def test_personal_app_registers_all_ten_and_keeps_tsm_blocked(tmp_path, monkeypatch) -> None:
    (tmp_path / "normalized" / "financials").mkdir(parents=True)
    (tmp_path / "status.json").write_text(
        json.dumps({"financial_tickers_available": 0, "financial_periods_available": 0, "market": {"status": "unavailable"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "personal_app.py")).run(timeout=20)

    assert not app.exception
    assert len(app.selectbox[0].options) == 10
    app.selectbox[0].set_value("TSM").run(timeout=20)
    rendered = " ".join(item.value for item in [*app.header, *app.warning, *app.info, *app.error])
    assert "TIFRS / TWD" in rendered
    assert "HTTP 403" in rendered
    assert "운영 판단: hold" in rendered
