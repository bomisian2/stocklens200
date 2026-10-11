from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol

import requests


NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"
US_EXCHANGES = ("NMS", "NYQ", "NGM", "NCM", "ASE")
RETRYABLE_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """A provider failed without exposing response bodies or credentials."""


@dataclass(frozen=True)
class UniverseRecord:
    ticker: str
    name: str
    sector: str
    industry: str
    country: str
    market_cap: float
    price: float | None
    change_percent: float | None
    volume: float | None
    market_time_utc: str | None
    collected_at_utc: str
    source: str
    metadata_source: str
    issuer_key: str


@dataclass(frozen=True)
class ProviderResult:
    records: list[UniverseRecord]
    source: str
    candidate_count: int


class UniverseProvider(Protocol):
    name: str

    def fetch(self) -> ProviderResult: ...


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "").replace("%", "")
    if not text or text.lower() in {"n/a", "na", "none", "nan", "--"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper().replace("/", "-").replace(".", "-")


_SECURITY_WORDS = re.compile(
    r"\b(warrants?|rights?|units?|preferred|preference|senior notes?|debentures?|bonds?|"
    r"exchange traded|etf|etn|closed[- ]end fund)\b",
    re.IGNORECASE,
)
_ISSUER_NOISE = re.compile(
    r"\b(class\s+[a-z]|common stocks?|ordinary shares?|american depositary shares?|"
    r"american depository shares?|ads|adr|plc|incorporated|corporation|corp|company|limited|ltd)\b",
    re.IGNORECASE,
)


def is_company_security(symbol: str, name: str, market_cap: float | None) -> bool:
    """Keep listed operating-company equity; reject funds and derivative securities."""
    if not symbol or not market_cap or market_cap <= 0:
        return False
    if "^" in symbol or symbol.endswith(("-WS", "-WT", "-W", "-R", "-U")):
        return False
    return not bool(_SECURITY_WORDS.search(name or ""))


def issuer_key(name: str, ticker: str) -> str:
    """Best-effort issuer identity used to collapse multiple listed share classes."""
    cleaned = _ISSUER_NOISE.sub(" ", name or "")
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
    return cleaned or normalize_ticker(ticker)


def _utc_from_epoch(value: Any) -> str | None:
    epoch = _number(value)
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat()


class JsonHttpClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        last_error = "unknown error"
        for attempt in range(self.attempts):
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=(5, 45))
                if 200 <= response.status_code < 300:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("JSON root is not an object")
                    return payload
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUS:
                    break
            except (requests.RequestException, ValueError) as exc:
                last_error = type(exc).__name__
            if attempt + 1 < self.attempts:
                self.sleep(self.backoff_seconds * (2**attempt))
        raise ProviderError(f"request failed after {self.attempts} attempts ({last_error})")


class NasdaqUniverseProvider:
    name = "nasdaq_screener"

    def __init__(self, client: JsonHttpClient | None = None) -> None:
        self.client = client or JsonHttpClient()

    def fetch(self) -> ProviderResult:
        payload = self.client.get(
            NASDAQ_URL,
            params={"tableonly": "true", "limit": 25, "offset": 0, "download": "true"},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36 StockLens200/2.0"
                ),
            },
        )
        rows = ((payload.get("data") or {}).get("rows") or [])
        if not isinstance(rows, list) or len(rows) < 200:
            raise ProviderError("Nasdaq response did not contain a complete equity universe")

        collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        records: list[UniverseRecord] = []
        for row in rows:
            symbol = normalize_ticker(row.get("symbol"))
            name = str(row.get("name") or symbol).replace("\n", " ").strip()
            cap = _number(row.get("marketCap"))
            if not is_company_security(symbol, name, cap):
                continue
            records.append(
                UniverseRecord(
                    ticker=symbol,
                    name=name,
                    sector=str(row.get("sector") or "Unknown").strip(),
                    industry=str(row.get("industry") or "Unknown").replace("\n", " ").strip(),
                    country=str(row.get("country") or "Unknown").strip(),
                    market_cap=float(cap),
                    price=_number(row.get("lastsale")),
                    change_percent=_number(row.get("pctchange")),
                    volume=_number(row.get("volume")),
                    # The free Nasdaq screener exposes no authoritative quote timestamp
                    # (`data.asOf` is currently null). Collection time is never treated
                    # as market time; this result can verify/enrich a Yahoo snapshot but
                    # cannot independently replace a fresh last-good snapshot.
                    market_time_utc=None,
                    collected_at_utc=collected_at,
                    source=self.name,
                    metadata_source=self.name,
                    issuer_key=issuer_key(name, symbol),
                )
            )
        return ProviderResult(records=records, source=self.name, candidate_count=len(rows))


