"""Official, local-only macro snapshot collector for the research regime model.

The collector intentionally uses public read-only downloads that require no
credential. Raw responses and normalized snapshots belong under the ignored
``private_data`` tree. A failed attempt never replaces the last-good snapshot.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .market_regime import (
    REGIME_MINIMUM_COVERAGE,
    build_market_regime,
    build_qqq_proxy_observations,
)
from .storage import atomic_write_bytes, atomic_write_json


MACRO_COLLECTOR_VERSION = "official-macro-snapshot-v1"
FRED_SERIES = (
    "DFF",
    "DGS2",
    "DGS10",
    "CPIAUCSL",
    "PCEPI",
    "UNRATE",
    "PAYEMS",
    "BAMLH0A0HYM2",
    "DTWEXBGS",
)
FRED_DOWNLOAD_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv?"
    "cosd=2024-01-01&id=" + ",".join(FRED_SERIES)
)
CBOE_VIX_DOWNLOAD_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)
FRED_TERMS_URL = "https://fred.stlouisfed.org/docs/api/terms_of_use.html"
CBOE_VIX_SOURCE_PAGE = "https://www.cboe.com/tradable-products/vix/vix-historical-data"
USER_AGENT = "StockLens200-personal-research/1.0 (local-only; no redistribution)"


class OfficialMacroSnapshotError(RuntimeError):
    """Raised when collection or normalization cannot protect the evidence contract."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class OfficialMacroCollectionResult:
    status: str
    snapshot_path: str
    observation_count: int
    overall_coverage: float
    collected_at: str
    source_hashes: dict[str, str]
    requests_made: int
    http_results: dict[str, dict[str, Any]]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_final_url(name: str, final_url: str) -> None:
    host = (urlparse(final_url).hostname or "").lower()
    allowed = {
        "fred": {"fred.stlouisfed.org"},
        "cboe_vix": {"cdn.cboe.com", "cdn-api.cboe.com"},
    }[name]
    if host not in allowed:
        raise OfficialMacroSnapshotError(
            f"{name} redirected outside the approved official domains: {host or 'missing-host'}"
        )


def _parse_iso_or_us_date(value: str) -> date:
    cleaned = value.strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    raise OfficialMacroSnapshotError(f"Unsupported date format: {cleaned!r}")


def _finite_float(value: str) -> float | None:
    cleaned = value.strip()
    if not cleaned or cleaned == ".":
        return None
    try:
        parsed = float(cleaned)
    except ValueError as exc:
        raise OfficialMacroSnapshotError(f"Non-numeric official observation: {cleaned!r}") from exc
    if not math.isfinite(parsed):
        raise OfficialMacroSnapshotError("Official observation must be finite")
    return parsed


