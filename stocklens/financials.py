from __future__ import annotations

import json
import socket
import ssl
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlparse

import requests


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_COMPANY_FACTS_BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SEC_SUBMISSIONS_BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
SEC_SOURCE = "sec_edgar_companyfacts"
KNOWN_CIKS = {
    # Stable SEC registrant identifiers for the mandatory valuation samples.
    "GOOGL": 1652044,
    "AMAT": 6951,
    "AVGO": 1730168,
    "QCOM": 804328,
    "INTC": 50863,
}


def validate_ticker_cik(
    ticker: str,
    cik: int,
    *,
    expected: dict[str, int] | None = None,
    provider: str = "sec",
) -> None:
    """Fail closed when a ticker is mapped to a different SEC registrant."""
    normalized = normalize_sec_ticker(ticker)
    reference = expected if expected is not None else KNOWN_CIKS
    expected_cik = reference.get(normalized)
    if expected_cik is not None and int(cik) != int(expected_cik):
        raise FinancialDataError(ProviderDiagnostic(
            provider=provider,
            domain="local",
            endpoint=normalized,
            category="ticker_cik_mismatch",
            error_type=f"expected_CIK{int(expected_cik):010d}_received_CIK{int(cik):010d}",
        ))


def merge_cik_maps(*mappings: dict[str, int], provider: str = "sec") -> dict[str, int]:
    """Merge mappings without allowing a later source to silently replace an issuer."""
    merged: dict[str, int] = {}
    for mapping in mappings:
        for raw_ticker, raw_cik in mapping.items():
            ticker = normalize_sec_ticker(raw_ticker)
            cik = int(raw_cik)
            validate_ticker_cik(ticker, cik, provider=provider)
            if ticker in merged and merged[ticker] != cik:
                raise FinancialDataError(ProviderDiagnostic(
                    provider=provider,
                    domain="local",
                    endpoint=ticker,
                    category="ticker_cik_mismatch",
                    error_type=f"conflicting_CIK{merged[ticker]:010d}_and_CIK{cik:010d}",
                ))
            merged[ticker] = cik
    return merged

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


