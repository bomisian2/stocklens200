from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from stocklens.personal.v23_adapter import EXPECTED_HISTORICAL_ROWS


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
    avgo_financial = json.loads(json.dumps(financial))
    avgo_financial["company"] = "Broadcom Inc."
    avgo_financial["ticker"] = "AVGO"
    avgo_financial["guidance"] = {
        "period": "Q4 FY2026",
        "revenue_approx": 40.0,
        "non_gaap_operating_margin_percent": 66.0,
    }
    (financial_dir / "AVGO.json").write_text(json.dumps(avgo_financial), encoding="utf-8")
    nvda_financial = json.loads(json.dumps(financial))
    nvda_financial["company"] = "NVIDIA Corporation"
    nvda_financial["ticker"] = "NVDA"
    nvda_financial["guidance"] = {"status": "unavailable"}
    (financial_dir / "NVDA.json").write_text(json.dumps(nvda_financial), encoding="utf-8")
    dates = pd.bdate_range("2026-05-20", periods=100).strftime("%Y-%m-%d")
    prices = pd.Series([400 + index for index in range(100)], dtype=float)
    frame = pd.DataFrame({"date": dates, "open": prices - 1, "high": prices + 2, "low": prices - 2, "close": prices, "volume": 1_000_000})
    frame.to_csv(market_dir / "AMAT.csv", index=False)
    frame.assign(close=prices * 0.8).to_csv(market_dir / "AVGO.csv", index=False)
    frame.assign(close=prices * 1.1).to_csv(market_dir / "QQQ.csv", index=False)
    (market_dir / "AMAT.manifest.json").write_text(json.dumps({
        "freshness_status": "verified", "market_date": dates[-1],
        "collected_at_utc": "2026-10-13T00:00:00+00:00", "provider": "fixture",
        "price_policy": "raw_as_traded_unadjusted", "classification": "test_fixture",
    }), encoding="utf-8")
    (market_dir / "AVGO.manifest.json").write_text(json.dumps({
        "freshness_status": "verified", "market_date": dates[-1],
        "collected_at_utc": "2026-10-13T00:00:00+00:00", "provider": "fixture",
        "price_policy": "raw_as_traded_unadjusted", "classification": "test_fixture",
    }), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps({
        "financial_tickers_available": 1, "financial_periods_available": 3,
        "market": {"status": "partial", "results": [
            {"ticker": "AMAT", "status": "verified", "as_of": dates[-1], "rows": 100},
            {"ticker": "AVGO", "status": "verified", "as_of": dates[-1], "rows": 100},
            {"ticker": "QQQ", "status": "verified", "as_of": dates[-1], "rows": 100},
        ]},
    }), encoding="utf-8")
    monkeypatch.setenv("STOCKLENS_PERSONAL_DATA_DIR", str(tmp_path))
    v23_fixture_path = Path(__file__).parent / "fixtures" / "v23_public_ledger_minimal.json"
    v23_payload = json.loads(v23_fixture_path.read_text(encoding="utf-8"))
    for v23_ticker, count in EXPECTED_HISTORICAL_ROWS.items():
        seed = v23_payload["tickers"][v23_ticker]["historicalPer"][0]
        v23_payload["tickers"][v23_ticker]["historicalPer"] = [dict(seed) for _ in range(count)]
    v23_path = tmp_path / "v23-public-ledger.json"
    v23_bytes = json.dumps(v23_payload, sort_keys=True).encode("utf-8")
    v23_path.write_bytes(v23_bytes)
    monkeypatch.setenv("STOCKLENS_V23_LEDGER_PATH", str(v23_path))
    monkeypatch.setenv("STOCKLENS_V23_LEDGER_SHA256", hashlib.sha256(v23_bytes).hexdigest())

    app = AppTest.from_file(str(Path(__file__).parents[1] / "personal_app.py")).run(timeout=20)

    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["검증 EOD 종가"] == "$499.00"
    assert metrics["대용 TTM FCF yield"] == "0.0%"
    assert metrics["발행 보통주"] == "793.597M"
    assert metrics["시점 불일치 주식가치 참고"] != "미확보"
    assert metrics["EMA20"] != "미확보"
    rendered_text = " ".join(item.value for item in [*app.subheader, *app.markdown, *app.caption])
    assert "AMAT·AVGO 실제 투자 분석 비교" in rendered_text
    assert "실적은 개선되고 있는가?" in rendered_text
    assert "현재 가격에는 높은 성장이 요구되는가?" in rendered_text
    assert "대용 P/FCF" in metrics
    assert "저성장·고할인 스트레스 조건" in metrics
    assert "중간 스트레스 격자(공식 기본값 아님)" in metrics
    assert "고성장·저할인 스트레스 조건" in metrics
    assert "정상 관심 범위" in metrics
    assert metrics["정상 관심 범위"] == "미산출"
    assert "현재 시장 요구 성장" in metrics
    assert "품질 근거 상태" in metrics
    assert metrics["운영 투자판단"] == "비활성"
    assert "검증된 위기 스트레스 범위" in metrics
    expander_states = {item.label: item.proto.expanded for item in app.expander}
    assert expander_states["품질 프리미엄 증거 · 점수로 합산하지 않음"] is True
    assert expander_states["평가 축 산출 자격과 결측 정책"] is False
    assert expander_states["평가 입력과 기준일 상세"] is False
    assert expander_states["2차 상세 · DCF 스트레스 참고 범위"] is False
    warnings = " ".join(item.value for item in app.warning)
    errors = " ".join(item.value for item in app.error)
    assert "연구 참고 전용" in warnings
    assert "확정 시가총액" in warnings
    assert "운영 판단: hold" in errors
    assert "정식 적정가" in errors
    assert "목표가격·매수구간" in warnings
    assert "정확성 부족을 더 낮은 가치로 바꾸지 않으며" in warnings

    app.selectbox[0].set_value("AVGO").run(timeout=20)
    assert not app.exception
    avgo_metrics = {item.label: item.value for item in app.metric}
    assert avgo_metrics["발행 보통주"] == "4,773.630M"
    assert avgo_metrics["시점 불일치 주식가치 참고"] != "미확보"
    avgo_rendered = " ".join(item.value for item in [*app.caption, *app.warning, *app.error])
    assert "0001730168-26-000080" in avgo_rendered
    assert "정식 적정가" in avgo_rendered
    assert "operational_eligible=false" in " ".join(item.value for item in app.caption)
    assert any("v23 reconstructed PIT 비교" in item.value for item in app.subheader)
    avgo_success = " ".join(item.value for item in app.success)
    assert "동일 가격일 2026-09-11 교차검증" in avgo_success
    assert "EPS·PER 정의는 합치거나 덮어쓰지 않았습니다" in avgo_success
    assert avgo_metrics["TTM 희석 EPS"] == "$7.84"
    assert avgo_metrics["TTM PER"] == "46.17배"

    app.selectbox[0].set_value("NVDA").run(timeout=20)
    assert not app.exception
    nvda_info = " ".join(item.value for item in app.info)
    nvda_captions = " ".join(item.value for item in app.caption)
    assert "StockLens 로컬 가격이 없어 v23 연구 입력만 표시합니다" in nvda_info
    assert "NVDA Q2 FCF: null 유지" in nvda_captions
    assert "동일 가격일 2026-09-11 교차검증" not in " ".join(item.value for item in app.success)


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
