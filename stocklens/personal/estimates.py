from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .market_data import (
    ALPHA_VANTAGE_DOCS_URL,
    ALPHA_VANTAGE_SUPPORT_URL,
    ALPHA_VANTAGE_TERMS_URL,
    diagnose_http_status,
    diagnose_provider_payload,
)
from .storage import atomic_write_bytes, atomic_write_json


@dataclass(frozen=True)
class EstimateCollectionResult:
    ticker: str
    status: str
    periods: int
    message: str
    diagnostic_code: str | None = None
    retryable: bool = False
    provider_field: str | None = None


def _number(value: Any) -> float | int | None:
    if value in (None, "", "None", "null", "-"):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def normalize_alpha_vantage_estimates(
    payload: dict[str, Any], *, ticker: str, collected_at_utc: str
) -> dict[str, Any]:
    """Normalize documented estimate arrays without inventing missing metadata.

    Alpha Vantage documents annual/quarterly EPS and revenue estimates, analyst
    counts, and revision history.  The provider does not document a guaranteed
    point-in-time snapshot timestamp for each observation, so collection time is
    retained separately and operational eligibility remains false.
    """

    groups = (("annual", payload.get("annualEarnings")), ("quarterly", payload.get("quarterlyEarnings")))
    periods: list[dict[str, Any]] = []
    for frequency, rows in groups:
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise ValueError(f"{frequency} estimate collection is not a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{frequency} estimate row is not an object")
            fiscal_date = row.get("fiscalDateEnding") or row.get("date")
            if not fiscal_date:
                raise ValueError(f"{frequency} estimate row has no fiscal date")
            eps_average = _number(row.get("eps_estimate_average") or row.get("epsAvg"))
            revenue_average = _number(row.get("revenue_estimate_average") or row.get("revenueAvg"))
            eps_count = _number(row.get("eps_estimate_analyst_count") or row.get("numAnalystsEps"))
            revenue_count = _number(row.get("revenue_estimate_analyst_count") or row.get("numAnalystsRevenue"))
            if eps_average is None and revenue_average is None:
                raise ValueError(f"{frequency} estimate row has neither EPS nor revenue average")
            if eps_average is not None and (eps_count is None or eps_count <= 0):
                raise ValueError(f"{frequency} EPS estimate has no positive analyst count")
            if revenue_average is not None and (revenue_count is None or revenue_count <= 0):
                raise ValueError(f"{frequency} revenue estimate has no positive analyst count")
            revisions = {
                key: _number(value)
                for key, value in row.items()
                if "revision" in key.casefold()
            }
            periods.append(
                {
                    "frequency": frequency,
                    "fiscal_date_ending": str(fiscal_date),
                    "provider_horizon": row.get("horizon"),
                    "provider_observation_date": row.get("date") if row.get("fiscalDateEnding") else None,
                    "eps": {
                        "average": eps_average,
                        "low": _number(row.get("eps_estimate_low") or row.get("epsLow")),
                        "high": _number(row.get("eps_estimate_high") or row.get("epsHigh")),
                        "analyst_count": eps_count,
                    },
                    "revenue": {
                        "average": revenue_average,
                        "low": _number(row.get("revenue_estimate_low") or row.get("revenueLow")),
                        "high": _number(row.get("revenue_estimate_high") or row.get("revenueHigh")),
                        "analyst_count": revenue_count,
                    },
                    "revisions": revisions,
                }
            )
    if not periods:
        raise ValueError("No annual or quarterly estimate periods were returned")
    return {
        "schema_version": 1,
        "ticker": ticker,
        "status": "research_only",
        "data_kind": "analyst_consensus",
        "provider": "Alpha Vantage",
        "dataset": "EARNINGS_ESTIMATES",
        "collected_at_utc": collected_at_utc,
        "provider_snapshot_time": None,
        "provider_snapshot_time_status": "not_documented_in_contract",
        "periods": periods,
        "operational_eligible": False,
        "reason": "Provider point-in-time snapshot semantics and ticker-level free entitlement require live validation",
        "issuer_guidance_mixed": False,
    }


class AlphaVantageEstimatesClient:
    endpoint = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str, *, session: requests.Session | None = None, now: datetime | None = None) -> None:
        if not api_key.strip():
            raise ValueError("ALPHAVANTAGE_API_KEY is required")
        self._api_key = api_key.strip()
        self._session = session or requests.Session()
        self._now = now

    def collect(self, ticker: str, data_dir: str | Path) -> EstimateCollectionResult:
        symbol = ticker.upper().strip()
        try:
            response = self._session.get(
                self.endpoint,
                params={"function": "EARNINGS_ESTIMATES", "symbol": symbol, "apikey": self._api_key},
                timeout=30,
            )
        except requests.exceptions.SSLError:
            return EstimateCollectionResult(symbol, "blocked", 0, "Provider TLS validation failed", "tls_error")
        except requests.exceptions.Timeout:
            return EstimateCollectionResult(symbol, "blocked", 0, "Provider request timed out", "timeout", True)
        except requests.exceptions.ConnectionError:
            return EstimateCollectionResult(symbol, "blocked", 0, "Provider connection failed", "network_connection_failed", True)
        except requests.exceptions.RequestException:
            return EstimateCollectionResult(symbol, "blocked", 0, "Provider request failed", "request_failed")
        if response.status_code != 200:
            code, retryable = diagnose_http_status(response.status_code)
            return EstimateCollectionResult(
                symbol, "blocked", 0, f"Provider HTTP {response.status_code}; request details withheld", code, retryable
            )
        try:
            payload = response.json()
        except ValueError:
            return EstimateCollectionResult(symbol, "blocked", 0, "Provider returned non-JSON data", "invalid_json")
        if not isinstance(payload, dict):
            return EstimateCollectionResult(symbol, "blocked", 0, "Provider returned a non-object payload", "invalid_json_shape")
        if not payload.get("annualEarnings") and not payload.get("quarterlyEarnings"):
            code, retryable, provider_field = diagnose_provider_payload(payload)
            if code == "daily_series_missing":
                code = "estimate_periods_missing"
            return EstimateCollectionResult(symbol, "blocked", 0, "Provider response did not include estimate periods", code, retryable, provider_field)

        collected_at = (self._now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        try:
            normalized = normalize_alpha_vantage_estimates(payload, ticker=symbol, collected_at_utc=collected_at)
        except ValueError as exc:
            return EstimateCollectionResult(symbol, "blocked", 0, str(exc), "estimate_schema_failed")

        raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        root = Path(data_dir)
        raw_path = root / "estimates" / "raw" / f"{symbol}.json"
        normalized_path = root / "estimates" / "normalized" / f"{symbol}.json"
        atomic_write_bytes(raw_path, raw_payload)
        normalized["source"] = {
            "terms_url": ALPHA_VANTAGE_TERMS_URL,
            "documentation_url": f"{ALPHA_VANTAGE_DOCS_URL}#earnings-estimates",
            "support_url": ALPHA_VANTAGE_SUPPORT_URL,
            "classification": "personal_noncommercial_local_only",
            "redistribution_authorized": False,
            "raw_sha256": hashlib.sha256(raw_payload).hexdigest(),
        }
        atomic_write_json(normalized_path, normalized)
        return EstimateCollectionResult(
            symbol, "research_only", len(normalized["periods"]), "Analyst estimates saved to ignored local-only storage"
        )
