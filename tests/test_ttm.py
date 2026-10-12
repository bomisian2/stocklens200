from __future__ import annotations

import json
from dataclasses import replace

import pytest

from stocklens.financials import FinancialPeriod
from stocklens.ttm import append_latest_ttm, build_ttm_period


def _period(
    *,
    year: int,
    kind: str,
    start: str,
    end: str,
    revenue: float,
    operating_income: float,
    net_income: float,
    pretax_income: float,
    income_tax: float,
    cfo: float,
    capex: float,
    diluted_eps: float,
    diluted_shares: float,
    other_income: float,
    securities_gain: float,
    common_income: float | None = None,
    preferred_dividends: float = 0.0,
) -> FinancialPeriod:
    return FinancialPeriod(
        ticker="GOOGL",
        cik=1652044,
        company_name="Alphabet Inc.",
        fiscal_year=year,
        period_type=kind,
        period_start=start,
        period_end=end,
        filed_at="2026-07-22",
        available_at_utc=(
            "2026-07-22T20:01:36+00:00" if year == 2026 else "2026-02-05T02:56:03+00:00"
        ),
        availability_basis="sec_acceptance_timestamp_crosscheck",
        availability_estimated=False,
        form="8-K" if kind != "FY" else "10-K",
        accession=f"test-{year}-{kind}-{end}",
        currency="USD",
        revenue=revenue,
        operating_income=operating_income,
        net_income=net_income,
        common_net_income=common_income if common_income is not None else net_income,
        pretax_income=pretax_income,
        income_tax=income_tax,
        other_income_expense=other_income,
        securities_gain_loss=securities_gain,
        preferred_dividends=preferred_dividends,
        diluted_eps=diluted_eps,
        diluted_shares=diluted_shares,
        cfo=cfo,
        capex=capex,
        fcf=cfo - capex,
        cash=55_911,
        total_debt=98_165,
        equity=640_480,
        sbc=10.0,
        depreciation_amortization=8.0,
        buybacks=4.0,
        source="alphabet_official_ir",
        source_url="https://s206.q4cdn.com/example.pdf",
        taxonomy="us-gaap",
        validation_state="degraded" if kind != "FY" else "verified",
        source_artifact_sha256=str(year % 10) * 64,
        source_obtained_at_utc="2026-10-12T00:00:00+00:00",
        normalization_version="issuer-ir-manifest-v1",
    )


def _inputs() -> tuple[FinancialPeriod, FinancialPeriod, FinancialPeriod, list[FinancialPeriod]]:
    annual = _period(
        year=2025, kind="FY", start="2025-01-01", end="2025-12-31",
        revenue=402_836, operating_income=129_039, net_income=132_170,
        pretax_income=158_826, income_tax=26_656, cfo=164_713, capex=91_447,
        diluted_eps=10.81, diluted_shares=12_230, other_income=29_787,
        securities_gain=24_620,
    )
    prior = _period(
        year=2025, kind="YTD", start="2025-01-01", end="2025-06-30",
        revenue=186_662, operating_income=61_877, net_income=62_736,
        pretax_income=75_722, income_tax=12_986, cfo=63_897, capex=39_643,
        diluted_eps=5.12, diluted_shares=12_245, other_income=13_845,
        securities_gain=11_411,
    )
    current = _period(
        year=2026, kind="YTD", start="2026-01-01", end="2026-06-30",
        revenue=229_692, operating_income=80_466, net_income=174_771,
        common_income=174_685, preferred_dividends=86, pretax_income=216_165,
        income_tax=41_394, cfo=84_859, capex=80_598, diluted_eps=14.24,
        diluted_shares=12_274, other_income=135_699, securities_gain=135_803,
    )
    quarters = [
        _period(year=2025, kind="Q", start="2025-07-01", end="2025-09-30", revenue=102_346, operating_income=31_228, net_income=34_979, pretax_income=43_987, income_tax=9_008, cfo=48_414, capex=23_953, diluted_eps=2.87, diluted_shares=12_203, other_income=12_759, securities_gain=10_000),
        _period(year=2025, kind="Q", start="2025-10-01", end="2025-12-31", revenue=113_828, operating_income=35_934, net_income=34_455, pretax_income=39_117, income_tax=4_662, cfo=52_402, capex=27_851, diluted_eps=2.82, diluted_shares=12_228, other_income=3_183, securities_gain=3_000),
        _period(year=2026, kind="Q", start="2026-01-01", end="2026-03-31", revenue=109_896, operating_income=39_696, net_income=62_578, pretax_income=77_412, income_tax=14_834, cfo=45_790, capex=35_674, diluted_eps=5.11, diluted_shares=12_238, other_income=37_716, securities_gain=36_804),
        _period(year=2026, kind="Q", start="2026-04-01", end="2026-06-30", revenue=119_796, operating_income=40_770, net_income=112_193, common_income=112_107, preferred_dividends=86, pretax_income=138_753, income_tax=26_560, cfo=39_069, capex=44_924, diluted_eps=9.11, diluted_shares=12_309, other_income=97_983, securities_gain=98_999),
    ]
    # Make optional quarter sums agree with the FY/YTD bridge.
    for field in ("sbc", "depreciation_amortization", "buybacks"):
        annual_value = getattr(annual, field)
        current_value = getattr(current, field)
        prior_value = getattr(prior, field)
        target = annual_value + current_value - prior_value
        quarters = [replace(row, **{field: target / 4}) for row in quarters]
    return annual, current, prior, quarters


def test_ttm_bridge_reconstructs_flows_but_withholds_operational_eps() -> None:
    annual, current, prior, quarters = _inputs()
    ttm = build_ttm_period(
        annual=annual, current_ytd=current, prior_ytd=prior, quarters=quarters
    )

    assert ttm.period_type == "TTM"
    assert ttm.revenue == 445_866
    assert ttm.operating_income == 147_628
    assert ttm.net_income == 244_205
    assert ttm.common_net_income == 244_119
    assert ttm.cfo == 185_675
    assert ttm.capex == 132_402
    assert ttm.fcf == 53_273
    assert ttm.other_income_expense == 151_641
    assert ttm.securities_gain_loss == 149_012
    assert ttm.diluted_eps is None
    evidence = json.loads(ttm.derivation_inputs_json)
    assert evidence["eps_bridge_diagnostic"] == pytest.approx(19.93)
    assert evidence["eps_quarter_sum_diagnostic"] == pytest.approx(19.91)
    assert evidence["eps_operational"] is None


def test_ttm_bridge_rejects_quarter_sum_mismatch() -> None:
    annual, current, prior, quarters = _inputs()
    quarters[-1] = replace(quarters[-1], revenue=quarters[-1].revenue + 1)
    with pytest.raises(ValueError, match="quarter sum for revenue"):
        build_ttm_period(
            annual=annual, current_ytd=current, prior_ytd=prior, quarters=quarters
        )


def test_append_latest_ttm_is_idempotent() -> None:
    annual, current, prior, quarters = _inputs()
    first = append_latest_ttm([annual, current, prior, *quarters])
    second = append_latest_ttm(first)
    assert sum(row.period_type == "TTM" for row in first) == 1
    assert sum(row.period_type == "TTM" for row in second) == 1
