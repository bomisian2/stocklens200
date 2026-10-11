from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_streamlit_snapshot_smoke() -> None:
    app = AppTest.from_file("app.py")
    app.run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "StockLens 200 v2"
    assert len(app.dataframe) == 1
