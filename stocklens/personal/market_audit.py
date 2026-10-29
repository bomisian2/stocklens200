from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import exchange_calendars as xcals
import pandas as pd

from .market_data import _validate_ohlcv, compute_relative_strength, compute_technical_snapshot
from .storage import atomic_write_json, load_json


DEFAULT_TICKERS = ("AMAT", "AVGO", "QCOM", "QQQ")


def _safe_failure(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {
            "diagnostic_code": "status_missing",
            "reason": "No safe collection status is available",
            "retryable": False,
        }
    code = result.get("diagnostic_code") or "legacy_unclassified_response"
    reason = result.get("message") or "Collection was blocked without a safe reason"
    if code == "legacy_unclassified_response":
        reason = "The previous adapter did not preserve a safe provider error category"
    return {"diagnostic_code": code, "reason": reason, "retryable": bool(result.get("retryable", False))}


def audit_saved_market_data(
    data_dir: str | Path,
    tickers: Iterable[str] = DEFAULT_TICKERS,
    *,
    verified_at: datetime | None = None,
) -> dict[str, Any]:
    """Audit ignored local OHLCV files without making a provider request."""

    root = Path(data_dir)
    status = load_json(root / "status.json") or {}
    status_rows = {
        str(row.get("ticker", "")).upper(): row
        for row in status.get("market", {}).get("results", [])
    }
    frames: dict[str, pd.DataFrame] = {}
    results: list[dict[str, Any]] = []
    calendar = xcals.get_calendar("XNYS", start="2000-01-01", end="2035-12-31")

    for raw_ticker in tickers:
        ticker = raw_ticker.upper()
        csv_path = root / "market" / f"{ticker}.csv"
        manifest_path = root / "market" / f"{ticker}.manifest.json"
        if not csv_path.exists() or not manifest_path.exists():
            results.append({"ticker": ticker, "data_status": "blocked", **_safe_failure(status_rows.get(ticker))})
            continue
        try:
            payload = csv_path.read_bytes()
            frame = pd.read_csv(csv_path)
            _validate_ohlcv(frame)
            manifest = load_json(manifest_path) or {}
            dates = pd.to_datetime(frame["date"], errors="raise")
            expected = {
                session.date().isoformat()
                for session in calendar.sessions_in_range(
                    dates.min().date().isoformat(), dates.max().date().isoformat()
                )
            }
            actual = set(frame["date"].astype(str))
            sha_matches = hashlib.sha256(payload).hexdigest() == manifest.get("sha256")
            end_matches = str(frame.iloc[-1]["date"]) == manifest.get("market_date")
            technical = compute_technical_snapshot(frame)
            frames[ticker] = frame
            missing_sessions = sorted(expected - actual)
            extra_dates = sorted(actual - expected)
            data_status = "verified" if sha_matches and end_matches and not missing_sessions and not extra_dates else "failed"
            results.append(
                {
                    "ticker": ticker,
                    "data_status": data_status,
                    "freshness_status": manifest.get("freshness_status"),
                    "history_start": str(frame.iloc[0]["date"]),
                    "history_end": str(frame.iloc[-1]["date"]),
                    "rows": len(frame),
                    "duplicate_dates": int(frame["date"].duplicated().sum()),
                    "missing_xnys_sessions": missing_sessions,
                    "extra_non_xnys_dates": extra_dates,
                    "price_policy": manifest.get("price_policy"),
                    "adjusted_prices_available": manifest.get("adjusted_prices_available"),
                    "sha256_matches_manifest": sha_matches,
                    "market_date_matches_manifest": end_matches,
                    "technical": technical,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ticker": ticker,
                    "data_status": "failed",
                    "diagnostic_code": "local_audit_failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "retryable": False,
                }
            )

    relative = compute_relative_strength(frames.get("AMAT", pd.DataFrame()), frames.get("QQQ", pd.DataFrame()))
    summary = {
        "schema_version": 1,
        "verified_at_utc": (verified_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "network_calls_made": 0,
        "results": results,
        "relative_strength": {"ticker": "AMAT", "benchmark": "QQQ", **relative},
    }
    atomic_write_json(root / "audit" / "market" / "summary.json", summary)
    return summary
