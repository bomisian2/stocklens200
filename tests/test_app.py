from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_snapshot_smoke() -> None:
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py")
    app.run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "StockLens 200 v2"
    assert len(app.dataframe) == 1


def test_app_source_mentions_stale_financial_warning() -> None:
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "financial_freshness_state" in source
    assert "신규 매매 신호에 사용되지 않습니다" in source
