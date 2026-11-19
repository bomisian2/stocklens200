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
import re
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import requests

from .market_regime import (
    REGIME_MINIMUM_COVERAGE,
    build_market_regime,
    build_qqq_proxy_observations,
)
from .storage import atomic_write_bytes, atomic_write_json


MACRO_COLLECTOR_VERSION = "official-macro-snapshot-v3"
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
FRED_GRAPH_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_MONTHLY_SERIES = {"CPIAUCSL", "PCEPI", "UNRATE", "PAYEMS"}
FRED_DAILY_SERIES = set(FRED_SERIES) - FRED_MONTHLY_SERIES
CBOE_VIX_DOWNLOAD_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)
FRED_TERMS_URL = "https://fred.stlouisfed.org/docs/api/terms_of_use.html"
CBOE_VIX_SOURCE_PAGE = "https://www.cboe.com/tradable-products/vix/vix-historical-data"
USER_AGENT = "StockLens200-personal-research/1.0 (local-only; no redistribution)"
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 90
STREAM_CHUNK_BYTES = 64 * 1024


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
    attempt_id: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_final_url(name: str, final_url: str) -> None:
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise OfficialMacroSnapshotError(
            f"{name} returned an unsafe final URL"
        )
    host = (parsed.hostname or "").lower()
    allowed = {
        "fred": {"fred.stlouisfed.org"},
        "cboe_vix": {"cdn.cboe.com", "cdn-api.cboe.com"},
    }[name]
    if host not in allowed:
        raise OfficialMacroSnapshotError(
            f"{name} redirected outside the approved official domains: {host or 'missing-host'}"
        )
    sensitive_names = {"apikey", "api_key", "token", "access_token", "secret", "key"}
    if any(key.lower() in sensitive_names for key, _ in parse_qsl(parsed.query)):
        raise OfficialMacroSnapshotError(f"{name} returned a URL containing a secret-like query")


def _attempt_id(collected: datetime) -> str:
    value = collected.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not re.fullmatch(r"[0-9TZ]+", value):
        raise OfficialMacroSnapshotError("Could not create a safe attempt identifier")
    return value


def _shift_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(index, 12)
    return date(year, month_index + 1, 1)


def fred_series_window(series: str, analysis_as_of: date) -> tuple[date, date]:
    """Return the deterministic minimum history window required by v3."""

    if series not in FRED_SERIES:
        raise OfficialMacroSnapshotError(f"Unsupported FRED series: {series}")
    if series in FRED_MONTHLY_SERIES:
        start = _shift_months(analysis_as_of, -24)
    else:
        # 180 calendar days provides margin for at least 120 daily/trading observations.
        start = date.fromordinal(analysis_as_of.toordinal() - 180)
    return start, analysis_as_of


def fred_series_url(series: str, analysis_as_of: date) -> str:
    start, end = fred_series_window(series, analysis_as_of)
    query = urlencode(
        {
            "cosd": start.isoformat(),
            "coed": end.isoformat(),
            "id": series,
        }
    )
    return f"{FRED_GRAPH_BASE_URL}?{query}"


def _attempt_manifest_path(root: Path, attempt_id: str) -> Path:
    return root / "macro" / "attempts" / attempt_id / "attempt.json"


def _write_attempt_manifest(
    root: Path,
    attempt_id: str,
    *,
    collected_at: str,
    status: str,
    requests_made: int,
    http_results: dict[str, dict[str, Any]],
    diagnostic_code: str | None = None,
    message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "collector_version": MACRO_COLLECTOR_VERSION,
        "attempt_id": attempt_id,
        "collected_at": collected_at,
        "status": status,
        "requests_made": requests_made,
        "responses": http_results,
        "last_good_preserved": status != "promoted",
    }
    if diagnostic_code:
        manifest["diagnostic_code"] = diagnostic_code
    if message:
        manifest["message"] = message
    if extra:
        manifest.update(extra)
    atomic_write_json(_attempt_manifest_path(root, attempt_id), manifest)


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
        raise OfficialMacroSnapshotError(
            "FRED response has unsupported encoding or invalid CSV bytes"
        ) from exc
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


