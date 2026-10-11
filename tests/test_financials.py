from __future__ import annotations

from datetime import datetime, timezone

from stocklens.financials import build_cik_map, extract_financial_periods


def _fact(label: str, rows: list[dict], unit: str = "USD") -> dict:
    return {"label": label, "description": label, "units": {unit: rows}}


def _annual(year: int, value: float, *, filed: str, accn: str, start: str | None = None) -> dict:
    row = {
        "end": f"{year}-12-31",
        "val": value,
        "accn": accn,
        "fy": year,
        "fp": "FY",
        "form": "10-K",
        "filed": filed,
    }
    if start:
        row["start"] = start
    return row


def test_build_cik_map_normalizes_share_class_symbols() -> None:
    payload = {"0": {"ticker": "BRK-B", "cik_str": 1067983}, "1": {"ticker": "GOOGL", "cik_str": "1652044"}}
    assert build_cik_map(payload) == {"BRK.B": 1067983, "GOOGL": 1652044}


def test_extract_periods_is_point_in_time_and_uses_latest_split_adjusted_comparative() -> None:
    older = _annual(2023, 5.0, filed="2024-02-01", accn="old", start="2023-01-01")
    restated = _annual(2023, 2.5, filed="2025-02-01", accn="new", start="2023-01-01")
    future = _annual(2024, 3.0, filed="2026-02-01", accn="future", start="2024-01-01")
    base = [_annual(2023, 100.0, filed="2024-02-01", accn="old", start="2023-01-01")]
    facts = {
        "entityName": "Example",
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", base),
            "NetIncomeLoss": _fact("Income", [_annual(2023, 25.0, filed="2024-02-01", accn="old", start="2023-01-01")]),
            "EarningsPerShareDiluted": _fact("EPS", [older, restated, future], "USD/shares"),
            "WeightedAverageNumberOfDilutedSharesOutstanding": _fact("Shares", [_annual(2023, 10.0, filed="2024-02-01", accn="old", start="2023-01-01")], "shares"),
            "NetCashProvidedByUsedInOperatingActivities": _fact("CFO", [_annual(2023, 30.0, filed="2024-02-01", accn="old", start="2023-01-01")]),
            "PaymentsToAcquirePropertyPlantAndEquipment": _fact("Capex", [_annual(2023, 7.0, filed="2024-02-01", accn="old", start="2023-01-01")]),
        }},
    }
    submissions = {"filings": {"recent": {"accessionNumber": ["old", "new", "future"], "acceptanceDateTime": ["2024-02-01T21:00:00Z", "2025-02-01T21:00:00Z", "2026-02-01T21:00:00Z"]}}}
    rows = extract_financial_periods(
        ticker="EX",
        cik=1,
        companyfacts=facts,
        submissions=submissions,
        as_of=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].diluted_eps == 2.5
    assert rows[0].fcf == 23.0
    assert rows[0].available_at_utc == "2025-02-01T21:00:00+00:00"


def test_non_annual_duration_is_rejected_during_fiscal_year_change() -> None:
    short = _annual(2024, 100.0, filed="2025-01-01", accn="a", start="2024-07-01")
    facts = {"entityName": "Short Year", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", [short]),
    }}}
    rows = extract_financial_periods(
        ticker="SHORT", cik=2, companyfacts=facts, submissions={},
        as_of=datetime(2025, 2, 1, tzinfo=timezone.utc),
    )
    assert rows == []


def test_latest_standalone_quarter_is_recorded_separately() -> None:
    quarter = {
        "start": "2024-07-01", "end": "2024-09-30", "val": 30.0, "accn": "q3",
        "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-10-25",
    }
    facts = {"entityName": "Quarterly", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _fact("Revenue", [quarter]),
        "NetIncomeLoss": _fact("Income", [{**quarter, "val": 5.0}]),
    }}}
    submissions = {"filings": {"recent": {"accessionNumber": ["q3"], "acceptanceDateTime": ["2024-10-25T20:05:00Z"]}}}
    rows = extract_financial_periods(
        ticker="QTR", cik=3, companyfacts=facts, submissions=submissions,
        as_of=datetime(2024, 11, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].period_type == "Q"
    assert rows[0].available_at_utc == "2024-10-25T20:05:00+00:00"

