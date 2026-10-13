from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from stocklens.market_calendar import latest_completed_session

from .storage import atomic_write_bytes, atomic_write_json


ALPHA_VANTAGE_TERMS_URL = "https://www.alphavantage.co/terms_of_service/"
ALPHA_VANTAGE_DOCS_URL = "https://www.alphavantage.co/documentation/"
ALPHA_VANTAGE_SUPPORT_URL = "https://www.alphavantage.co/support/"


@dataclass(frozen=True)
class MarketCollectionResult:
    ticker: str
    status: str
    rows: int
    message: str
    as_of: str | None = None
    rows_returned: int = 0
    diagnostic_code: str | None = None
    retryable: bool = False
    provider_field: str | None = None


def diagnose_provider_payload(payload: dict[str, Any]) -> tuple[str, bool, str | None]:
    """Classify a non-series response without retaining provider text or secrets."""

    diagnostic_fields = ("Error Message", "Note", "Information")
    provider_field = next((field for field in diagnostic_fields if field in payload), None)
    text = str(payload.get(provider_field, "")).casefold() if provider_field else ""
    if any(token in text for token in ("rate limit", "call frequency", "25 requests", "too many")):
        return "rate_limit", True, provider_field
    if any(token in text for token in ("premium", "entitlement", "subscription")):
        return "entitlement_required", False, provider_field
    if any(token in text for token in ("invalid api key", "api key is invalid", "apikey")):
        return "authentication_failed", False, provider_field
    if provider_field == "Error Message" or any(token in text for token in ("invalid api call", "invalid symbol")):
        return "invalid_request_or_symbol", False, provider_field
    return "daily_series_missing", False, provider_field


def _http_diagnostic(status_code: int) -> tuple[str, bool]:
    if status_code == 429:
        return "rate_limit", True
    if status_code in {401, 403}:
        return "authentication_or_entitlement", False
    if 500 <= status_code <= 599:
        return "provider_unavailable", True
    return "http_error", False


def _validate_ohlcv(frame: pd.DataFrame) -> None:
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"OHLCV columns are missing: {sorted(missing)}")
    if frame.empty or frame["date"].duplicated().any():
        raise ValueError("OHLCV history is empty or has duplicate dates")
    numeric = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("OHLCV history contains non-numeric values")
    if (numeric[["open", "high", "low", "close"]] <= 0).any().any() or (numeric["volume"] < 0).any():
        raise ValueError("OHLCV history contains out-of-range values")
    if (numeric["high"] < numeric[["open", "close"]].max(axis=1)).any():
        raise ValueError("Daily high is below open or close")
    if (numeric["low"] > numeric[["open", "close"]].min(axis=1)).any():
        raise ValueError("Daily low is above open or close")


