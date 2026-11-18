from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


AUDIT_SCHEMA_VERSION = 1
TEXT_SUFFIXES = {".csv", ".js", ".json", ".md", ".py", ".toml", ".ts", ".tsx", ".yaml", ".yml"}
SKIP_DIRECTORIES = {".git", ".pytest_cache", ".venv", "__pycache__", "node_modules"}

FEATURE_PATTERNS = {
    "bandTable": (re.compile(r"\bbandTable\b", re.IGNORECASE),),
    "analystLog.oneTime": (
        re.compile(r"\banalystLog\b", re.IGNORECASE),
        re.compile(r"\boneTime\b", re.IGNORECASE),
    ),
    "computePriceValidity": (re.compile(r"\bcomputePriceValidity\b", re.IGNORECASE),),
    "distorted+PEG veto": (
        re.compile(r"\bdistort(?:ed|ion)?\b", re.IGNORECASE),
        re.compile(r"\bPEG\b", re.IGNORECASE),
    ),
    "confidence": (re.compile(r"\bconfidence\b", re.IGNORECASE),),
    "review queue signature": (
        re.compile(r"\bsignature\b", re.IGNORECASE),
        re.compile(r"\b(?:review|recheck|queue)\w*", re.IGNORECASE),
    ),
}


def _first(row: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def _aware_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _https(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("https://")


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _band_rows(payload: Any) -> list[tuple[str, list[Any]]]:
    rows: list[tuple[str, list[Any]]] = []
    for path, value in _walk(payload):
        if path.lower().endswith(".bandtable") and isinstance(value, list):
            rows.append((path, value))
    return rows


def _one_time_rows(payload: Any) -> list[tuple[str, list[Any]]]:
    rows: list[tuple[str, list[Any]]] = []
    for path, value in _walk(payload):
        if path.lower().endswith(".analystlog.onetime") and isinstance(value, list):
            rows.append((path, value))
    return rows


def _audit_band_observations(groups: list[tuple[str, list[Any]]]) -> dict[str, Any]:
    observations = [row for _, rows in groups for row in rows]
    valid_dates: list[datetime] = []
    valid_eps_vintages: list[datetime] = []
    complete_fingerprints: list[tuple[str, str, float, float]] = []
    complete = 0
    gaap_count = 0
    for row in observations:
        if not isinstance(row, dict):
            continue
        observed = _aware_timestamp(_first(row, ("observed_at_utc", "observedAt", "date")))
        eps_available = _aware_timestamp(
            _first(row, ("eps_available_at_utc", "epsAvailableAt", "epsPublishedAt"))
        )
        price = _first(row, ("price", "marketPrice"))
        eps = _first(row, ("public_ttm_eps", "ttmEps", "eps"))
        price_source = _first(row, ("price_source_url", "priceSourceUrl"))
        eps_source = _first(row, ("eps_source_url", "epsSourceUrl"))
        basis = str(_first(row, ("eps_basis", "epsBasis", "accountingBasis")) or "").upper()
        split_adjusted = _first(row, ("split_adjusted", "splitAdjusted"))
        numeric = all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (price, eps))
        point_in_time = observed is not None and eps_available is not None and eps_available <= observed
        if basis == "GAAP":
            gaap_count += 1
        if (
            numeric
            and point_in_time
            and _https(price_source)
            and _https(eps_source)
            and basis == "GAAP"
            and isinstance(split_adjusted, bool)
            and "distorted" in row
        ):
            complete += 1
            valid_dates.append(observed)
            valid_eps_vintages.append(eps_available)
            complete_fingerprints.append(
                (observed.isoformat(), eps_available.isoformat(), float(price), float(eps))
            )
    unique_dates = len({value.isoformat() for value in valid_dates})
    unique_eps_vintages = len({value.isoformat() for value in valid_eps_vintages})
    duplicate_rows = complete - len(set(complete_fingerprints))
    # Repeated prices within one unchanged EPS vintage are strongly correlated. Allow at most two
    # effective price observations per independently published EPS vintage.
    effective_observations = min(unique_dates, unique_eps_vintages * 2)
    span_years = 0.0
    if len(valid_dates) >= 2:
        span_years = (max(valid_dates) - min(valid_dates)).days / 365.25
    total = len(observations)
    blockers: list[str] = []
    if total == 0:
        blockers.append("no bandTable observations")
    if complete != total:
        blockers.append(
            "each row must include timezone-aware observation/EPS-publication times, numeric price/EPS, "
            "separate HTTPS sources, GAAP basis, split adjustment, and distortion status"
        )
    if effective_observations < 20:
        blockers.append(
            f"only {effective_observations} effective observations after date/EPS-vintage de-duplication; "
            "formal minimum is 20"
        )
    if span_years < 5.0:
        blockers.append(f"complete observation span is {span_years:.2f} years; formal minimum is 5.00")
    return {
        "json_paths": [path for path, _ in groups],
        "observation_count": total,
        "complete_point_in_time_count": complete,
        "unique_price_date_count": unique_dates,
        "unique_eps_vintage_count": unique_eps_vintages,
        "duplicate_row_count": duplicate_rows,
        "effective_observation_count": effective_observations,
        "gaap_labeled_count": gaap_count,
        "span_years": round(span_years, 4),
        "reference_eligible": complete > 0,
        "formal_valuation_eligible": (
            total > 0
            and complete == total
            and duplicate_rows == 0
            and effective_observations >= 20
            and span_years >= 5.0
        ),
        "blocking_reasons": blockers,
    }


def _audit_one_time_observations(groups: list[tuple[str, list[Any]]]) -> dict[str, Any]:
    observations = [row for _, rows in groups for row in rows]
    complete = 0
    for row in observations:
        if not isinstance(row, dict):
            continue
        amount = _first(row, ("amount_per_share", "amountPerShare", "afterTaxPerShare"))
        category = _first(row, ("category", "adjustmentCategory"))
        available = _aware_timestamp(_first(row, ("available_at_utc", "availableAt", "publishedAt")))
        source = _first(row, ("source_url", "sourceUrl"))
        recurring = _first(row, ("recurring", "isRecurring"))
        tax_basis = _first(row, ("tax_basis", "taxBasis", "afterTax"))
        if (
            isinstance(amount, (int, float))
            and not isinstance(amount, bool)
            and bool(category)
            and available is not None
            and _https(source)
            and isinstance(recurring, bool)
            and tax_basis not in (None, "")
        ):
            complete += 1
    total = len(observations)
    blockers: list[str] = []
    if total == 0:
        blockers.append("no analystLog.oneTime observations")
    if complete != total:
        blockers.append(
            "each adjustment needs a quantified per-share amount, category, publication time, HTTPS source, "
            "recurrence decision, and tax basis"
        )
    return {
        "json_paths": [path for path, _ in groups],
        "observation_count": total,
        "complete_adjustment_count": complete,
        "read_only_adapter_eligible": total > 0 and complete == total,
        "blocking_reasons": blockers,
    }


def audit_legacy_assets(source_root: str | Path) -> dict[str, Any]:
    """Audit a legacy export without modifying it or producing valuation evidence."""
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"legacy source root does not exist: {root}")

    scanned = 0
    matched_files: dict[str, list[str]] = {feature: [] for feature in FEATURE_PATTERNS}
    parse_errors: list[dict[str, str]] = []
    band_groups: list[tuple[str, list[Any]]] = []
    one_time_groups: list[tuple[str, list[Any]]] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        scanned += 1
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            parse_errors.append({"file": relative, "error": type(exc).__name__})
            continue
        for feature, patterns in FEATURE_PATTERNS.items():
            if all(pattern.search(text) for pattern in patterns):
                matched_files[feature].append(relative)
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                parse_errors.append({"file": relative, "error": f"JSONDecodeError:{exc.lineno}"})
                continue
            band_groups.extend((f"{relative}:{json_path}", rows) for json_path, rows in _band_rows(payload))
            one_time_groups.extend(
                (f"{relative}:{json_path}", rows) for json_path, rows in _one_time_rows(payload)
            )

    band = _audit_band_observations(band_groups)
    one_time = _audit_one_time_observations(one_time_groups)
    features: dict[str, Any] = {}
    for feature, files in matched_files.items():
        features[feature] = {"present_in_text": bool(files), "files": sorted(files)}
    features["bandTable"].update(band)
    features["analystLog.oneTime"].update(one_time)

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "source_root": str(root),
        "read_only": True,
        "scanned_text_file_count": scanned,
        "parse_errors": parse_errors,
        "features": features,
        "conversion_performed": False,
        "formal_valuation_input_available": bool(band["formal_valuation_eligible"]),
    }
