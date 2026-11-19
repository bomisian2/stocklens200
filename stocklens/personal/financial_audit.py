from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import PILOT_IR_DOCUMENTS
from .storage import atomic_write_json, load_json


METRIC_KEYS = (
    "revenue",
    "operating_income",
    "net_income",
    "diluted_eps",
    "diluted_shares",
    "cfo",
    "capex",
    "fcf",
)


# Independent transcription contracts from the issuer releases.  These values
# are intentionally separate from the extraction code so a parser regression
# cannot silently redefine the expected result.
OFFICIAL_FACT_CONTRACTS: dict[str, dict[str, Any]] = {
    "AMAT": {
        "periods": {
            "FY2025": [28368, 8289, 6998, 8.66, 808, 7958, 2260, 5698],
            "Q3 FY2026": [9115, 3075, 2538, 3.17, 800, 3037, 707, 2330],
            "9M FY2026": [24037, 7429, 7370, 9.22, 799, 5568, 1988, 3580],
        },
        "prior_ytd": [21568, 6577, 5101, None, None, 5130, 1475, 3655],
        "balance_sheet": {
            "cash_and_equivalents": 7037,
            "short_term_investments": 2196,
            "long_term_investments": 5268,
            "short_term_debt": 1299,
            "long_term_debt": 5245,
        },
        "guidance": {
            "revenue_midpoint": 10250,
            "revenue_plus_minus": 500,
            "non_gaap_eps_midpoint": 4.02,
            "non_gaap_eps_plus_minus": 0.20,
        },
        "ttm_period_end": "2026-07-26",
    },
    "AVGO": {
        "periods": {
            "FY2025": [63887, 25484, 23126, 4.77, 4853, 27537, 623, 26914],
            "Q3 FY2026": [29591, 15955, 13088, 2.68, 4887, 14197, 532, 13665],
            "9M FY2026": [71089, 35306, 29747, 6.09, 4884, 32950, 1013, 31937],
        },
        "prior_ytd": [45872, 17976, 14608, None, None, 19834, 386, 19448],
        "balance_sheet": {
            "cash_and_equivalents": 23975,
            "short_term_debt": 2252,
            "long_term_debt": 57167,
        },
        "guidance": {
            "revenue_approx": 34800,
            "non_gaap_operating_margin_percent": 66,
        },
        "ttm_period_end": "2026-08-02",
    },
    "QCOM": {
        "periods": {
            "FY2025": [44284, 12355, 5541, 5.01, 1105, 14012, 1192, 12820],
            "Q3 FY2026": [9947, 1626, 2002, 1.87, 1069, None, None, None],
            "9M FY2026": [32798, 7302, 12377, 11.53, 1073, 8405, 1578, 6827],
        },
        "prior_ytd": [33013, 9437, 8658, None, None, 10016, 785, 9231],
        "balance_sheet": {
            "cash_and_equivalents": 4533,
            "marketable_securities": 3771,
            "short_term_debt": 2489,
            "long_term_debt": 12781,
        },
        "guidance": {
            "revenue_low": 9700,
            "revenue_high": 10500,
            "gaap_eps_low": 1.22,
            "gaap_eps_high": 1.42,
        },
        "ttm_period_end": "2026-06-28",
    },
}


def _metric_map(values: list[float | None]) -> dict[str, float | None]:
    return dict(zip(METRIC_KEYS, values, strict=True))


def _compare(checks: list[dict[str, Any]], path: str, expected: Any, actual: Any) -> None:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        passed = abs(float(expected) - float(actual)) <= 1e-9
    else:
        passed = expected == actual
    checks.append({"path": path, "expected": expected, "actual": actual, "passed": passed})


def _ttm_from_contract(contract: dict[str, Any]) -> dict[str, float | None]:
    annual = _metric_map(contract["periods"]["FY2025"])
    current = _metric_map(contract["periods"]["9M FY2026"])
    prior = _metric_map(contract["prior_ytd"])
    values: dict[str, float | None] = {}
    for key in ("revenue", "operating_income", "net_income", "cfo", "capex", "fcf"):
        inputs = annual[key], current[key], prior[key]
        values[key] = None if any(value is None for value in inputs) else inputs[0] + inputs[1] - inputs[2]
    values["diluted_eps"] = None
    values["diluted_shares"] = None
    return values


