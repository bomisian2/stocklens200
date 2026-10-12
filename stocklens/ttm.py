from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from .financials import FinancialPeriod


TTM_NORMALIZATION_VERSION = "ttm-bridge-v1"
FLOW_FIELDS = (
    "revenue",
    "operating_income",
    "net_income",
    "common_net_income",
    "pretax_income",
    "income_tax",
    "other_income_expense",
    "securities_gain_loss",
    "preferred_dividends",
    "cfo",
    "capex",
    "fcf",
    "sbc",
    "depreciation_amortization",
    "buybacks",
    "dividends",
)
REQUIRED_FLOW_FIELDS = {
    "revenue",
    "operating_income",
    "net_income",
    "pretax_income",
    "income_tax",
    "cfo",
    "capex",
    "fcf",
}


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("TTM input timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _bridge_value(
    annual: FinancialPeriod,
    current_ytd: FinancialPeriod,
    prior_ytd: FinancialPeriod,
    field: str,
) -> float | None:
    values = [getattr(row, field) for row in (annual, current_ytd, prior_ytd)]
    if any(value is None for value in values):
        if field in REQUIRED_FLOW_FIELDS:
            raise ValueError(f"TTM bridge requires {field} in annual and both YTD periods")
        return None
    return float(values[0]) + float(values[1]) - float(values[2])


def _quarter_rows(
    periods: Iterable[FinancialPeriod], current_ytd: FinancialPeriod
) -> list[FinancialPeriod]:
    end = _date(current_ytd.period_end)
    start = end - timedelta(days=370)
    rows = sorted(
        [
            row
            for row in periods
            if row.ticker == current_ytd.ticker
            and row.period_type == "Q"
            and start < _date(row.period_end) <= end
        ],
        key=lambda row: row.period_end,
    )
    return rows[-4:]


