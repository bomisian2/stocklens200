from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


def test_committed_googl_fcff_evidence_is_official_traceable_and_non_operational() -> None:
    payload = json.loads(
        (ROOT / "docs" / "evidence" / "googl-fcff-v2.2.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 1
    assert payload["ticker"] == "GOOGL"
    assert payload["operational_use"] is False
    for name in ("fy2025", "h1_2025", "h1_2026"):
        source = payload["sources"][name]
        assert urlparse(source["url"]).hostname == "s206.q4cdn.com"
        assert len(source["sha256"]) == 64
    assert payload["working_capital_snapshots"]["prior_2025_06_30"]["inventory"] is None
    assert payload["capital_structure_2026_06_30"]["noncontrolling_interest"] is None
    assert payload["wacc_evidence"]["beta"] is None
    assert payload["wacc_evidence"]["equity_risk_premium_verified"] is False
    assert payload["forecast_evidence"]["official_revenue_growth_guidance"] is None