class YahooUniverseProvider:
    name = "yahoo_screener"

    def __init__(
        self,
        attempts: int = 3,
        backoff_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        screen: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self._screen = screen

    def _fetch_payload(self) -> dict[str, Any]:
        if self._screen is None:
            # Isolate yfinance because upstream cookie/crumb retries have occasionally
            # stalled. A hard subprocess deadline keeps the Actions job bounded.
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "stocklens.yahoo_worker"],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=60,
                )
                payload = json.loads(completed.stdout)
            except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
                raise ProviderError(f"bounded Yahoo worker failed ({type(exc).__name__})") from exc
            if not isinstance(payload, dict):
                raise ProviderError("bounded Yahoo worker returned an invalid payload")
            return payload
        query = {"exchange": US_EXCHANGES}
        return self._screen(query, size=250, sortField="intradaymarketcap", sortAsc=False)

    def fetch(self) -> ProviderResult:
        payload: dict[str, Any] | None = None
        last_error = "unknown error"
        for attempt in range(self.attempts):
            try:
                candidate = self._fetch_payload()
                rows = candidate.get("quotes") if isinstance(candidate, dict) else None
                if isinstance(rows, list) and len(rows) >= 200:
                    payload = candidate
                    break
                last_error = "incomplete payload"
            except Exception as exc:  # yfinance raises several HTTP-specific exception types
                last_error = type(exc).__name__
            if attempt + 1 < self.attempts:
                self.sleep(self.backoff_seconds * (2**attempt))
        if payload is None:
            raise ProviderError(f"Yahoo screener failed after {self.attempts} attempts ({last_error})")

        rows = payload.get("quotes") or []
        if not isinstance(rows, list) or len(rows) < 200:
            raise ProviderError("Yahoo response did not contain at least 200 equities")
        collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        records: list[UniverseRecord] = []
        for row in rows:
            symbol = normalize_ticker(row.get("symbol"))
            name = str(row.get("longName") or row.get("shortName") or symbol).strip()
            cap = _number(row.get("marketCap"))
            if not is_company_security(symbol, name, cap):
                continue
            records.append(
                UniverseRecord(
                    ticker=symbol,
                    name=name,
                    sector=str(row.get("sector") or "Unknown").strip(),
                    industry=str(row.get("industry") or "Unknown").strip(),
                    # Yahoo's `region` describes the screener market, not issuer domicile.
                    country="Unknown",
                    market_cap=float(cap),
                    price=_number(row.get("regularMarketPrice")),
                    change_percent=_number(row.get("regularMarketChangePercent")),
                    volume=_number(row.get("regularMarketVolume")),
                    market_time_utc=_utc_from_epoch(row.get("regularMarketTime")),
                    collected_at_utc=collected_at,
                    source=self.name,
                    metadata_source=self.name,
                    issuer_key=issuer_key(name, symbol),
                )
            )
        return ProviderResult(records=records, source=self.name, candidate_count=int(payload.get("total") or len(rows)))


def rank_companies(records: Iterable[UniverseRecord], limit: int = 200) -> list[UniverseRecord]:
    """Rank companies by market cap and retain one liquid listing per issuer."""
    best_by_issuer: dict[str, UniverseRecord] = {}
    for record in records:
        current = best_by_issuer.get(record.issuer_key)
        if current is None or (record.volume or 0) > (current.volume or 0):
            best_by_issuer[record.issuer_key] = record
    return sorted(best_by_issuer.values(), key=lambda row: row.market_cap, reverse=True)[:limit]


def enrich_metadata(
    records: Iterable[UniverseRecord], metadata_records: Iterable[UniverseRecord]
) -> list[UniverseRecord]:
    """Fill descriptive fields from an independent exact-ticker source."""
    metadata_by_ticker = {record.ticker: record for record in metadata_records}
    enriched: list[UniverseRecord] = []
    for record in records:
        metadata = metadata_by_ticker.get(record.ticker)
        if metadata is None:
            enriched.append(record)
            continue
        enriched.append(
            replace(
                record,
                sector=metadata.sector if metadata.sector != "Unknown" else record.sector,
                industry=metadata.industry if metadata.industry != "Unknown" else record.industry,
                country=metadata.country if metadata.country != "Unknown" else record.country,
                metadata_source=metadata.source,
            )
        )
    return enriched