def build_ttm_period(
    *,
    annual: FinancialPeriod,
    current_ytd: FinancialPeriod,
    prior_ytd: FinancialPeriod,
    quarters: Iterable[FinancialPeriod] = (),
) -> FinancialPeriod:
    """Build a point-in-time TTM period without manufacturing a GAAP TTM EPS.

    Flow values use ``prior fiscal year + current YTD - prior YTD``. Four
    single-quarter rows, when supplied, independently cross-check those totals.
    EPS diagnostics are recorded in provenance, but ``diluted_eps`` remains null
    because annual and YTD weighted-average diluted denominators are not additive.
    """

    identity = {(row.ticker, row.cik, row.currency) for row in (annual, current_ytd, prior_ytd)}
    if len(identity) != 1:
        raise ValueError("TTM inputs must have identical ticker, CIK, and currency")
    if annual.period_type != "FY" or current_ytd.period_type != "YTD" or prior_ytd.period_type != "YTD":
        raise ValueError("TTM bridge requires one FY and two YTD periods")
    current_end, prior_end, annual_end = map(
        _date, (current_ytd.period_end, prior_ytd.period_end, annual.period_end)
    )
    if (
        current_end.year != prior_end.year + 1
        or (current_end.month, current_end.day) != (prior_end.month, prior_end.day)
        or annual_end.year != prior_end.year
        or (annual_end.month, annual_end.day) != (12, 31)
    ):
        raise ValueError("TTM fiscal periods do not form FY + matching current/prior YTD bridge")

    values = {
        field: _bridge_value(annual, current_ytd, prior_ytd, field)
        for field in FLOW_FIELDS
    }
    if values["fcf"] is None or not math.isclose(
        values["fcf"], values["cfo"] - values["capex"], rel_tol=1e-9, abs_tol=1e-6
    ):
        raise ValueError("derived TTM FCF must equal TTM CFO minus TTM CAPEX")

    quarter_rows = sorted(list(quarters), key=lambda row: row.period_end)
    quarter_crosscheck: dict[str, float] = {}
    if quarter_rows:
        if len(quarter_rows) != 4 or any(row.period_type != "Q" for row in quarter_rows):
            raise ValueError("TTM quarter cross-check requires exactly four single-quarter rows")
        expected_ends = {(9, 30), (12, 31), (3, 31), (6, 30)}
        if {(_date(row.period_end).month, _date(row.period_end).day) for row in quarter_rows} != expected_ends:
            raise ValueError("TTM quarter cross-check rows are not four consecutive fiscal quarters")
        for field in REQUIRED_FLOW_FIELDS:
            raw = [getattr(row, field) for row in quarter_rows]
            if any(value is None for value in raw):
                raise ValueError(f"quarter cross-check requires {field} in all four quarters")
            quarter_sum = sum(float(value) for value in raw)
            if not math.isclose(quarter_sum, float(values[field]), rel_tol=1e-9, abs_tol=1e-6):
                raise ValueError(f"quarter sum for {field} does not match FY/YTD bridge")
            quarter_crosscheck[field] = quarter_sum

    eps_bridge = None
    if all(row.diluted_eps is not None for row in (annual, current_ytd, prior_ytd)):
        eps_bridge = annual.diluted_eps + current_ytd.diluted_eps - prior_ytd.diluted_eps
    eps_quarter_sum = None
    if quarter_rows and all(row.diluted_eps is not None for row in quarter_rows):
        eps_quarter_sum = sum(float(row.diluted_eps) for row in quarter_rows)

    inputs = [annual, current_ytd, prior_ytd, *quarter_rows]
    hashes = sorted({row.source_artifact_sha256 for row in inputs if row.source_artifact_sha256})
    derived_hash = hashlib.sha256("|".join(hashes).encode("utf-8")).hexdigest()
    available_at = max(_timestamp(row.available_at_utc) for row in inputs)
    obtained = max(
        (_timestamp(row.source_obtained_at_utc) for row in inputs if row.source_obtained_at_utc),
        default=available_at,
    )
    derivation = {
        "formula": "FY + current_YTD - prior_YTD",
        "input_periods": [
            {
                "period_type": row.period_type,
                "period_end": row.period_end,
                "accession": row.accession,
                "artifact_sha256": row.source_artifact_sha256,
            }
            for row in inputs
        ],
        "quarter_crosscheck": quarter_crosscheck,
        "eps_bridge_diagnostic": eps_bridge,
        "eps_quarter_sum_diagnostic": eps_quarter_sum,
        "eps_operational": None,
        "eps_reason": "annual/YTD and quarterly EPS use different weighted diluted denominators",
    }
    notes = (
        "derived TTM flow period; four-quarter sums cross-checked when available; "
        f"EPS bridge={eps_bridge!r}, quarter sum={eps_quarter_sum!r}; operational TTM EPS withheld"
    )
    return FinancialPeriod(
        ticker=annual.ticker,
        cik=annual.cik,
        company_name=annual.company_name,
        fiscal_year=current_end.year,
        period_type="TTM",
        period_start=(prior_end + timedelta(days=1)).isoformat(),
        period_end=current_end.isoformat(),
        filed_at=current_ytd.filed_at,
        available_at_utc=available_at.isoformat(),
        availability_basis="latest_publication_of_fy_ytd_ttm_bridge",
        availability_estimated=any(row.availability_estimated for row in inputs),
        form="derived_ttm",
        accession=current_ytd.accession,
        currency=annual.currency,
        diluted_eps=None,
        normalized_diluted_eps=None,
        diluted_shares=current_ytd.diluted_shares,
        normalized_net_income=None,
        cash=current_ytd.cash,
        total_debt=current_ytd.total_debt,
        equity=current_ytd.equity,
        source="issuer_ir_ttm_bridge",
        source_url=current_ytd.source_url,
        taxonomy=annual.taxonomy,
        validation_state="degraded",
        validation_notes=notes,
        source_artifact_sha256=derived_hash,
        source_obtained_at_utc=obtained.isoformat(),
        normalization_version=TTM_NORMALIZATION_VERSION,
        derivation_inputs_json=json.dumps(derivation, sort_keys=True),
        **values,
    )


def append_latest_ttm(periods: Iterable[FinancialPeriod]) -> list[FinancialPeriod]:
    """Append one latest TTM bridge per ticker when an exact FY/YTD trio exists."""

    output = [row for row in periods if row.period_type != "TTM"]
    for ticker in sorted({row.ticker for row in output}):
        ticker_rows = [row for row in output if row.ticker == ticker]
        ytd = sorted([row for row in ticker_rows if row.period_type == "YTD"], key=lambda row: row.period_end)
        if len(ytd) < 2:
            continue
        current_ytd = ytd[-1]
        prior_candidates = [
            row
            for row in ytd[:-1]
            if _date(row.period_end).year == _date(current_ytd.period_end).year - 1
            and (_date(row.period_end).month, _date(row.period_end).day)
            == (_date(current_ytd.period_end).month, _date(current_ytd.period_end).day)
        ]
        annual_candidates = [
            row
            for row in ticker_rows
            if row.period_type == "FY"
            and _date(row.period_end).year == _date(current_ytd.period_end).year - 1
        ]
        if not prior_candidates or not annual_candidates:
            continue
        output.append(
            build_ttm_period(
                annual=annual_candidates[-1],
                current_ytd=current_ytd,
                prior_ytd=prior_candidates[-1],
                quarters=_quarter_rows(ticker_rows, current_ytd),
            )
        )
    return sorted(output, key=lambda row: (row.ticker, row.period_end, row.period_type))
