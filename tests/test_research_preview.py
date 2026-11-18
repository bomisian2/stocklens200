from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from scripts.audit_googl_practical_value import build_audit
from stocklens.research_preview import (
    ResearchPreviewError,
    load_research_preview,
    validate_research_preview,
)


ROOT = Path(__file__).resolve().parents[1]
PREVIEW_PATH = ROOT / "research" / "googl-v2.4.1-preview.json"


def _payload() -> dict:
    return json.loads(PREVIEW_PATH.read_text(encoding="utf-8"))


def test_research_preview_app_smoke_and_required_user_messages() -> None:
    app = AppTest.from_file(ROOT / "research_preview.py")
    app.run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "Alphabet Inc. · GOOGL"
    markdown = "\n".join(item.value for item in app.markdown)
    assert app.warning[0].value == "B등급 연구 참고 · 투자 신호 아님"
    assert "$41.63—$84.88" in markdown
    assert "공식 중간 적정가 없음" in markdown
    assert "운영 자격 false · 판단 hold" in markdown
    assert "WACC" in markdown
    assert "5년 성장·영업마진" in markdown
    assert "투자자산 경제가치" in markdown
    assert "매수 추천" not in markdown
    assert app.metric[1].value == "2026-10-09"
    assert any(item.value == "미국 거래일 기준" for item in app.caption)


def test_committed_preview_matches_reproducible_audit_numbers() -> None:
    payload = load_research_preview(PREVIEW_PATH)
    audit = build_audit(
        ROOT / "docs" / "evidence" / "googl-equity-bridge-v2.4.json",
        ROOT / "docs" / "evidence" / "googl-scenarios-v2.3.json",
        ROOT / "data" / "universe.csv",
    )
    calculated = audit["value_levels"]["B_research_per_share_reference"]
    assert payload["research_per_share_range"]["low"] == pytest.approx(
        calculated["range"]["low"], abs=0.005
    )
    assert payload["research_per_share_range"]["high"] == pytest.approx(
        calculated["range"]["high"], abs=0.005
    )
    assert payload["branches"][0]["low"] == pytest.approx(
        calculated["claim_branch"]["per_share_reference_range"]["low"], abs=0.005
    )
    assert payload["branches"][1]["high"] == pytest.approx(
        calculated["if_converted_branch"]["per_share_reference_range"]["high"], abs=0.005
    )
    for displayed, calculated_driver in zip(
        payload["top_uncertainties"], audit["driver_ranking"][:3], strict=True
    ):
        assert displayed["name"] == {
            "wacc": "WACC",
            "five_year_revenue_growth_and_operating_margin": "5년 성장·영업마진",
            "investment_asset_economic_recognition": "투자자산 경제가치",
        }[calculated_driver["name"]]
        assert displayed["mechanical_per_share_span"] == pytest.approx(
            calculated_driver["mechanical_per_share_span"], abs=0.005
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("fixture", True, "fixture data"),
        ("operational_use", True, "operational use"),
        ("eligible_for_signals", True, "eligible_for_signals"),
        ("eligible_for_ranking", True, "eligible_for_ranking"),
        ("eligible_for_alerts", True, "eligible_for_alerts"),
        ("official_midpoint", 63.25, "official midpoint"),
        ("price_comparison_enabled", True, "current-price comparison"),
    ],
)
def test_preview_rejects_fixture_and_operational_promotion(
    field: str, value: object, message: str
) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(ResearchPreviewError, match=message):
        validate_research_preview(payload)


def test_preview_rejects_recommendation_keys_anywhere() -> None:
    payload = _payload()
    payload["nested"] = {"buy_signal": "buy"}
    with pytest.raises(ResearchPreviewError, match="forbidden operating or recommendation keys"):
        validate_research_preview(payload)


def test_preview_loader_rejects_production_data_directory(tmp_path: Path) -> None:
    path = tmp_path / "data" / "googl.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    with pytest.raises(ResearchPreviewError, match="production data directory"):
        load_research_preview(path)


def test_preview_is_not_wired_into_production_app_or_snapshots() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    workflow_source = (ROOT / ".github" / "workflows" / "refresh-data.yml").read_text(
        encoding="utf-8"
    )
    assert "research_preview" not in app_source
    assert "research_preview" not in workflow_source
    assert PREVIEW_PATH.parent == ROOT / "research"
    assert not (ROOT / "data" / "valuations.csv").exists()
    assert not (ROOT / "data" / "signals.csv").exists()


def test_formal_value_and_automatic_price_labels_remain_absent() -> None:
    payload = _payload()
    assert payload["formal_fair_value"] is None
    assert payload["official_midpoint"] is None
    assert payload["price_comparison_enabled"] is False
    assert payload["judgement"] == "hold"
    serialized = json.dumps(payload, ensure_ascii=False)
    # Disclaimer text must name the forbidden interpretations; the corresponding
    # machine-readable fields must never exist.
    for forbidden in ('"target_price"', '"buy_zone"', '"upside"', '"buy_signal"', '"sell_signal"'):
        assert forbidden not in serialized
