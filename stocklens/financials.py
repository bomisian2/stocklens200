from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timezone
from typing import Any, Callable, Iterable

import requests


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_SOURCE = "sec_edgar_companyfacts"
KNOWN_CIKS = {
    # Stable SEC registrant identifiers for the mandatory four-company sample.
    "GOOGL": 1652044,
    "AMAT": 6951,
    "AVGO": 1730168,
    "QCOM": 804328,
}

# Ordered aliases. The first usable tag wins so the same filing always produces
# the same normalized row. Values are GAAP unless the taxonomy says otherwise.
FACT_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ),
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
    "income_tax": ("IncomeTaxExpenseBenefit",),
    "diluted_eps": ("EarningsPerShareDiluted",),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "cfo": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ),
    "cash": (
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalentsAtCarryingValue",
    ),
    "debt_total": (
        "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebt",
    ),
    "debt_current": (
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtCurrent",
        "ShortTermBorrowings",
    ),
    "debt_noncurrent": (
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "LongTermDebtNoncurrent",
    ),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "sbc": ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    "depreciation_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
    ),
    "buybacks": ("PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"),
}


class FinancialDataError(RuntimeError):
    """A bounded provider or validation failure without response-body leakage."""


@dataclass(frozen=True)
class FinancialPeriod:
    ticker: str
    cik: int
    company_name: str
    fiscal_year: int
    period_type: str
    period_start: str | None
    period_end: str
    filed_at: str
    available_at_utc: str
    form: str
    accession: str
    currency: str
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    pretax_income: float | None = None
    income_tax: float | None = None
    diluted_eps: float | None = None
    diluted_shares: float | None = None
    cfo: float | None = None
    capex: float | None = None
    fcf: float | None = None
    cash: float | None = None
    total_debt: float | None = None
    equity: float | None = None
    sbc: float | None = None
    depreciation_amortization: float | None = None
    buybacks: float | None = None
    source: str = SEC_SOURCE
    source_url: str = ""
    taxonomy: str = "us-gaap"
    validation_state: str = "unverified"
    validation_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SecClient:
    """Small retrying SEC client constrained below the 10 requests/sec policy."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        user_agent: str = "StockLens200/2.0 bomisian2@users.noreply.github.com",
        attempts: int = 4,
        minimum_interval: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.user_agent = user_agent
        self.attempts = attempts
        self.minimum_interval = minimum_interval
        self.sleep = sleep
        self._last_request = 0.0

    def get_json(self, url: str) -> dict[str, Any]:
        last_error = "unknown"
        for attempt in range(self.attempts):
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.minimum_interval:
                self.sleep(self.minimum_interval - elapsed)
            try:
                response = self.session.get(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept-Encoding": "gzip, deflate",
                        "Accept": "application/json",
                    },
                    timeout=(5, 45),
                )
                self._last_request = time.monotonic()
                if response.status_code == 200:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("JSON root is not an object")
                    return payload
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in {403, 408, 425, 429, 500, 502, 503, 504}:
                    break
            except (requests.RequestException, ValueError) as exc:
                last_error = type(exc).__name__
            if attempt + 1 < self.attempts:
                self.sleep(min(30.0, 2.0**attempt))
        raise FinancialDataError(f"SEC request failed after {self.attempts} attempts ({last_error})")


def normalize_sec_ticker(ticker: str) -> str:
    return str(ticker).strip().upper().replace("-", ".")


def build_cik_map(payload: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in payload.values():
        if not isinstance(row, dict):
            continue
        ticker = normalize_sec_ticker(str(row.get("ticker") or ""))
        try:
            cik = int(row.get("cik_str"))
        except (TypeError, ValueError):
            continue
        if ticker:
            result[ticker] = cik
    return result


def accession_acceptance_map(submissions: dict[str, Any]) -> dict[str, str]:
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    accessions = recent.get("accessionNumber") or []
    accepted = recent.get("acceptanceDateTime") or []
    return {
        str(accession): _iso_utc(str(value))
        for accession, value in zip(accessions, accepted)
        if accession and value
    }


def _iso_utc(value: str) -> str:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _available_at(row: dict[str, Any], acceptance: dict[str, str]) -> str:
    accession = str(row.get("accn") or "")
    if accession in acceptance:
        return acceptance[accession]
    # Filing date alone has no release time. Midnight *after* the filing date is
    # conservative and prevents same-day look-ahead in point-in-time evaluation.
    filed = date.fromisoformat(str(row["filed"]))
    next_day = datetime.combine(filed, dt_time.min, tzinfo=timezone.utc)
    return next_day.isoformat()


def _unit_rows(fact: dict[str, Any], metric: str) -> list[dict[str, Any]]:
    units = fact.get("units") or {}
    preferred = ("USD/shares",) if metric == "diluted_eps" else ("shares",) if metric == "diluted_shares" else ("USD",)
    for unit in preferred:
        rows = units.get(unit)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _duration_days(row: dict[str, Any]) -> int | None:
    if not row.get("start") or not row.get("end"):
        return None
    try:
        return (date.fromisoformat(str(row["end"])) - date.fromisoformat(str(row["start"]))).days
    except ValueError:
        return None


def _eligible_fact_row(row: dict[str, Any], period_type: str, as_of: datetime) -> bool:
    form = str(row.get("form") or "")
    if period_type == "FY":
        if form not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
            return False
        duration = _duration_days(row)
        if duration is not None and not 300 <= duration <= 430:
            return False
    else:
        if form not in {"10-Q", "10-Q/A", "6-K", "6-K/A"}:
            return False
        duration = _duration_days(row)
        if duration is not None and not 65 <= duration <= 120:
            return False
    try:
        filed = datetime.fromisoformat(str(row["filed"])).replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return False
    return filed <= as_of


def _metric_rows(
    facts: dict[str, Any], metric: str, period_type: str, as_of: datetime
) -> list[dict[str, Any]]:
    gaap = facts.get("us-gaap") or {}
    for tag in FACT_ALIASES[metric]:
        fact = gaap.get(tag)
        if not isinstance(fact, dict):
            continue
        rows = [row for row in _unit_rows(fact, metric) if _eligible_fact_row(row, period_type, as_of)]
        if rows:
            return rows
    return []


def _select_by_year(rows: Iterable[dict[str, Any]], year: int) -> dict[str, Any] | None:
    matching = []
    for row in rows:
        row_year = row.get("fy")
        try:
            row_year = int(row_year)
        except (TypeError, ValueError):
            try:
                row_year = date.fromisoformat(str(row.get("end"))).year
            except ValueError:
                continue
        if row_year == year:
            matching.append(row)
    if not matching:
        return None
    # Later filings contain split-adjusted/restated comparatives. Restricting all
    # candidates to as_of makes this deterministic without future information.
    return max(matching, key=lambda row: (str(row.get("filed") or ""), str(row.get("accn") or "")))


def _value(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    try:
        value = float(row.get("val"))
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def extract_financial_periods(
    *,
    ticker: str,
    cik: int,
    companyfacts: dict[str, Any],
    submissions: dict[str, Any],
    as_of: datetime,
    annual_years: int = 5,
) -> list[FinancialPeriod]:
    """Normalize point-in-time annual SEC facts and the latest standalone quarter."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    acceptance = accession_acceptance_map(submissions)
    facts = companyfacts.get("facts") or {}
    company_name = str(companyfacts.get("entityName") or ticker)
    metric_rows = {
        metric: _metric_rows(facts, metric, "FY", as_of) for metric in FACT_ALIASES
    }
    years = sorted(
        {
            int(row["fy"])
            for rows in metric_rows.values()
            for row in rows
            if str(row.get("fy") or "").isdigit()
        },
        reverse=True,
    )[:annual_years]
    periods: list[FinancialPeriod] = []
    for year in sorted(years):
        chosen = {metric: _select_by_year(rows, year) for metric, rows in metric_rows.items()}
        anchor = chosen.get("revenue") or chosen.get("net_income") or chosen.get("cfo")
        if not anchor:
            continue
        values = {metric: _value(row) for metric, row in chosen.items()}
        debt = values.pop("debt_total")
        if debt is None:
            debt_parts = [values.pop("debt_current"), values.pop("debt_noncurrent")]
            debt = sum(value for value in debt_parts if value is not None) if any(value is not None for value in debt_parts) else None
        else:
            values.pop("debt_current")
            values.pop("debt_noncurrent")
        cfo, capex = values.get("cfo"), values.get("capex")
        fcf = cfo - capex if cfo is not None and capex is not None else None
        selected_rows = [row for row in chosen.values() if row]
        latest_row = max(
            selected_rows,
            key=lambda row: (_available_at(row, acceptance), str(row.get("accn") or "")),
        )
        available_at = max(_available_at(row, acceptance) for row in selected_rows)
        availability = datetime.fromisoformat(available_at)
        if availability > as_of:
            continue
        notes: list[str] = []
        derived_eps = None
        if values.get("net_income") is not None and values.get("diluted_shares") not in {None, 0}:
            derived_eps = values["net_income"] / values["diluted_shares"]
        reported_eps = values.get("diluted_eps")
        if reported_eps is not None and derived_eps is not None:
            denominator = max(abs(reported_eps), 0.01)
            if abs(derived_eps - reported_eps) / denominator > 0.20:
                notes.append("reported EPS differs from net income / diluted shares by more than 20%")
        required = [values.get("revenue"), values.get("net_income"), values.get("cfo"), values.get("diluted_shares")]
        state = "verified" if all(value is not None for value in required) and not notes else "degraded"
        accession = str(latest_row.get("accn") or "")
        source_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
            if accession
            else ""
        )
        periods.append(
            FinancialPeriod(
                ticker=ticker,
                cik=cik,
                company_name=company_name,
                fiscal_year=year,
                period_type="FY",
                period_start=str(anchor.get("start")) if anchor.get("start") else None,
                period_end=str(anchor.get("end")),
                filed_at=str(latest_row.get("filed")),
                available_at_utc=available_at,
                form=str(anchor.get("form")),
                accession=accession,
                currency="USD",
                fcf=fcf,
                total_debt=debt,
                validation_state=state,
                validation_notes="; ".join(notes),
                source_url=source_url,
                **values,
            )
        )

    quarter_rows = {
        metric: _metric_rows(facts, metric, "Q", as_of) for metric in FACT_ALIASES
    }
    quarter_ends = sorted(
        {
            str(row.get("end"))
            for rows in quarter_rows.values()
            for row in rows
            if row.get("end")
        },
        reverse=True,
    )
    if quarter_ends:
        quarter_end = quarter_ends[0]
        chosen_q: dict[str, dict[str, Any] | None] = {}
        for metric, rows in quarter_rows.items():
            matches = [row for row in rows if str(row.get("end")) == quarter_end]
            chosen_q[metric] = max(
                matches,
                key=lambda row: (str(row.get("filed") or ""), str(row.get("accn") or "")),
            ) if matches else None
        anchor_q = chosen_q.get("revenue") or chosen_q.get("net_income") or chosen_q.get("cfo")
        if anchor_q:
            selected_q = [row for row in chosen_q.values() if row]
            latest_q = max(selected_q, key=lambda row: (_available_at(row, acceptance), str(row.get("accn") or "")))
            available_q = max(_available_at(row, acceptance) for row in selected_q)
            if datetime.fromisoformat(available_q) <= as_of:
                q_values = {metric: _value(row) for metric, row in chosen_q.items()}
                q_debt = q_values.pop("debt_total")
                if q_debt is None:
                    debt_parts = [q_values.pop("debt_current"), q_values.pop("debt_noncurrent")]
                    q_debt = sum(value for value in debt_parts if value is not None) if any(value is not None for value in debt_parts) else None
                else:
                    q_values.pop("debt_current")
                    q_values.pop("debt_noncurrent")
                q_cfo, q_capex = q_values.get("cfo"), q_values.get("capex")
                q_fcf = q_cfo - q_capex if q_cfo is not None and q_capex is not None else None
                accession_q = str(latest_q.get("accn") or "")
                periods.append(
                    FinancialPeriod(
                        ticker=ticker,
                        cik=cik,
                        company_name=company_name,
                        fiscal_year=int(latest_q.get("fy") or date.fromisoformat(quarter_end).year),
                        period_type="Q",
                        period_start=str(anchor_q.get("start")) if anchor_q.get("start") else None,
                        period_end=quarter_end,
                        filed_at=str(latest_q.get("filed")),
                        available_at_utc=available_q,
                        form=str(latest_q.get("form")),
                        accession=accession_q,
                        currency="USD",
                        fcf=q_fcf,
                        total_debt=q_debt,
                        validation_state="verified" if all(q_values.get(metric) is not None for metric in ("revenue", "net_income")) else "degraded",
                        validation_notes="standalone latest quarter; guidance is not inferred from XBRL",
                        source_url=(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_q.replace('-', '')}/" if accession_q else ""),
                        **q_values,
                    )
                )
    return periods


def fetch_company_periods(
    client: SecClient, ticker: str, cik: int, *, as_of: datetime
) -> list[FinancialPeriod]:
    facts = client.get_json(SEC_COMPANY_FACTS_URL.format(cik=cik))
    submissions = client.get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
    return extract_financial_periods(
        ticker=ticker,
        cik=cik,
        companyfacts=facts,
        submissions=submissions,
        as_of=as_of,
    )