def parse_fred_series_csv(payload: bytes, series: str) -> list[tuple[date, float]]:
    """Parse one official fredgraph.csv series without filling missing values."""

    if series not in FRED_SERIES:
        raise OfficialMacroSnapshotError(f"Unsupported FRED series: {series}")
    try:
        encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = payload.decode(encoding)
    except UnicodeDecodeError as exc:
        raise OfficialMacroSnapshotError(
            f"FRED {series} response has unsupported encoding or invalid CSV bytes"
        ) from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise OfficialMacroSnapshotError(f"FRED {series} CSV has no header")
    date_field = next(
        (field for field in ("observation_date", "DATE", "date") if field in reader.fieldnames),
        None,
    )
    if date_field is None or series not in reader.fieldnames:
        raise OfficialMacroSnapshotError(
            f"FRED {series} CSV is missing observation_date or series column"
        )
    values: list[tuple[date, float]] = []
    seen: set[date] = set()
    for row in reader:
        observed_on = _parse_iso_or_us_date(str(row.get(date_field, "")))
        value = _finite_float(str(row.get(series, "")))
        if value is None:
            continue
        if observed_on in seen:
            raise OfficialMacroSnapshotError(
                f"Duplicate FRED observation: {series} {observed_on.isoformat()}"
            )
        seen.add(observed_on)
        values.append((observed_on, value))
    values.sort(key=lambda item: item[0])
    if not values:
        raise OfficialMacroSnapshotError(f"FRED series has no observations: {series}")
    return values


def parse_cboe_vix_csv(payload: bytes) -> tuple[date, float]:
    """Return the latest official daily VIX close."""

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OfficialMacroSnapshotError(
            "Cboe VIX response has unsupported encoding or invalid CSV bytes"
        ) from exc
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


