from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd


HISTORY_COLUMNS = ["date", "ticker", "open", "high", "low", "close", "volume"]
MIN_HISTORY_ROWS = 600


def history_error_category(exc: ValueError) -> str:
    message = str(exc)
    if "row count" in message:
        return "insufficient_history"
    if "latest session" in message:
        return "stale_history"
    return "history_unavailable"


@dataclass(frozen=True)
class HistoryCollection:
    frames: dict[str, pd.DataFrame]
    fresh_tickers: set[str]
    cached_tickers: set[str]
    unavailable_tickers: set[str]
    errors: list[dict]


def validate_history(
    frame: pd.DataFrame,
    *,
    ticker: str,
    expected_session: date,
    minimum_rows: int = MIN_HISTORY_ROWS,
) -> pd.DataFrame:
    missing = set(HISTORY_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"history columns missing: {sorted(missing)}")
    output = frame.loc[:, HISTORY_COLUMNS].copy()
    output["ticker"] = output["ticker"].astype(str).str.upper()
    output = output[output["ticker"].eq(ticker.upper())]
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        output[column] = pd.to_numeric(output[column], errors="coerce")
    output = output.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    output = output.sort_values("date").drop_duplicates("date", keep="last")
    if len(output) < minimum_rows:
        raise ValueError(f"history row count {len(output)} < {minimum_rows}")
    if (output[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("history contains non-positive prices")
    if (output["volume"] < 0).any():
        raise ValueError("history contains negative volume")
    if (output["high"] < output[["open", "close"]].max(axis=1)).any():
        raise ValueError("history high is below open or close")
    if (output["low"] > output[["open", "close"]].min(axis=1)).any():
        raise ValueError("history low is above open or close")
    latest = output["date"].iloc[-1].date()
    if latest != expected_session:
        raise ValueError(
            f"history latest session {latest.isoformat()} != {expected_session.isoformat()}"
        )
    return output.reset_index(drop=True)


class YahooHistoryCollector:
    def __init__(
        self,
        *,
        cache_dir: str | Path,
        batch_size: int = 20,
        timeout_seconds: int = 90,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.batch_size = batch_size
        self.timeout_seconds = timeout_seconds

    def _cache_path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker.upper()}.csv"

    def _load_cache(self, ticker: str, expected_session: date) -> pd.DataFrame | None:
        path = self._cache_path(ticker)
        if not path.exists():
            return None
        try:
            return validate_history(
                pd.read_csv(path), ticker=ticker, expected_session=expected_session
            )
        except (OSError, ValueError, pd.errors.ParserError):
            return None

    def _save_cache(self, ticker: str, frame: pd.DataFrame) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_path(ticker)
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{ticker}.", suffix=".tmp", dir=self.cache_dir
        )
        os.close(handle)
        temp = Path(temp_name)
        try:
            frame.to_csv(temp, index=False, encoding="utf-8")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

    def _download_batch(
        self, tickers: list[str], *, start: date, end_exclusive: date
    ) -> pd.DataFrame:
        self.cache_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="stocklens-history-",
            dir=self.cache_dir.parent,
            ignore_cleanup_errors=True,
        ) as directory:
            output = Path(directory) / "history.csv"
            command = [
                sys.executable, "-m", "stocklens.history_worker", "--tickers", *tickers,
                "--start", start.isoformat(), "--end", end_exclusive.isoformat(),
                "--output", str(output),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or not output.exists():
                raise RuntimeError(
                    f"history worker failed ({completed.returncode}, stderr redacted)"
                )
            return pd.read_csv(output)

    def collect(
        self,
        tickers: Iterable[str],
        *,
        expected_session: date,
        lookback_days: int = 365 * 6,
        allow_network: bool = True,
    ) -> HistoryCollection:
        requested = list(dict.fromkeys(str(value).upper() for value in tickers))
        frames: dict[str, pd.DataFrame] = {}
        fresh: set[str] = set()
        cached: set[str] = set()
        unavailable: set[str] = set()
        errors: list[dict] = []
        start = expected_session - timedelta(days=lookback_days)
        end_exclusive = expected_session + timedelta(days=1)
        for offset in range(0, len(requested), self.batch_size):
            requested_batch = requested[offset : offset + self.batch_size]
            batch: list[str] = []
            for ticker in requested_batch:
                verified_cache = self._load_cache(ticker, expected_session)
                if verified_cache is not None:
                    frames[ticker] = verified_cache
                    cached.add(ticker)
                else:
                    batch.append(ticker)
            if not batch:
                continue
            if not allow_network:
                unavailable.update(batch)
                errors.extend({
                    "ticker": ticker,
                    "category": "verified_cache_unavailable",
                    "reason": "network collection disabled",
                } for ticker in batch)
                continue
            try:
                raw = self._download_batch(batch, start=start, end_exclusive=end_exclusive)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                errors.append({
                    "tickers": batch,
                    "category": "provider_batch_failure",
                    "error_type": type(exc).__name__,
                })
                raw = pd.DataFrame(columns=HISTORY_COLUMNS)
            for ticker in batch:
                try:
                    verified = validate_history(
                        raw, ticker=ticker, expected_session=expected_session
                    )
                except ValueError as exc:
                    try:
                        individual = self._download_batch(
                            [ticker], start=start, end_exclusive=end_exclusive
                        )
                        verified = validate_history(
                            individual, ticker=ticker, expected_session=expected_session
                        )
                        errors.append({
                            "ticker": ticker,
                            "category": "individual_fallback_succeeded",
                        })
                    except (RuntimeError, subprocess.TimeoutExpired, ValueError):
                        verified = None
                    if verified is not None:
                        frames[ticker] = verified
                        fresh.add(ticker)
                        self._save_cache(ticker, verified)
                        continue
                    fallback = self._load_cache(ticker, expected_session)
                    if fallback is None:
                        unavailable.add(ticker)
                        errors.append({
                            "ticker": ticker,
                            "category": history_error_category(exc),
                            "error_type": type(exc).__name__,
                            "reason": str(exc)[:160],
                        })
                        continue
                    frames[ticker] = fallback
                    cached.add(ticker)
                    continue
                frames[ticker] = verified
                fresh.add(ticker)
                self._save_cache(ticker, verified)
        return HistoryCollection(frames, fresh, cached, unavailable, errors)

