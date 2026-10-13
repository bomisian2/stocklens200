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
        response = self._session.get(
            self.endpoint,
            params={"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact", "apikey": self._api_key},
            timeout=30,
        )
        if response.status_code != 200:
            return MarketCollectionResult(symbol, "blocked", 0, f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return MarketCollectionResult(symbol, "blocked", 0, "Provider returned non-JSON data")
        series = payload.get("Time Series (Daily)")
        if not isinstance(series, dict) or not series:
            return MarketCollectionResult(symbol, "blocked", 0, "Provider quota, entitlement, or symbol response did not include daily history")

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
            return MarketCollectionResult(symbol, "blocked", 0, f"Invalid OHLCV payload: {type(exc).__name__}")

        returned = pd.DataFrame(rows)
        root = Path(data_dir)
        csv_path = root / "market" / f"{symbol}.csv"
        cached = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
        frame = pd.concat([cached, returned], ignore_index=True)
        frame = frame.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
        try:
            _validate_ohlcv(frame)
        except ValueError as exc:
            return MarketCollectionResult(symbol, "blocked", 0, str(exc), rows_returned=len(returned))

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
    return {
        "status": "verified" if not missing else "partial",
        "latest_close": round(float(close.iloc[-1]), 4),
        "ema20": round(float(ema20), 4),
        "ema50": round(float(ema50), 4),
        "ema200": round(float(ema200), 4) if ema200 is not None else None,
        "rsi14": round(float(rsi14), 2) if rsi14 is not None else None,
        "drawdown_from_52w_high": round(float(drawdown), 6) if drawdown is not None else None,
        "volume_trend_20d_vs_prior_20d": round(float(volume_trend), 6) if volume_trend is not None else None,
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
