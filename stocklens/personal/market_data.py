from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .storage import atomic_write_bytes, atomic_write_json


ALPHA_VANTAGE_TERMS_URL = "https://www.alphavantage.co/terms_of_service/"


@dataclass(frozen=True)
class MarketCollectionResult:
    ticker: str
    status: str
    rows: int
    message: str
    as_of: str | None = None


class AlphaVantagePersonalClient:
    """Personal/non-commercial EOD collector requiring the user's own free key.

    The adapter deliberately does not expose the key in errors or stored files.
    It requests one daily-history endpoint per ticker and labels the latest row as
    an end-of-day observation, never as a real-time quote.
    """

    endpoint = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str, *, session: requests.Session | None = None) -> None:
        if not api_key.strip():
            raise ValueError("ALPHAVANTAGE_API_KEY is required")
        self._api_key = api_key.strip()
        self._session = session or requests.Session()

    def collect_daily(self, ticker: str, data_dir: str | Path) -> MarketCollectionResult:
        symbol = ticker.upper().strip()
        response = self._session.get(
            self.endpoint,
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "outputsize": "full",
                "apikey": self._api_key,
            },
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
            message = "Provider quota, entitlement, or symbol response did not include daily history"
            return MarketCollectionResult(symbol, "blocked", 0, message)

        rows: list[dict[str, Any]] = []
        for date_text, values in series.items():
            rows.append(
                {
                    "date": date_text,
                    "open": float(values["1. open"]),
                    "high": float(values["2. high"]),
                    "low": float(values["3. low"]),
                    "close": float(values["4. close"]),
                    "volume": int(values["5. volume"]),
                }
            )
        frame = pd.DataFrame(rows).sort_values("date")
        csv_payload = frame.to_csv(index=False).encode("utf-8")
        root = Path(data_dir)
        atomic_write_bytes(root / "market" / f"{symbol}.csv", csv_payload)
        manifest = {
            "ticker": symbol,
            "provider": "Alpha Vantage",
            "dataset": "TIME_SERIES_DAILY",
            "classification": "personal_noncommercial_local_only",
            "terms_url": ALPHA_VANTAGE_TERMS_URL,
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "market_date": str(frame.iloc[-1]["date"]),
            "rows": len(frame),
            "sha256": hashlib.sha256(csv_payload).hexdigest(),
            "is_realtime": False,
            "redistribution_authorized": False,
        }
        atomic_write_json(root / "market" / f"{symbol}.manifest.json", manifest)
        return MarketCollectionResult(
            symbol,
            "verified",
            len(frame),
            "Daily EOD history saved locally",
            manifest["market_date"],
        )


def compute_technical_snapshot(frame: pd.DataFrame) -> dict[str, float | str | None]:
    if frame.empty or "close" not in frame:
        return {"status": "unavailable", "reason": "No local daily close history"}
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if len(close) < 50:
        return {"status": "partial", "reason": "At least 50 daily closes are required"}
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi14 = 100 - (100 / (1 + rs.iloc[-1])) if pd.notna(rs.iloc[-1]) else None
    trailing = close.tail(min(252, len(close)))
    peak = trailing.max()
    drawdown = close.iloc[-1] / peak - 1 if peak else None
    return {
        "status": "verified" if len(close) >= 252 else "partial",
        "latest_close": round(float(close.iloc[-1]), 4),
        "ema20": round(float(ema20), 4),
        "ema50": round(float(ema50), 4),
        "rsi14": round(float(rsi14), 2) if rsi14 is not None else None,
        "drawdown_from_52w_high": round(float(drawdown), 6) if drawdown is not None else None,
        "observations": int(len(close)),
        "note": "52-week drawdown is partial when fewer than 252 sessions are available",
    }
