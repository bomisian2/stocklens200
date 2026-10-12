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


def test_universe_search_sort_and_state_filter() -> None:
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py")
    app.run(timeout=30)

    app.text_input(key="universe_query").set_value("GOOGL")
    app.run(timeout=30)
    assert list(app.dataframe[0].value["티커"]) == ["GOOGL"]

    app.text_input(key="universe_query").set_value("")
    app.selectbox(key="universe_sort").set_value("가격 높은 순")
    app.run(timeout=30)
    prices = list(app.dataframe[0].value["시장 가격"])
    assert prices == sorted(prices, reverse=True)

    app.selectbox(key="universe_state").set_value("blocked")
    app.run(timeout=30)
    assert set(app.dataframe[0].value["티커"]) == {"AMAT", "AVGO", "QCOM"}


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


def test_data_status_discloses_unresolved_public_display_rights() -> None:
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(root / "app_pages" / "data_status.py")
    app.run(timeout=30)
    assert not app.exception
    assert any("운영 배포 보류" in item.value for item in app.error)