def audit_normalized_snapshot(
    snapshot: dict[str, Any],
    payloads: dict[str, bytes],
    *,
    verified_at: datetime | None = None,
) -> dict[str, Any]:
    ticker = str(snapshot.get("ticker", "")).upper()
    contract = OFFICIAL_FACT_CONTRACTS[ticker]
    checks: list[dict[str, Any]] = []

    periods = {period["label"]: period for period in snapshot.get("periods", [])}
    for label, values in contract["periods"].items():
        period = periods.get(label, {})
        _compare(checks, f"periods.{label}.unit", "USD millions except per-share data", period.get("unit"))
        for key, expected in _metric_map(values).items():
            _compare(checks, f"periods.{label}.{key}", expected, period.get("metrics", {}).get(key))

    ttm = periods.get("TTM through Q3 FY2026", {})
    _compare(checks, "periods.TTM.period_end", contract["ttm_period_end"], ttm.get("period_end"))
    for key, expected in _ttm_from_contract(contract).items():
        _compare(checks, f"periods.TTM.{key}", expected, ttm.get("metrics", {}).get(key))

    for key, expected in contract["balance_sheet"].items():
        _compare(checks, f"balance_sheet.{key}", expected, snapshot.get("balance_sheet", {}).get(key))
    for key, expected in contract["guidance"].items():
        _compare(checks, f"guidance.{key}", expected, snapshot.get("guidance", {}).get(key))

    catalog = {document.key: document for document in PILOT_IR_DOCUMENTS[ticker]}
    manifests = {document["key"]: document for document in snapshot.get("documents", [])}
    source_rows: list[dict[str, Any]] = []
    for key, document in catalog.items():
        raw = payloads[key]
        manifest = manifests.get(key, {})
        digest = hashlib.sha256(raw).hexdigest()
        _compare(checks, f"documents.{key}.sha256", digest, manifest.get("sha256"))
        _compare(checks, f"documents.{key}.bytes", len(raw), manifest.get("bytes"))
        _compare(checks, f"documents.{key}.url", document.url, manifest.get("url"))
        _compare(checks, f"documents.{key}.published_at", document.published_at, manifest.get("published_at"))
        _compare(checks, f"documents.{key}.period_end", document.period_end, manifest.get("period_end"))
        published = datetime.fromisoformat(document.published_at)
        source_rows.append(
            {
                "key": key,
                "url": document.url,
                "period_end": document.period_end,
                "published_at": document.published_at,
                "available_after_period_end": published.date().isoformat() > document.period_end,
                "sha256": digest,
                "bytes": len(raw),
            }
        )

    failed = [check for check in checks if not check["passed"]]
    return {
        "schema_version": 1,
        "ticker": ticker,
        "status": "verified" if not failed else "failed",
        "verified_at_utc": (verified_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "method": "independent_official_fact_contract_plus_raw_hash",
        "checks_total": len(checks),
        "checks_passed": len(checks) - len(failed),
        "mismatches": failed,
        "changes": [],
        "sources": source_rows,
        "ttm_formula": "FY2025 + 9M FY2026 - 9M FY2025",
        "unit_policy": "USD millions except per-share data",
        "gaap_policy": "Reported GAAP facts are separate from issuer non-GAAP guidance",
    }


def audit_saved_personal_data(data_dir: str | Path) -> dict[str, Any]:
    root = Path(data_dir)
    results: list[dict[str, Any]] = []
    for ticker, documents in PILOT_IR_DOCUMENTS.items():
        snapshot = load_json(root / "normalized" / "financials" / f"{ticker}.json")
        if snapshot is None:
            results.append({"ticker": ticker, "status": "blocked", "reason": "normalized snapshot missing"})
            continue
        payloads: dict[str, bytes] = {}
        for document in documents:
            extension = "pdf" if document.media_type == "application/pdf" else "html"
            payloads[document.key] = (root / "raw" / f"{ticker}_{document.key}.{extension}").read_bytes()
        audit = audit_normalized_snapshot(snapshot, payloads)
        atomic_write_json(root / "audit" / "financials" / f"{ticker}.json", audit)
        results.append(
            {
                "ticker": ticker,
                "status": audit["status"],
                "checks_total": audit["checks_total"],
                "checks_passed": audit["checks_passed"],
                "mismatches": len(audit["mismatches"]),
            }
        )
    summary = {
        "schema_version": 1,
        "status": "verified" if results and all(row["status"] == "verified" for row in results) else "failed",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    atomic_write_json(root / "audit" / "financials" / "summary.json", summary)
    return summary