@dataclass(frozen=True)
class ProviderDiagnostic:
    provider: str
    domain: str
    endpoint: str
    category: str
    http_status: int | None = None
    timeout_seconds: float | None = None
    error_type: str | None = None
    response_headers: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FinancialDataError(RuntimeError):
    """A bounded provider failure carrying only allow-listed diagnostics."""

    def __init__(self, diagnostic: ProviderDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(json.dumps(diagnostic.to_dict(), sort_keys=True))


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
    availability_basis: str
    availability_estimated: bool
    form: str
    accession: str
    currency: str
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    common_net_income: float | None = None
    pretax_income: float | None = None
    income_tax: float | None = None
    other_income_expense: float | None = None
    securities_gain_loss: float | None = None
    equity_securities_gain_loss: float | None = None
    preferred_dividends: float | None = None
    normalized_net_income: float | None = None
    diluted_eps: float | None = None
    normalized_diluted_eps: float | None = None
    diluted_shares: float | None = None
    cfo: float | None = None
    capex: float | None = None
    fcf: float | None = None
    cash: float | None = None
    marketable_securities: float | None = None
    total_debt: float | None = None
    equity: float | None = None
    sbc: float | None = None
    depreciation_amortization: float | None = None
    buybacks: float | None = None
    dividends: float | None = None
    source: str = SEC_SOURCE
    source_url: str = ""
    taxonomy: str = "us-gaap"
    validation_state: str = "unverified"
    validation_notes: str = ""
    source_artifact_sha256: str = ""
    source_obtained_at_utc: str = ""
    normalization_version: str = "sec-gaap-v1"
    derivation_inputs_json: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SecClient:
    """Small retrying SEC client constrained below the 10 requests/sec policy."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        user_agent: str = "StockLens200/2.1 (+https://github.com/bomisian2/stocklens200; mailto:bomisian2@users.noreply.github.com)",
        attempts: int = 4,
        minimum_interval: float = 0.4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.user_agent = user_agent
        self.attempts = attempts
        self.minimum_interval = minimum_interval
        self.sleep = sleep
        self._last_request = 0.0

    def get_json(self, url: str) -> dict[str, Any]:
        domain = urlparse(url).hostname or "unknown"
        endpoint = _endpoint_name(url)
        last_diagnostic = ProviderDiagnostic("sec_api", domain, endpoint, "unknown")
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
                last_diagnostic = ProviderDiagnostic(
                    provider="sec_api",
                    domain=domain,
                    endpoint=endpoint,
                    category="http",
                    http_status=int(response.status_code),
                    timeout_seconds=45.0,
                    response_headers=_safe_headers(response.headers),
                )
                if response.status_code == 403:
                    break
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    break
            except requests.ConnectTimeout as exc:
                last_diagnostic = ProviderDiagnostic("sec_api", domain, endpoint, "connect_timeout", timeout_seconds=5.0, error_type=type(exc).__name__)
            except requests.ReadTimeout as exc:
                last_diagnostic = ProviderDiagnostic("sec_api", domain, endpoint, "read_timeout", timeout_seconds=45.0, error_type=type(exc).__name__)
            except requests.exceptions.SSLError as exc:
                last_diagnostic = ProviderDiagnostic("sec_api", domain, endpoint, "tls", error_type=_root_exception_name(exc, ssl.SSLError))
            except requests.ConnectionError as exc:
                category = "dns" if _has_exception(exc, socket.gaierror) else "connection"
                last_diagnostic = ProviderDiagnostic("sec_api", domain, endpoint, category, error_type=_root_exception_name(exc))
            except (requests.RequestException, ValueError) as exc:
                last_diagnostic = ProviderDiagnostic("sec_api", domain, endpoint, "response", error_type=type(exc).__name__)
            if attempt + 1 < self.attempts:
                self.sleep(min(30.0, 2.0**attempt))
        raise FinancialDataError(last_diagnostic)


def _endpoint_name(url: str) -> str:
    path = urlparse(url).path.lower()
    if "companyfacts" in path:
        return "companyfacts"
    if "submissions" in path:
        return "submissions"
    if "company_tickers" in path:
        return "company_tickers"
    return "other"


def _safe_headers(headers: Any) -> dict[str, str]:
    allowed = {"server", "content-type", "retry-after", "date", "cache-control", "content-length", "via"}
    return {
        str(key).lower(): str(value)[:160]
        for key, value in headers.items()
        if str(key).lower() in allowed
    }


def _has_exception(exc: BaseException, expected: type[BaseException]) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, expected):
            return True
        current = current.__cause__ or current.__context__
    return False


def _root_exception_name(exc: BaseException, preferred: type[BaseException] | None = None) -> str:
    current: BaseException = exc
    selected = type(exc).__name__
    while current.__cause__ is not None or current.__context__ is not None:
        current = current.__cause__ or current.__context__  # type: ignore[assignment]
        if preferred is None or isinstance(current, preferred):
            selected = type(current).__name__
    return selected


def probe_financial_source(
    url: str,
    *,
    provider: str,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 20.0),
) -> ProviderDiagnostic:
    """Probe headers only; never consume or log a response body."""
    domain = urlparse(url).hostname or "unknown"
    endpoint = _endpoint_name(url)
    client = session or requests.Session()
    try:
        response = client.get(
            url,
            headers={
                "User-Agent": "StockLens200/2.1 (+https://github.com/bomisian2/stocklens200; mailto:bomisian2@users.noreply.github.com)",
                "Accept": "application/json, application/zip;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=timeout,
            stream=True,
        )
        diagnostic = ProviderDiagnostic(
            provider=provider, domain=domain, endpoint=endpoint,
            category="ok" if response.status_code == 200 else "http",
            http_status=int(response.status_code), timeout_seconds=timeout[1],
            response_headers=_safe_headers(response.headers),
        )
        response.close()
        return diagnostic
    except requests.ConnectTimeout as exc:
        return ProviderDiagnostic(provider, domain, endpoint, "connect_timeout", timeout_seconds=timeout[0], error_type=type(exc).__name__)
    except requests.ReadTimeout as exc:
        return ProviderDiagnostic(provider, domain, endpoint, "read_timeout", timeout_seconds=timeout[1], error_type=type(exc).__name__)
    except requests.exceptions.SSLError as exc:
        return ProviderDiagnostic(provider, domain, endpoint, "tls", error_type=_root_exception_name(exc, ssl.SSLError))
    except requests.ConnectionError as exc:
        category = "dns" if _has_exception(exc, socket.gaierror) else "connection"
        return ProviderDiagnostic(provider, domain, endpoint, category, error_type=_root_exception_name(exc))
    except requests.RequestException as exc:
        return ProviderDiagnostic(provider, domain, endpoint, "request", error_type=type(exc).__name__)


class FinancialProvider(Protocol):
    name: str

    def fetch_periods(self, ticker: str, cik: int, *, as_of: datetime) -> list[FinancialPeriod]: ...


class SecApiProvider:
    name = "sec_api"

    def __init__(self, client: SecClient | None = None) -> None:
        self.client = client or SecClient()

    def fetch_periods(self, ticker: str, cik: int, *, as_of: datetime) -> list[FinancialPeriod]:
        validate_ticker_cik(ticker, cik, provider=self.name)
        return fetch_company_periods(self.client, ticker, cik, as_of=as_of)


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


@dataclass(frozen=True)
class Availability:
    timestamp_utc: str
    basis: str
    estimated: bool


def _available_at(row: dict[str, Any], acceptance: dict[str, str]) -> Availability:
    accession = str(row.get("accn") or "")
    if accession in acceptance:
        return Availability(acceptance[accession], "sec_acceptance_timestamp", False)
    # A filing date has no intraday release time. Midnight on that date would
    # introduce look-ahead. End of the following UTC day is intentionally
    # conservative and remains explicitly marked as an estimate.
    filed = date.fromisoformat(str(row["filed"]))
    conservative = datetime.combine(filed + timedelta(days=1), dt_time.max, tzinfo=timezone.utc)
    return Availability(
        conservative.replace(microsecond=0).isoformat(),
        "estimated_end_of_next_utc_day_from_filed_date",
        True,
    )


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
    facts: dict[str, Any], metric: str, period_type: str, as_of: datetime,
    acceptance: dict[str, str],
) -> list[dict[str, Any]]:
    gaap = facts.get("us-gaap") or {}
    for tag in FACT_ALIASES[metric]:
        fact = gaap.get(tag)
        if not isinstance(fact, dict):
            continue
        rows = [
            row for row in _unit_rows(fact, metric)
            if _eligible_fact_row(row, period_type, as_of)
            and datetime.fromisoformat(_available_at(row, acceptance).timestamp_utc) <= as_of
        ]
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
    source: str = SEC_SOURCE,
    source_artifact_sha256: str = "",
    source_obtained_at_utc: str = "",
) -> list[FinancialPeriod]:
    """Normalize point-in-time annual SEC facts and the latest standalone quarter."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    acceptance = accession_acceptance_map(submissions)
    facts = companyfacts.get("facts") or {}
    company_name = str(companyfacts.get("entityName") or ticker)
    metric_rows = {
        metric: _metric_rows(facts, metric, "FY", as_of, acceptance) for metric in FACT_ALIASES
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
            key=lambda row: (_available_at(row, acceptance).timestamp_utc, str(row.get("accn") or "")),
        )
        availability_record = max(
            (_available_at(row, acceptance) for row in selected_rows),
            key=lambda item: item.timestamp_utc,
        )
        available_at = availability_record.timestamp_utc
        availability = datetime.fromisoformat(available_at)
        if availability > as_of:
            continue
        notes: list[str] = []
        if availability_record.estimated:
            notes.append("SEC acceptance timestamp unavailable; availability uses a conservative estimate")
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
                availability_basis=availability_record.basis,
                availability_estimated=availability_record.estimated,
                form=str(anchor.get("form")),
                accession=accession,
                currency="USD",
                fcf=fcf,
                total_debt=debt,
                validation_state=state,
                validation_notes="; ".join(notes),
                source=source,
                source_url=source_url,
                source_artifact_sha256=source_artifact_sha256,
                source_obtained_at_utc=source_obtained_at_utc,
                **values,
            )
        )

    quarter_rows = {
        metric: _metric_rows(facts, metric, "Q", as_of, acceptance) for metric in FACT_ALIASES
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
            latest_q = max(selected_q, key=lambda row: (_available_at(row, acceptance).timestamp_utc, str(row.get("accn") or "")))
            availability_q_record = max(
                (_available_at(row, acceptance) for row in selected_q),
                key=lambda item: item.timestamp_utc,
            )
            available_q = availability_q_record.timestamp_utc
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
                quarter_notes = ["standalone latest quarter; guidance is not inferred from XBRL"]
                if availability_q_record.estimated:
                    quarter_notes.append(
                        "SEC acceptance timestamp unavailable; availability uses a conservative estimate"
                    )
                quarter_complete = all(
                    q_values.get(metric) is not None for metric in ("revenue", "net_income")
                )
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
                        availability_basis=availability_q_record.basis,
                        availability_estimated=availability_q_record.estimated,
                        form=str(latest_q.get("form")),
                        accession=accession_q,
                        currency="USD",
                        fcf=q_fcf,
                        total_debt=q_debt,
                        validation_state=(
                            "verified"
                            if quarter_complete and not availability_q_record.estimated
                            else "degraded"
                        ),
                        validation_notes="; ".join(quarter_notes),
                        source=source,
                        source_url=(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_q.replace('-', '')}/" if accession_q else ""),
                        source_artifact_sha256=source_artifact_sha256,
                        source_obtained_at_utc=source_obtained_at_utc,
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

