from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest


def test_streamlit_snapshot_smoke() -> None:
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py")
    app.run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "StockLens 200 v2"
    assert len(app.dataframe) == 1


def test_streamlit_renders_independently_validated_signal_schema(
    tmp_path: Path, monkeypatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    shutil.copy(root / "data" / "universe.csv", tmp_path / "universe.csv")
    shutil.copy(root / "data" / "status.json", tmp_path / "status.json")
    market_session = json.loads(
        (tmp_path / "status.json").read_text(encoding="utf-8")
    )["market_session_date"]
    pd.DataFrame([{
        "ticker": "TEST", "strategy_label": "EMA Crossback",
        "signal_date": market_session, "entry_expected_date": "2026-10-12",
        "best_horizon": 10, "validation_samples": 15, "win_rate": 0.6,
        "expected_return": 0.03, "average_excess_return": 0.02,
        "payoff_ratio": 1.5, "mtm_max_drawdown": -0.12,
        "average_mae": -0.04, "confidence": "independent_validation_passed",
        "status": "current_active",
    }]).to_csv(tmp_path / "signals.csv", index=False)
    pd.DataFrame([{
        "ticker": "TEST", "strategy_label": "EMA Crossback", "horizon": 10,
        "train_samples": 25, "walk_forward_samples": 25, "final_samples": 15,
        "final_win_rate": 0.6, "final_average_return": 0.03,
        "final_payoff_ratio": 1.5, "final_mtm_max_drawdown": -0.12,
        "final_average_mae": -0.04, "final_average_mfe": 0.08,
        "final_average_excess_return": 0.02, "fdr_qvalue": 0.04,
        "validation_state": "independent_validated", "hold_reason": "",
    }]).to_csv(tmp_path / "signal_backtests.csv", index=False)
    (tmp_path / "signal_status.json").write_text(json.dumps({
        "data_state": "healthy", "market_session_date": market_session,
        "eligible_for_display": True,
    }), encoding="utf-8")
    monkeypatch.setenv("STOCKLENS_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(root / "app.py")
    app.run(timeout=30)
    assert not app.exception
    assert any(item.value == "검증된 단기 매수 신호" for item in app.subheader)
    assert len(app.dataframe) == 4
