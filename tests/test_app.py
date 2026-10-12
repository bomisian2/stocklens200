from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_snapshot_smoke() -> None:
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py")
    app.run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "StockLens 200"
    assert len(app.dataframe) == 1
    assert len(app.dataframe[0].value) == 200


def test_research_and_signal_pages_are_explicitly_non_operational() -> None:
    root = Path(__file__).resolve().parents[1]
    research = AppTest.from_file(root / "app_pages" / "research.py")
    research.run(timeout=30)
    assert not research.exception
    assert research.title[0].value == "Alphabet Inc. · GOOGL"
    assert any("투자 신호 아님" in item.value for item in research.warning)
    assert any("목표가격" in item.value for item in research.warning)

    signals = AppTest.from_file(root / "app_pages" / "signals.py")
    signals.run(timeout=30)
    assert not signals.exception
    assert signals.title[0].value == "단기 신호 검증"
    assert any("0개" in item.value for item in signals.error)


def test_company_page_renders_missing_data_without_failure() -> None:
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(root / "app_pages" / "company.py")
    app.run(timeout=30)
    assert not app.exception
    for ticker in ("GOOGL", "AMAT", "AVGO", "QCOM"):
        app.selectbox(key="company_ticker").set_value(ticker)
        app.run(timeout=30)
        assert not app.exception
        assert ticker in app.subheader[0].value