class AlphaVantagePersonalClient:
    """Local personal-use EOD collector requiring the user's own free key.

    The free endpoint exposes the latest 100 raw, as-traded sessions. Each call
    is merged into the ignored local cache so history can accumulate without a
    premium ``outputsize=full`` or adjusted-history entitlement.
    """

    endpoint = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str, *, session: requests.Session | None = None, now: datetime | None = None) -> None:
        if not api_key.strip():
            raise ValueError("ALPHAVANTAGE_API_KEY is required")
        self._api_key = api_key.strip()
        self._session = session or requests.Session()
        self._now = now

    def collect_daily(self, ticker: str, data_dir: str | Path) -> MarketCollectionResult:
        symbol = ticker.upper().strip()
        try:
            response = self._session.get(
                self.endpoint,
                params={"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact", "apikey": self._api_key},
                timeout=30,
            )
        except requests.exceptions.SSLError:
            return MarketCollectionResult(
                symbol, "blocked", 0, "Provider TLS validation failed; request details withheld",
                diagnostic_code="tls_error", retryable=False,
            )
        except requests.exceptions.Timeout:
            return MarketCollectionResult(
                symbol, "blocked", 0, "Provider request timed out; request details withheld",
                diagnostic_code="timeout", retryable=True,
            )
        except requests.exceptions.ConnectionError:
            return MarketCollectionResult(
                symbol, "blocked", 0, "Provider connection failed; DNS and connection details withheld",
                diagnostic_code="network_connection_failed", retryable=True,
            )
        except requests.exceptions.RequestException:
            return MarketCollectionResult(
                symbol, "blocked", 0, "Provider request failed; request details withheld",
                diagnostic_code="request_failed", retryable=False,
            )
        if response.status_code != 200:
            code, retryable = _http_diagnostic(response.status_code)
            return MarketCollectionResult(
                symbol,
                "blocked",
                0,
                f"Provider HTTP {response.status_code}; request URL and credentials withheld",
                diagnostic_code=code,
                retryable=retryable,
            )
        try:
            payload = response.json()
        except ValueError:
            return MarketCollectionResult(
                symbol,
                "blocked",
                0,
                "Provider returned non-JSON data; response body withheld",
                diagnostic_code="invalid_json",
            )
        series = payload.get("Time Series (Daily)")
        if not isinstance(series, dict) or not series:
            code, retryable, provider_field = diagnose_provider_payload(payload)
            messages = {
                "rate_limit": "Provider rate limit was reported",
                "entitlement_required": "Provider reported that this dataset requires another entitlement",
                "authentication_failed": "Provider rejected the API credential",
                "invalid_request_or_symbol": "Provider rejected the request or symbol",
                "daily_series_missing": "Provider response did not include daily history",
            }
            return MarketCollectionResult(
                symbol,
                "blocked",
                0,
                messages[code],
                diagnostic_code=code,
                retryable=retryable,
                provider_field=provider_field,
            )

        rows: list[dict[str, Any]] = []
        try:
            for date_text, values in series.items():
                rows.append({
                    "date": date_text,
                    "open": float(values["1. open"]),
                    "high": float(values["2. high"]),
                    "low": float(values["3. low"]),
                    "close": float(values["4. close"]),
                    "volume": int(values["5. volume"]),
                })
        except (KeyError, TypeError, ValueError) as exc:
            return MarketCollectionResult(
                symbol,
                "blocked",
                0,
                f"Invalid OHLCV payload structure: {type(exc).__name__}",
                diagnostic_code="invalid_ohlcv_payload",
            )

        returned = pd.DataFrame(rows)
        root = Path(data_dir)
        csv_path = root / "market" / f"{symbol}.csv"
        cached = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
        frame = pd.concat([cached, returned], ignore_index=True)
        frame = frame.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
        try:
            _validate_ohlcv(frame)
        except ValueError as exc:
            return MarketCollectionResult(
                symbol,
                "blocked",
                0,
                str(exc),
                rows_returned=len(returned),
                diagnostic_code="ohlcv_quality_failed",
            )

        collected_at = (self._now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        market_date = str(frame.iloc[-1]["date"])
        expected = latest_completed_session(collected_at).session_date
        freshness = "verified" if market_date == expected else "stale" if market_date < expected else "unverified"
        csv_payload = frame.to_csv(index=False).encode("utf-8")
        atomic_write_bytes(csv_path, csv_payload)
        manifest = {
            "schema_version": 2,
            "ticker": symbol,
            "provider": "Alpha Vantage",
            "dataset": "TIME_SERIES_DAILY",
            "classification": "personal_noncommercial_local_only",
            "terms_url": ALPHA_VANTAGE_TERMS_URL,
            "documentation_url": ALPHA_VANTAGE_DOCS_URL,
            "support_url": ALPHA_VANTAGE_SUPPORT_URL,
            "collected_at_utc": collected_at.isoformat(),
            "provider_market_date": str(returned["date"].max()),
            "market_date": market_date,
            "expected_session_date": expected,
            "freshness_status": freshness,
            "rows_returned": len(returned),
            "rows_cached": len(frame),
            "history_start": str(frame.iloc[0]["date"]),
            "history_end": market_date,
            "sha256": hashlib.sha256(csv_payload).hexdigest(),
            "price_policy": "raw_as_traded_unadjusted",
            "adjusted_prices_available": False,
            "is_realtime": False,
            "redistribution_authorized": False,
            "free_plan": {"daily_request_limit": 25, "outputsize": "compact_latest_100_sessions", "full_history": "premium_only", "adjusted_daily": "premium_only"},
        }
        atomic_write_json(root / "market" / f"{symbol}.manifest.json", manifest)
        return MarketCollectionResult(symbol, freshness, len(frame), "Raw EOD history merged into the local-only cache", market_date, len(returned))


def _price_series(frame: pd.DataFrame) -> tuple[pd.Series, str]:
    column = "adjusted_close" if "adjusted_close" in frame.columns else "close"
    values = pd.to_numeric(frame.get(column), errors="coerce").dropna()
    return values, column


def compute_technical_snapshot(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or "close" not in frame:
        return {"status": "unavailable", "reason": "No local daily close history"}
    close, column = _price_series(frame)
    if len(close) < 50:
        return {"status": "partial", "reason": "At least 50 daily closes are required for EMA50", "observations": int(len(close)), "price_policy": column}
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi14 = 100 - (100 / (1 + rs.iloc[-1])) if pd.notna(rs.iloc[-1]) else None
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1] if len(close) >= 200 else None
    drawdown = close.iloc[-1] / close.tail(252).max() - 1 if len(close) >= 252 else None
    volume_trend = None
    if "volume" in frame and len(frame) >= 40:
        volume = pd.to_numeric(frame["volume"], errors="coerce").dropna()
        if len(volume) >= 40 and volume.iloc[-40:-20].mean() > 0:
            volume_trend = volume.iloc[-20:].mean() / volume.iloc[-40:-20].mean() - 1
    missing = []
    if ema200 is None:
        missing.append("EMA200 requires 200 sessions")
    if drawdown is None:
        missing.append("52-week drawdown requires 252 sessions")
    if volume_trend is None:
        missing.append("volume trend requires 40 valid sessions")

    returns: dict[str, float | None] = {}
    for sessions in (5, 20, 63):
        returns[f"return_{sessions}d"] = (
            round(float(close.iloc[-1] / close.iloc[-sessions - 1] - 1), 6)
            if len(close) >= sessions + 1 else None
        )
    recent_ranges: dict[str, dict[str, float] | None] = {}
    ordered = frame.loc[close.index].copy()
    for sessions in (20, 50):
        if len(ordered) < sessions:
            recent_ranges[f"range_{sessions}d"] = None
            continue
        window = ordered.tail(sessions)
        low_values = pd.to_numeric(window["low"], errors="coerce") if "low" in window else pd.to_numeric(window["close"], errors="coerce")
        high_values = pd.to_numeric(window["high"], errors="coerce") if "high" in window else pd.to_numeric(window["close"], errors="coerce")
        recent_ranges[f"range_{sessions}d"] = {
            "low": round(float(low_values.min()), 4),
            "high": round(float(high_values.max()), 4),
        }
    running_peak = close.cummax()
    observed_max_drawdown = round(float((close / running_peak - 1).min()), 6)
    return {
        "status": "verified" if not missing else "partial",
        "latest_close": round(float(close.iloc[-1]), 4),
        "ema20": round(float(ema20), 4),
        "ema50": round(float(ema50), 4),
        "ema200": round(float(ema200), 4) if ema200 is not None else None,
        "rsi14": round(float(rsi14), 2) if rsi14 is not None else None,
        "drawdown_from_52w_high": round(float(drawdown), 6) if drawdown is not None else None,
        "volume_trend_20d_vs_prior_20d": round(float(volume_trend), 6) if volume_trend is not None else None,
        **returns,
        **recent_ranges,
        "observed_period_max_drawdown": observed_max_drawdown,
        "observations": int(len(close)),
        "price_policy": column,
        "missing_reasons": missing,
        "is_buy_signal": False,
        "note": "Descriptive indicators only; not an independently validated trading signal",
    }


def compute_relative_strength(stock: pd.DataFrame, benchmark: pd.DataFrame) -> dict[str, Any]:
    if stock.empty or benchmark.empty or "date" not in stock or "date" not in benchmark:
        return {"status": "unavailable", "reason": "Ticker and QQQ histories are both required"}
    stock_prices, stock_column = _price_series(stock)
    benchmark_prices, benchmark_column = _price_series(benchmark)
    left = pd.DataFrame({"date": stock.loc[stock_prices.index, "date"], "stock": stock_prices.values})
    right = pd.DataFrame({"date": benchmark.loc[benchmark_prices.index, "date"], "qqq": benchmark_prices.values})
    aligned = left.merge(right, on="date", how="inner").sort_values("date")
    output: dict[str, Any] = {
        "status": "partial", "aligned_observations": len(aligned),
        "price_policy": f"ticker={stock_column}, benchmark={benchmark_column}",
        "relative_return_63d": None, "relative_return_252d": None,
        "missing_reasons": [], "is_buy_signal": False,
    }
    for sessions in (63, 252):
        key = f"relative_return_{sessions}d"
        if len(aligned) < sessions + 1:
            output["missing_reasons"].append(f"{key} requires {sessions + 1} aligned sessions")
            continue
        window = aligned.tail(sessions + 1)
        stock_return = window.iloc[-1]["stock"] / window.iloc[0]["stock"] - 1
        qqq_return = window.iloc[-1]["qqq"] / window.iloc[0]["qqq"] - 1
        output[key] = round(float(stock_return - qqq_return), 6)
    if not output["missing_reasons"]:
        output["status"] = "verified"
    return output