def parse_fred_csv(payload: bytes) -> dict[str, list[tuple[date, float]]]:
    """Parse a FRED graph CSV response without filling missing values."""

    try:
        encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = payload.decode(encoding)
    except UnicodeDecodeError as exc:
        raise OfficialMacroSnapshotError("FRED response is not UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise OfficialMacroSnapshotError("FRED CSV has no header")
    date_field = next(
        (field for field in ("observation_date", "DATE", "date") if field in reader.fieldnames),
        None,
    )
    if date_field is None:
        raise OfficialMacroSnapshotError("FRED CSV is missing its observation date")
    missing_series = set(FRED_SERIES) - set(reader.fieldnames)
    if missing_series:
        raise OfficialMacroSnapshotError(f"FRED CSV missing series: {sorted(missing_series)}")
    result: dict[str, list[tuple[date, float]]] = {series: [] for series in FRED_SERIES}
    seen: set[tuple[str, date]] = set()
    for row in reader:
        observed_on = _parse_iso_or_us_date(str(row.get(date_field, "")))
        for series in FRED_SERIES:
            value = _finite_float(str(row.get(series, "")))
            if value is None:
                continue
            key = (series, observed_on)
            if key in seen:
                raise OfficialMacroSnapshotError(
                    f"Duplicate FRED observation: {series} {observed_on.isoformat()}"
                )
            seen.add(key)
            result[series].append((observed_on, value))
    for series, values in result.items():
        values.sort(key=lambda item: item[0])
        if not values:
            raise OfficialMacroSnapshotError(f"FRED series has no observations: {series}")
    return result


def parse_cboe_vix_csv(payload: bytes) -> tuple[date, float]:
    """Return the latest official daily VIX close."""

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OfficialMacroSnapshotError("Cboe VIX response is not UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise OfficialMacroSnapshotError("Cboe VIX CSV has no header")
    normalized = {field.strip().upper(): field for field in reader.fieldnames}
    if "DATE" not in normalized or "CLOSE" not in normalized:
        raise OfficialMacroSnapshotError("Cboe VIX CSV is missing DATE or CLOSE")
    observations: list[tuple[date, float]] = []
    seen: set[date] = set()
    for row in reader:
        observed_on = _parse_iso_or_us_date(str(row.get(normalized["DATE"], "")))
        value = _finite_float(str(row.get(normalized["CLOSE"], "")))
        if value is None:
            continue
        if observed_on in seen:
            raise OfficialMacroSnapshotError(
                f"Duplicate Cboe VIX observation: {observed_on.isoformat()}"
            )
        seen.add(observed_on)
        observations.append((observed_on, value))
    if not observations:
        raise OfficialMacroSnapshotError("Cboe VIX CSV has no closing observations")
    return max(observations, key=lambda item: item[0])


def _month_shift(value: date, months: int) -> tuple[int, int]:
    index = value.year * 12 + value.month - 1 + months
    return index // 12, index % 12 + 1


def _monthly_value(values: list[tuple[date, float]], year: int, month: int) -> tuple[date, float]:
    matches = [item for item in values if item[0].year == year and item[0].month == month]
    if not matches:
        raise OfficialMacroSnapshotError(f"Missing monthly observation for {year:04d}-{month:02d}")
    return max(matches, key=lambda item: item[0])


def _monthly_change_3m(values: list[tuple[date, float]], *, ratio: bool) -> tuple[date, float]:
    latest_date, latest = values[-1]
    prior_year, prior_month = _month_shift(latest_date, -3)
    _, prior = _monthly_value(values, prior_year, prior_month)
    if ratio:
        if prior == 0:
            raise OfficialMacroSnapshotError("Three-month ratio denominator is zero")
        return latest_date, latest / prior - 1
    return latest_date, latest - prior


def _yoy_trend_3m(values: list[tuple[date, float]]) -> tuple[date, float]:
    latest_date, latest = values[-1]
    prior3_year, prior3_month = _month_shift(latest_date, -3)
    prior12_year, prior12_month = _month_shift(latest_date, -12)
    prior15_year, prior15_month = _month_shift(latest_date, -15)
    _, value_3m = _monthly_value(values, prior3_year, prior3_month)
    _, value_12m = _monthly_value(values, prior12_year, prior12_month)
    _, value_15m = _monthly_value(values, prior15_year, prior15_month)
    if value_12m == 0 or value_15m == 0:
        raise OfficialMacroSnapshotError("YoY denominator is zero")
    latest_yoy_pct = (latest / value_12m - 1) * 100
    prior_yoy_pct = (value_3m / value_15m - 1) * 100
    return latest_date, latest_yoy_pct - prior_yoy_pct


def _latest_common(
    left: list[tuple[date, float]],
    right: list[tuple[date, float]],
) -> tuple[date, float, float]:
    left_map = dict(left)
    right_map = dict(right)
    common = sorted(set(left_map) & set(right_map))
    if not common:
        raise OfficialMacroSnapshotError("Treasury series have no common observation date")
    observed_on = common[-1]
    return observed_on, left_map[observed_on], right_map[observed_on]


def _observation(
    metric_id: str,
    value: float,
    unit: str,
    source: str,
    source_hash: str,
    as_of: date,
    available_at: str,
    frequency: str,
    calculation_basis: str,
    *,
    status: str = "proxy",
) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "value": float(value),
        "unit": unit,
        "source": source,
        "source_hash": source_hash,
        "as_of": as_of.isoformat(),
        "available_at": available_at,
        "frequency": frequency,
        "status": status,
        "availability_status": "current_snapshot_proxy",
        "calculation_basis": calculation_basis,
    }


def normalize_official_macro_responses(
    fred_payload: bytes,
    vix_payload: bytes,
    *,
    collected_at: str,
) -> list[dict[str, Any]]:
    """Normalize official current snapshots into the market-regime contract."""

    collected = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
    if collected.tzinfo is None:
        raise OfficialMacroSnapshotError("collected_at must include a timezone")
    available_at = collected.astimezone(timezone.utc).isoformat()
    fred_hash = _sha256(fred_payload)
    vix_hash = _sha256(vix_payload)
    series = parse_fred_csv(fred_payload)
    vix_date, vix_close = parse_cboe_vix_csv(vix_payload)

    dff_date, dff = series["DFF"][-1]
    dgs2_date, dgs2 = series["DGS2"][-1]
    dgs10_date, dgs10 = series["DGS10"][-1]
    spread_date, common_2y, common_10y = _latest_common(series["DGS2"], series["DGS10"])
    unemp_date, unemp_change = _monthly_change_3m(series["UNRATE"], ratio=False)
    jobs_date, jobs_growth = _monthly_change_3m(series["PAYEMS"], ratio=True)
    cpi_date, cpi_trend = _yoy_trend_3m(series["CPIAUCSL"])
    pce_date, pce_trend = _yoy_trend_3m(series["PCEPI"])
    hy_date, hy_oas = series["BAMLH0A0HYM2"][-1]
    dollar_values = series["DTWEXBGS"]
    if len(dollar_values) < 64:
        raise OfficialMacroSnapshotError("Broad dollar index needs 64 daily observations")
    dollar_date, dollar_latest = dollar_values[-1]
    dollar_prior = dollar_values[-64][1]
    if dollar_prior == 0:
        raise OfficialMacroSnapshotError("Broad dollar return denominator is zero")

    fred_source = "FRED official graph CSV"
    return [
        _observation("policy_rate_pct", dff, "percent", f"{fred_source}:DFF", fred_hash, dff_date, available_at, "daily", "latest DFF observation; publication time unavailable, collected_at used"),
        _observation("treasury_2y_yield_pct", dgs2, "percent", f"{fred_source}:DGS2", fred_hash, dgs2_date, available_at, "daily", "latest DGS2 observation; publication time unavailable, collected_at used"),
        _observation("treasury_10y_yield_pct", dgs10, "percent", f"{fred_source}:DGS10", fred_hash, dgs10_date, available_at, "daily", "latest DGS10 observation; publication time unavailable, collected_at used"),
        _observation("treasury_2s10s_spread_pp", common_10y - common_2y, "percentage_point", f"{fred_source}:DGS10-DGS2", fred_hash, spread_date, available_at, "daily", "same-date DGS10 minus DGS2; publication time unavailable, collected_at used"),
        _observation("unemployment_rate_change_3m_pp", unemp_change, "percentage_point", f"{fred_source}:UNRATE", fred_hash, unemp_date, available_at, "monthly", "latest UNRATE minus the observation three calendar months earlier"),
        _observation("employment_growth_3m_ratio", jobs_growth, "ratio", f"{fred_source}:PAYEMS", fred_hash, jobs_date, available_at, "monthly", "latest PAYEMS / PAYEMS three calendar months earlier - 1"),
        _observation("cpi_yoy_trend_3m_pp", cpi_trend, "percentage_point", f"{fred_source}:CPIAUCSL", fred_hash, cpi_date, available_at, "monthly", "latest CPI YoY percent minus CPI YoY percent three months earlier"),
        _observation("pce_yoy_trend_3m_pp", pce_trend, "percentage_point", f"{fred_source}:PCEPI", fred_hash, pce_date, available_at, "monthly", "latest PCE price index YoY percent minus its YoY percent three months earlier"),
        _observation("broad_dollar_return_63d_ratio", dollar_latest / dollar_prior - 1, "ratio", f"{fred_source}:DTWEXBGS", fred_hash, dollar_date, available_at, "daily", "Federal Reserve nominal broad dollar index latest / 63 observations earlier - 1; this is not DXY"),
        _observation("hy_oas_pct", hy_oas, "percent", f"{fred_source}:BAMLH0A0HYM2", fred_hash, hy_date, available_at, "daily", "latest FRED-hosted ICE BofA US High Yield OAS; local individual research only"),
        _observation("vix_index", vix_close, "index_point", "Cboe official VIX historical daily CSV", vix_hash, vix_date, available_at, "daily", "latest official VIX daily close; publication time unavailable, collected_at used"),
    ]


def collect_official_macro_snapshot(
    data_dir: str | Path,
    *,
    session: requests.Session | None = None,
    now: datetime | None = None,
    qqq_csv_path: str | Path | None = None,
    qqq_manifest_path: str | Path | None = None,
    minimum_coverage: float = REGIME_MINIMUM_COVERAGE,
) -> OfficialMacroCollectionResult:
    """Fetch once from FRED and Cboe and atomically publish a local snapshot."""

    client = session or requests.Session()
    collected = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,*/*;q=0.5",
        "Accept-Encoding": "identity",
    }
    payloads: dict[str, bytes] = {}
    http_results: dict[str, dict[str, Any]] = {}
    for name, url in (("fred", FRED_DOWNLOAD_URL), ("cboe_vix", CBOE_VIX_DOWNLOAD_URL)):
        try:
            response = client.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OfficialMacroSnapshotError(
                f"{name} collection failed ({type(exc).__name__}); last-good snapshot preserved",
                details={"requests_made": len(http_results) + 1, "http_results": http_results},
            ) from exc
        final_url = str(response.url)
        _validate_final_url(name, final_url)
        payloads[name] = bytes(response.content)
        http_results[name] = {
            "requested_url": url,
            "final_url": final_url,
            "http_status": int(response.status_code),
            "content_type": str(response.headers.get("Content-Type") or ""),
            "content_length": len(response.content),
        }

    source_hashes = {name: _sha256(payload) for name, payload in payloads.items()}
    try:
        observations = normalize_official_macro_responses(
            payloads["fred"],
            payloads["cboe_vix"],
            collected_at=collected.isoformat(),
        )
    except OfficialMacroSnapshotError as exc:
        raise OfficialMacroSnapshotError(
            f"{exc}; last-good snapshot preserved",
            details={
                "requests_made": 2,
                "http_results": http_results,
                "source_hashes": source_hashes,
            },
        ) from exc
    if qqq_csv_path and qqq_manifest_path:
        observations.extend(build_qqq_proxy_observations(qqq_csv_path, qqq_manifest_path))
    regime = build_market_regime(observations, analysis_as_of=collected.isoformat())
    if regime["overall_coverage"] < minimum_coverage:
        raise OfficialMacroSnapshotError(
            f"coverage {regime['overall_coverage']:.1%} is below {minimum_coverage:.0%}; "
            "last-good snapshot preserved"
        )

    root = Path(data_dir)
    raw_dir = root / "macro" / "raw"
    snapshot_path = root / "macro" / "market_regime_snapshot.json"
    fred_hash = _sha256(payloads["fred"])
    vix_hash = _sha256(payloads["cboe_vix"])
    snapshot = {
        "schema_version": 1,
        "collector_version": MACRO_COLLECTOR_VERSION,
        "collected_at": collected.isoformat(),
        "availability_status": "current_snapshot_proxy",
        "point_in_time_history": False,
        "operational_eligible": False,
        "observations": observations,
        "regime": regime,
        "sources": [
            {"name": "FRED", **http_results["fred"], "series_ids": list(FRED_SERIES), "sha256": fred_hash, "raw_path": f"raw/fred_macro.{fred_hash}.csv", "terms_url": FRED_TERMS_URL},
            {"name": "Cboe VIX", **http_results["cboe_vix"], "sha256": vix_hash, "raw_path": f"raw/cboe_vix.{vix_hash}.csv", "source_page": CBOE_VIX_SOURCE_PAGE},
        ],
    }
    # Hash-addressed raw files keep an earlier snapshot's evidence intact even
    # if a later multi-file publish is interrupted before the snapshot swap.
    atomic_write_bytes(raw_dir / f"fred_macro.{fred_hash}.csv", payloads["fred"])
    atomic_write_bytes(raw_dir / f"cboe_vix.{vix_hash}.csv", payloads["cboe_vix"])
    atomic_write_json(snapshot_path, snapshot)
    return OfficialMacroCollectionResult(
        status=regime["status"],
        snapshot_path=str(snapshot_path),
        observation_count=len(observations),
        overall_coverage=float(regime["overall_coverage"]),
        collected_at=collected.isoformat(),
        source_hashes={"fred": fred_hash, "cboe_vix": vix_hash},
        requests_made=2,
        http_results=http_results,
    )


def write_failed_attempt(data_dir: str | Path, exc: BaseException, *, now: datetime | None = None) -> None:
    """Record only a safe failure category; never store response bodies or URLs with secrets."""

    collected = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    details = exc.details if isinstance(exc, OfficialMacroSnapshotError) else {}
    atomic_write_json(
        Path(data_dir) / "macro" / "last_attempt.json",
        {
            "status": "blocked",
            "collected_at": collected.isoformat(),
            "diagnostic_code": type(exc).__name__,
            "message": str(exc),
            "last_good_preserved": True,
            **details,
        },
    )