def normalize_partial_macro_series(
    series: dict[str, list[tuple[date, float]]],
    source_hashes: dict[str, str],
    *,
    collected_at: str,
    vix_observation: tuple[date, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Normalize only available series and retain per-input failures as missing."""

    collected = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
    if collected.tzinfo is None:
        raise OfficialMacroSnapshotError("collected_at must include a timezone")
    available_at = collected.astimezone(timezone.utc).isoformat()
    observations: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    fred_source = "FRED official graph CSV"

    def add_latest(series_id: str, metric_id: str, unit: str, basis: str) -> None:
        values = series.get(series_id)
        if not values:
            errors[series_id] = "missing_or_blocked"
            return
        observed_on, value = values[-1]
        observations.append(
            _observation(
                metric_id,
                value,
                unit,
                f"{fred_source}:{series_id}",
                source_hashes[series_id],
                observed_on,
                available_at,
                "daily",
                basis,
            )
        )

    add_latest("DFF", "policy_rate_pct", "percent", "latest DFF observation")
    add_latest("DGS2", "treasury_2y_yield_pct", "percent", "latest DGS2 observation")
    add_latest("DGS10", "treasury_10y_yield_pct", "percent", "latest DGS10 observation")

    if series.get("DGS2") and series.get("DGS10"):
        try:
            spread_date, common_2y, common_10y = _latest_common(series["DGS2"], series["DGS10"])
            observations.append(
                _observation(
                    "treasury_2s10s_spread_pp",
                    common_10y - common_2y,
                    "percentage_point",
                    f"{fred_source}:DGS10-DGS2",
                    _sha256(
                        f"{source_hashes['DGS10']}:{source_hashes['DGS2']}".encode("ascii")
                    ),
                    spread_date,
                    available_at,
                    "daily",
                    "same-date DGS10 minus DGS2",
                )
            )
        except OfficialMacroSnapshotError as exc:
            errors["DGS2_DGS10_SPREAD"] = str(exc)
    else:
        errors["DGS2_DGS10_SPREAD"] = "missing_or_blocked_component"

    for series_id, metric_id, ratio, unit, basis in (
        ("UNRATE", "unemployment_rate_change_3m_pp", False, "percentage_point", "latest minus three calendar months earlier"),
        ("PAYEMS", "employment_growth_3m_ratio", True, "ratio", "latest / three calendar months earlier - 1"),
    ):
        values = series.get(series_id)
        if not values:
            errors[series_id] = "missing_or_blocked"
            continue
        try:
            observed_on, value = _monthly_change_3m(values, ratio=ratio)
            observations.append(
                _observation(metric_id, value, unit, f"{fred_source}:{series_id}", source_hashes[series_id], observed_on, available_at, "monthly", basis)
            )
        except OfficialMacroSnapshotError as exc:
            errors[series_id] = str(exc)

    for series_id, metric_id in (
        ("CPIAUCSL", "cpi_yoy_trend_3m_pp"),
        ("PCEPI", "pce_yoy_trend_3m_pp"),
    ):
        values = series.get(series_id)
        if not values:
            errors[series_id] = "missing_or_blocked"
            continue
        try:
            observed_on, value = _yoy_trend_3m(values)
            observations.append(
                _observation(metric_id, value, "percentage_point", f"{fred_source}:{series_id}", source_hashes[series_id], observed_on, available_at, "monthly", "latest YoY percent minus YoY percent three months earlier")
            )
        except OfficialMacroSnapshotError as exc:
            errors[series_id] = str(exc)

    dollar_values = series.get("DTWEXBGS")
    if dollar_values:
        if len(dollar_values) >= 64 and dollar_values[-64][1] != 0:
            observed_on, latest = dollar_values[-1]
            observations.append(
                _observation("broad_dollar_return_63d_ratio", latest / dollar_values[-64][1] - 1, "ratio", f"{fred_source}:DTWEXBGS", source_hashes["DTWEXBGS"], observed_on, available_at, "daily", "latest / 63 observations earlier - 1; not DXY")
            )
        else:
            errors["DTWEXBGS"] = "requires_64_nonzero_observations"
    else:
        errors["DTWEXBGS"] = "missing_or_blocked"

    add_latest("BAMLH0A0HYM2", "hy_oas_pct", "percent", "latest ICE BofA US High Yield OAS")
    if vix_observation is not None and "cboe_vix" in source_hashes:
        vix_date, vix_close = vix_observation
        observations.append(
            _observation("vix_index", vix_close, "index_point", "Cboe official VIX historical daily CSV", source_hashes["cboe_vix"], vix_date, available_at, "daily", "latest official VIX daily close")
        )
    else:
        errors["cboe_vix"] = "missing_or_blocked"
    return observations, errors


def _stream_official_response(
    client: requests.Session,
    *,
    source_id: str,
    url: str,
    attempt_dir: Path,
    collected_at: str,
    headers: dict[str, str],
) -> tuple[bytes | None, dict[str, Any]]:
    """Perform one non-retrying request and preserve complete or partial bytes."""

    base_result: dict[str, Any] = {
        "source_id": source_id,
        "requested_url": url,
        "collected_at": collected_at,
        "status": "blocked",
    }
    try:
        response = client.get(
            url,
            headers=headers,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            stream=True,
        )
    except requests.RequestException as exc:
        base_result.update(
            {
                "diagnostic_code": type(exc).__name__,
                "message": f"request failed before response headers ({type(exc).__name__})",
                "incomplete": True,
                "actual_bytes": 0,
            }
        )
        return None, base_result

    final_url = str(response.url)
    try:
        _validate_final_url("cboe_vix" if source_id == "cboe_vix" else "fred", final_url)
    except OfficialMacroSnapshotError as exc:
        base_result.update(
            {
                "diagnostic_code": type(exc).__name__,
                "message": str(exc),
                "incomplete": True,
                "actual_bytes": 0,
            }
        )
        return None, base_result

    result = {
        **base_result,
        "final_url": final_url,
        "http_status": int(response.status_code),
        "content_type": str(response.headers.get("Content-Type") or ""),
        "content_encoding": str(response.headers.get("Content-Encoding") or ""),
        "declared_content_length": str(response.headers.get("Content-Length") or ""),
    }
    attempt_dir.mkdir(parents=True, exist_ok=True)
    partial_path = attempt_dir / f"{source_id}.streaming.partial"
    digest = hashlib.sha256()
    actual_bytes = 0
    try:
        with partial_path.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=STREAM_CHUNK_BYTES):
                if not chunk:
                    continue
                stream.write(chunk)
                stream.flush()
                digest.update(chunk)
                actual_bytes += len(chunk)
            os.fsync(stream.fileno())
    except requests.RequestException as exc:
        partial_hash = digest.hexdigest()
        preserved = attempt_dir / f"{source_id}.partial.{partial_hash}.bin"
        os.replace(partial_path, preserved)
        result.update(
            {
                "status": "blocked",
                "diagnostic_code": type(exc).__name__,
                "message": f"stream interrupted ({type(exc).__name__})",
                "incomplete": True,
                "actual_bytes": actual_bytes,
                "sha256": partial_hash,
                "attempt_raw_path": preserved.name,
            }
        )
        return None, result

    payload_hash = digest.hexdigest()
    preserved = attempt_dir / f"{source_id}.{payload_hash}.bin"
    os.replace(partial_path, preserved)
    result.update(
        {
            "actual_bytes": actual_bytes,
            "sha256": payload_hash,
            "attempt_raw_path": preserved.name,
            "incomplete": False,
        }
    )
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        result.update(
            {
                "status": "blocked",
                "diagnostic_code": type(exc).__name__,
                "message": f"HTTP response rejected ({type(exc).__name__})",
            }
        )
        return None, result
    result["status"] = "received"
    return preserved.read_bytes(), result


def collect_official_macro_snapshot(
    data_dir: str | Path,
    *,
    session: requests.Session | None = None,
    now: datetime | None = None,
    qqq_csv_path: str | Path | None = None,
    qqq_manifest_path: str | Path | None = None,
    minimum_coverage: float = REGIME_MINIMUM_COVERAGE,
    minimum_axis_coverage: float = REGIME_MINIMUM_COVERAGE,
    canary_only: bool = False,
    fail_fast_canary: bool = True,
) -> OfficialMacroCollectionResult:
    """Collect split official series with a DFF canary and partial-success gate."""

    client = session or requests.Session()
    collected = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root = Path(data_dir)
    attempt_id = _attempt_id(collected)
    attempt_dir = root / "macro" / "attempts" / attempt_id
    analysis_date = collected.date()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,*/*;q=0.5",
        "Accept-Encoding": "identity",
    }
    plan = [
        {
            "source_id": f"fred_{series}",
            "series": series,
            "url": fred_series_url(series, analysis_date),
            "window": [
                item.isoformat() for item in fred_series_window(series, analysis_date)
            ],
        }
        for series in FRED_SERIES
    ]
    http_results: dict[str, dict[str, Any]] = {}
    valid_payloads: dict[str, bytes] = {}
    parsed_series: dict[str, list[tuple[date, float]]] = {}
    source_hashes: dict[str, str] = {}
    normalization_errors: dict[str, str] = {}
    requests_made = 0
    plan_metadata = {
        "request_plan": [
            {
                "source_id": item["source_id"],
                "series": item["series"],
                "window": item["window"],
            }
            for item in plan
        ],
        "canary": "fred_DFF",
        "canary_only": canary_only,
        "fail_fast_canary": fail_fast_canary,
        "automatic_retry": False,
        "max_concurrency": 1,
        "timeout_seconds": {
            "connect": CONNECT_TIMEOUT_SECONDS,
            "read": READ_TIMEOUT_SECONDS,
        },
    }
    _write_attempt_manifest(
        root,
        attempt_id,
        collected_at=collected.isoformat(),
        status="collecting",
        requests_made=0,
        http_results=http_results,
        extra=plan_metadata,
    )

    for index, item in enumerate(plan):
        source_id = str(item["source_id"])
        series_id = str(item["series"])
        url = str(item["url"])
        _validate_final_url("fred", url)
        requests_made += 1
        payload, result = _stream_official_response(
            client,
            source_id=source_id,
            url=url,
            attempt_dir=attempt_dir,
            collected_at=collected.isoformat(),
            headers=headers,
        )
        http_results[source_id] = result
        if payload is not None:
            try:
                parsed_series[series_id] = parse_fred_series_csv(payload, series_id)
                valid_payloads[series_id] = payload
                source_hashes[series_id] = str(result["sha256"])
                result["status"] = "validated"
            except OfficialMacroSnapshotError as exc:
                result.update(
                    {
                        "status": "blocked_parse",
                        "diagnostic_code": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                normalization_errors[series_id] = str(exc)
        else:
            normalization_errors[series_id] = str(
                result.get("diagnostic_code") or "transport_blocked"
            )
        _write_attempt_manifest(
            root,
            attempt_id,
            collected_at=collected.isoformat(),
            status="collecting",
            requests_made=requests_made,
            http_results=http_results,
            extra={**plan_metadata, "normalization_errors": normalization_errors},
        )

        if index == 0 and series_id not in parsed_series and fail_fast_canary:
            error = OfficialMacroSnapshotError(
                "DFF canary failed; remaining official requests were not attempted; "
                "last-good snapshot preserved",
                details={
                    "attempt_id": attempt_id,
                    "requests_made": requests_made,
                    "http_results": http_results,
                    "normalization_errors": normalization_errors,
                },
            )
            _write_attempt_manifest(
                root,
                attempt_id,
                collected_at=collected.isoformat(),
                status="blocked",
                requests_made=requests_made,
                http_results=http_results,
                diagnostic_code=type(error).__name__,
                message=str(error),
                extra={**plan_metadata, "normalization_errors": normalization_errors},
            )
            raise error
        if index == 0 and canary_only:
            _write_attempt_manifest(
                root,
                attempt_id,
                collected_at=collected.isoformat(),
                status="canary_verified",
                requests_made=requests_made,
                http_results=http_results,
                extra={**plan_metadata, "normalization_errors": normalization_errors},
            )
            return OfficialMacroCollectionResult(
                status="canary_verified",
                snapshot_path="",
                observation_count=0,
                overall_coverage=0.0,
                collected_at=collected.isoformat(),
                source_hashes=source_hashes,
                requests_made=requests_made,
                http_results=http_results,
                attempt_id=attempt_id,
            )

    requests_made += 1
    vix_payload, vix_result = _stream_official_response(
        client,
        source_id="cboe_vix",
        url=CBOE_VIX_DOWNLOAD_URL,
        attempt_dir=attempt_dir,
        collected_at=collected.isoformat(),
        headers=headers,
    )
    http_results["cboe_vix"] = vix_result
    vix_observation: tuple[date, float] | None = None
    if vix_payload is not None:
        try:
            vix_observation = parse_cboe_vix_csv(vix_payload)
            valid_payloads["cboe_vix"] = vix_payload
            source_hashes["cboe_vix"] = str(vix_result["sha256"])
            vix_result["status"] = "validated"
        except OfficialMacroSnapshotError as exc:
            vix_result.update(
                {
                    "status": "blocked_parse",
                    "diagnostic_code": type(exc).__name__,
                    "message": str(exc),
                }
            )
            normalization_errors["cboe_vix"] = str(exc)
    else:
        normalization_errors["cboe_vix"] = str(
            vix_result.get("diagnostic_code") or "transport_blocked"
        )

    observations, derived_errors = normalize_partial_macro_series(
        parsed_series,
        source_hashes,
        collected_at=collected.isoformat(),
        vix_observation=vix_observation,
    )
    for source_id, reason in derived_errors.items():
        normalization_errors.setdefault(source_id, reason)
    if qqq_csv_path and qqq_manifest_path:
        try:
            observations.extend(
                build_qqq_proxy_observations(qqq_csv_path, qqq_manifest_path)
            )
        except (OSError, ValueError) as exc:
            normalization_errors["qqq_proxy"] = f"{type(exc).__name__}: {exc}"

    regime = build_market_regime(
        observations,
        analysis_as_of=collected.isoformat(),
    )
    axis_coverages = {
        axis: float(regime[f"{axis}_regime"]["coverage"])
        for axis in ("economy", "inflation", "risk")
    }
    coverage_eligible = (
        float(regime["overall_coverage"]) >= minimum_coverage
        and all(value >= minimum_axis_coverage for value in axis_coverages.values())
    )
    if not coverage_eligible:
        error = OfficialMacroSnapshotError(
            "overall or axis coverage is below the activation gate; "
            "last-good snapshot preserved",
            details={
                "attempt_id": attempt_id,
                "requests_made": requests_made,
                "http_results": http_results,
                "source_hashes": source_hashes,
                "normalization_errors": normalization_errors,
                "overall_coverage": regime["overall_coverage"],
                "axis_coverages": axis_coverages,
            },
        )
        _write_attempt_manifest(
            root,
            attempt_id,
            collected_at=collected.isoformat(),
            status="blocked",
            requests_made=requests_made,
            http_results=http_results,
            diagnostic_code=type(error).__name__,
            message=str(error),
            extra={
                **plan_metadata,
                "normalization_errors": normalization_errors,
                "overall_coverage": regime["overall_coverage"],
                "axis_coverages": axis_coverages,
            },
        )
        raise error

    raw_dir = root / "macro" / "raw"
    snapshot_path = root / "macro" / "market_regime_snapshot.json"
    promoted_paths: dict[str, str] = {}
    for source_id, payload in valid_payloads.items():
        payload_hash = source_hashes[source_id]
        filename = f"{source_id}.{payload_hash}.csv"
        atomic_write_bytes(raw_dir / filename, payload)
        promoted_paths[source_id] = f"raw/{filename}"
    partial_collection = any(
        result.get("status") != "validated" for result in http_results.values()
    )
    snapshot = {
        "schema_version": 1,
        "collector_version": MACRO_COLLECTOR_VERSION,
        "collected_at": collected.isoformat(),
        "availability_status": "current_snapshot_proxy",
        "point_in_time_history": False,
        "operational_eligible": False,
        "collection_status": "partial" if partial_collection else "complete",
        "normalization_errors": normalization_errors,
        "observations": observations,
        "regime": regime,
        "coverage_gate": {
            "minimum_overall": minimum_coverage,
            "minimum_each_axis": minimum_axis_coverage,
            "overall": regime["overall_coverage"],
            "axes": axis_coverages,
        },
        "sources": [
            {**result, "promoted_raw_path": promoted_paths.get(source_id)}
            for source_id, result in http_results.items()
        ],
        "terms": {"fred": FRED_TERMS_URL, "cboe_source_page": CBOE_VIX_SOURCE_PAGE},
    }
    atomic_write_json(snapshot_path, snapshot)
    _write_attempt_manifest(
        root,
        attempt_id,
        collected_at=collected.isoformat(),
        status="promoted",
        requests_made=requests_made,
        http_results=http_results,
        extra={
            **plan_metadata,
            "normalization_errors": normalization_errors,
            "overall_coverage": regime["overall_coverage"],
            "axis_coverages": axis_coverages,
            "collection_status": snapshot["collection_status"],
        },
    )
    return OfficialMacroCollectionResult(
        status=regime["status"],
        snapshot_path=str(snapshot_path),
        observation_count=len(observations),
        overall_coverage=float(regime["overall_coverage"]),
        collected_at=collected.isoformat(),
        source_hashes=source_hashes,
        requests_made=requests_made,
        http_results=http_results,
        attempt_id=attempt_id,
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
