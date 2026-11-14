from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


pytestmark = pytest.mark.streamlit


def test_streamlit_snapshot_smoke() -> None:
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py")
    app.run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "StockLens 200 v2"
    assert len(app.dataframe) == 1
