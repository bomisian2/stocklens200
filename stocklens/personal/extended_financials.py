from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, load_json


PARSER_VERSION = "issuer-reviewed-field-map-v1"


SOURCES: dict[str, tuple[dict[str, Any], ...]] = {
    "GOOGL": (
        {
            "key": "fy2025",
            "filename": "GOOGL_fy2025.pdf",
            "url": "https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2025q4-alphabet-earnings-release.pdf",
            "published_at": "2026-02-04T16:00:00-05:00",
            "period_end": "2025-12-31",
            "distribution": "Alphabet official IR CDN",
            "sha256": "8bb2778772fc60c0290cfeac3b24d345a07b8918a7708ec8fcef7239243922f9",
        },
        {
            "key": "q2fy2026",
            "filename": "GOOGL_q2fy2026.pdf",
            "url": "https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf",
            "published_at": "2026-07-22T16:00:00-04:00",
            "period_end": "2026-06-30",
            "distribution": "Alphabet official IR CDN",
            "sha256": "65f35a2e9c287112121f736321c7526d603ba8e0dae27acac56c7d7357602aa8",
        },
    ),
    "MSFT": (
        {
            "key": "fy2026",
            "filename": "MSFT_fy2026.html",
            "url": "https://www.microsoft.com/en-us/investor/earnings/fy-2026-q4/press-release-webcast",
            "published_at": "2026-07-29T16:05:00-04:00",
            "period_end": "2026-06-30",
            "distribution": "Microsoft official investor relations",
            "sha256": "c38029afcfa10f73625a8f07e7394ed630de1b9eb9aaafad8919eded6075d8ef",
        },
    ),
    "LRCX": (
        {
            "key": "fy2026",
            "filename": "LRCX_fy2026.html",
            "url": "https://investor.lamresearch.com/2026-07-29-Lam-Research-Corporation-Reports-Financial-Results-for-the-Quarter-Ended-June-28,-2026",
            "published_at": "2026-07-29T16:05:00-04:00",
            "period_end": "2026-06-28",
            "distribution": "Lam Research official investor relations",
            "sha256": "541eda65547e5da6199bbaf1177f0c37a4856031f9218ce76ccda373f740f1f7",
        },
    ),
    "AMD": (
        {
            "key": "fy2025",
            "filename": "AMD_fy2025.html",
            "url": "https://ir.amd.com/news-events/press-releases/detail/1276/amd-reports-fourth-quarter-and-full-year-2025-financial-results",
            "published_at": "2026-02-03T16:15:00-05:00",
            "period_end": "2025-12-27",
            "distribution": "AMD official investor relations",
            "sha256": "bddde5d15780afb6b576642a7ca25964fa161bd987517c27d04b7f8866f98803",
        },
        {
            "key": "q2fy2026",
            "filename": "AMD_q2fy2026.html",
            "url": "https://ir.amd.com/news-events/press-releases/detail/1295/amd-reports-second-quarter-2026-financial-results",
            "published_at": "2026-08-04T16:15:00-04:00",
            "period_end": "2026-06-27",
            "distribution": "AMD official investor relations",
            "sha256": "8ab4a52ae0b36ed93dd47a830610363a1f607adc6e99dd93d11cafaa8a489446",
        },
    ),
    "NVDA": (
        {
            "key": "q2fy2027_10q",
            "filename": "NVDA_q2fy2027.pdf",
            "url": "https://investor.nvidia.com/files/doc_financials/2027/NVDA-2027-Q2-10Q-Final-including-exhibits.pdf",
            "published_at": "2026-08-26T16:20:00-04:00",
            "period_end": "2026-07-26",
            "distribution": "NVIDIA official investor relations",
            "sha256": "6b5a8dc1ed21e8b2b960fd0de97103af6a2f32ccc8dd91b363f06ea192ecfebd",
        },
    ),
}


def _period(
    label: str,
    kind: str,
    period_end: str,
    metrics: dict[str, float | None],
    *,
    basis: str = "reported_gaap",
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "kind": kind,
        "period_end": period_end,
        "unit": "USD millions except per-share data",
        "accounting_basis": basis,
        "metrics": metrics,
        "note": note,
    }


def _with_fcf(values: dict[str, float | None]) -> dict[str, float | None]:
    result = dict(values)
    cfo, capex = result.get("cfo"), result.get("capex")
    result["fcf"] = None if cfo is None or capex is None else cfo - capex
    return result


def _build_googl() -> dict[str, Any]:
    fy = _with_fcf({"revenue": 402836, "operating_income": 129039, "net_income": 132170, "diluted_eps": 10.81, "diluted_shares": 12230, "cfo": 164713, "capex": 91447})
    quarter = _with_fcf({"revenue": 119796, "operating_income": 40770, "net_income": 112193, "diluted_eps": 9.11, "diluted_shares": 12309, "cfo": 39069, "capex": 44924})
    ytd = _with_fcf({"revenue": 229692, "operating_income": 80466, "net_income": 174771, "diluted_eps": 14.24, "diluted_shares": 12274, "cfo": 84859, "capex": 80598})
    prior = _with_fcf({"revenue": 186662, "operating_income": 61877, "net_income": 62736, "diluted_eps": 5.12, "diluted_shares": 12245, "cfo": 63897, "capex": 39643})
    ttm = _with_fcf({
        key: fy[key] + ytd[key] - prior[key]
        for key in ("revenue", "operating_income", "net_income", "cfo", "capex")
    })
    ttm.update({"diluted_eps": None, "diluted_shares": None})
    return {
        "ticker": "GOOGL", "company": "Alphabet Inc.",
        "periods": [
            _period("FY2025", "fiscal_year", "2025-12-31", fy),
            _period("Q2 FY2026", "quarter", "2026-06-30", quarter),
            _period("6M FY2026", "year_to_date", "2026-06-30", ytd),
            _period("6M FY2025 comparative", "prior_year_to_date", "2025-06-30", prior),
            _period("TTM through Q2 FY2026", "ttm", "2026-06-30", ttm, basis="derived_from_reported_gaap", note="FY2025 + 6M FY2026 - 6M FY2025; TTM EPS withheld because weighted diluted denominators are not additive."),
        ],
        "balance_sheet": {"as_of": "2026-06-30", "cash_and_equivalents": 55911, "short_term_investments": 186563, "long_term_investments": 131461, "short_term_debt": 1999, "long_term_debt": 98165, "investment_scope_complete": False},
        "guidance": {"period": "FY2026", "capex_low": 175000, "capex_high": 185000, "status": "issuer_capex_guidance"},
        "earnings_quality": {"status": "distorted", "reason": "2026 H1 net income and EPS include large investment-security fair-value gains; verified after-tax per-share normalization remains unavailable", "per_model_eligible": False},
    }


def _build_amd() -> dict[str, Any]:
    fy = _with_fcf({"revenue": 34639, "operating_income": 3694, "net_income": 4335, "diluted_eps": 2.65, "diluted_shares": 1636, "cfo": 6493, "capex": 974})
    quarter = _with_fcf({"revenue": 11536, "operating_income": 1990, "net_income": 2297, "diluted_eps": 1.38, "diluted_shares": 1659, "cfo": 2366, "capex": 808})
    ytd = _with_fcf({"revenue": 21789, "operating_income": 3466, "net_income": 3680, "diluted_eps": 2.22, "diluted_shares": 1655, "cfo": 5321, "capex": 1197})
    prior = _with_fcf({"revenue": 15123, "operating_income": 672, "net_income": 1581, "diluted_eps": 0.97, "diluted_shares": 1628, "cfo": 2401, "capex": 494})
    ttm = _with_fcf({key: fy[key] + ytd[key] - prior[key] for key in ("revenue", "operating_income", "net_income", "cfo", "capex")})
    ttm.update({"diluted_eps": None, "diluted_shares": None})
    return {
        "ticker": "AMD", "company": "Advanced Micro Devices, Inc.",
        "periods": [
            _period("FY2025", "fiscal_year", "2025-12-27", fy),
            _period("Q2 FY2026", "quarter", "2026-06-27", quarter),
            _period("6M FY2026", "year_to_date", "2026-06-27", ytd),
            _period("6M FY2025 comparative", "prior_year_to_date", "2025-06-28", prior),
            _period("TTM through Q2 FY2026", "ttm", "2026-06-27", ttm, basis="derived_from_reported_gaap", note="FY2025 + 6M FY2026 - 6M FY2025; operational TTM EPS withheld."),
        ],
        "balance_sheet": {"as_of": "2026-06-27", "cash_and_equivalents": 5086, "short_term_investments": 8025, "short_term_debt": 875, "long_term_debt": 2351, "investment_scope_complete": True},
        "guidance": {"period": "Q3 FY2026", "status": "issuer_guidance_not_normalized", "reason": "release narrative requires a separately reviewed mapping before numerical display"},
    }


def _build_msft() -> dict[str, Any]:
    fy = _with_fcf({"revenue": 331839, "operating_income": 155237, "net_income": 133749, "diluted_eps": 17.95, "diluted_shares": 7453, "cfo": 182935, "capex": 115948})
    quarter = _with_fcf({"revenue": 90007, "operating_income": 40603, "net_income": 35766, "diluted_eps": 4.81, "diluted_shares": 7443, "cfo": 55441, "capex": 35802})
    return {
        "ticker": "MSFT", "company": "Microsoft Corporation",
        "periods": [
            _period("FY2026", "fiscal_year", "2026-06-30", fy),
            _period("Q4 FY2026", "quarter", "2026-06-30", quarter),
            _period("TTM at FY2026 year-end", "ttm", "2026-06-30", {**fy, "diluted_eps": None, "diluted_shares": None}, basis="same_interval_as_reported_fiscal_year", note="The latest fiscal year is the same 12-month interval as TTM; it is not a separately estimated period."),
        ],
        "balance_sheet": {"as_of": "2026-06-30", "cash_and_equivalents": 20935, "short_term_investments": 55908, "short_term_debt": 9227, "long_term_debt": 31067, "common_shares_outstanding": 7427, "investment_scope_complete": False},
        "guidance": {"status": "unavailable", "reason": "numeric next-quarter outlook is not included in the normalized earnings-release table"},
    }


def _build_lrcx() -> dict[str, Any]:
    fy = _with_fcf({"revenue": 23232.690, "operating_income": 8199.795, "net_income": 7265.396, "diluted_eps": 5.76, "diluted_shares": 1261.102, "cfo": 5857.657, "capex": 966.405})
    quarter = _with_fcf({"revenue": 6722.238, "operating_income": 2513.488, "net_income": 2277.282, "diluted_eps": 1.81, "diluted_shares": 1256.032, "cfo": 1457.228, "capex": 188.801})
    return {
        "ticker": "LRCX", "company": "Lam Research Corporation",
        "periods": [
            _period("FY2026", "fiscal_year", "2026-06-28", fy),
            _period("Q4 FY2026", "quarter", "2026-06-28", quarter),
            _period("TTM at FY2026 year-end", "ttm", "2026-06-28", {**fy, "diluted_eps": None, "diluted_shares": None}, basis="same_interval_as_reported_fiscal_year", note="Source was reported in USD thousands and normalized to USD millions; latest FY equals the TTM interval."),
        ],
        "balance_sheet": {"as_of": "2026-06-28", "cash_and_equivalents": 5579.171, "short_term_debt": 4.073, "long_term_debt": 3730.490, "investment_scope_complete": False},
        "guidance": {"period": "Q1 FY2027", "revenue_midpoint": 8100, "revenue_plus_minus": 400, "gaap_eps_midpoint": 2.15, "gaap_eps_plus_minus": 0.15, "gaap_operating_margin_midpoint_percent": 39.5, "status": "issuer_guidance_gaap"},
    }


def _build_nvda() -> dict[str, Any]:
    quarter = _with_fcf({"revenue": 96221, "operating_income": 63734, "net_income": 59688, "diluted_eps": 2.46, "diluted_shares": 24285, "cfo": None, "capex": None})
    ytd = _with_fcf({"revenue": 177837, "operating_income": 117270, "net_income": 118010, "diluted_eps": 4.85, "diluted_shares": 24338, "cfo": 74421, "capex": 4434})
    prior = _with_fcf({"revenue": 90805, "operating_income": 50078, "net_income": 45197, "diluted_eps": 1.84, "diluted_shares": 24571, "cfo": 42779, "capex": 3122})
    return {
        "ticker": "NVDA", "company": "NVIDIA Corporation", "status": "partial",
        "periods": [
            _period("Q2 FY2027", "quarter", "2026-07-26", quarter, note="Quarter-only CFO/CAPEX are not present in the reviewed statement; left null."),
            _period("6M FY2027", "year_to_date", "2026-07-26", ytd),
            _period("6M FY2026 comparative", "prior_year_to_date", "2025-07-27", prior),
        ],
        "balance_sheet": {"as_of": "2026-07-26", "cash_and_equivalents": 22443, "short_term_investments": 34143, "long_term_investments": 42783, "short_term_debt": 1000, "long_term_debt": 32366, "investment_scope_complete": False},
        "guidance": {"period": "Q3 FY2027", "revenue_midpoint": 108000, "revenue_percent_range": 2.0, "gaap_gross_margin_midpoint_percent": 74.0, "gaap_gross_margin_plus_minus_percent": 0.5, "status": "issuer_guidance_gaap"},
        "data_gap": "FY2026 official raw artifact is not connected, so FY and TTM are intentionally unavailable.",
    }


BUILDERS = {"GOOGL": _build_googl, "MSFT": _build_msft, "LRCX": _build_lrcx, "AMD": _build_amd, "NVDA": _build_nvda}


def import_saved_extended_financials(data_dir: Path) -> dict[str, Any]:
    """Normalize only hash-pinned issuer files already present in ignored local storage."""
    root = Path(data_dir)
    raw_dir = root / "raw"
    financial_dir = root / "normalized" / "financials"
    audit_dir = root / "audit" / "financials"
    results: list[dict[str, Any]] = []
    for ticker, documents in SOURCES.items():
        existing = load_json(financial_dir / f"{ticker}.json")
        try:
            normalized_documents = []
            checks: list[dict[str, Any]] = []
            for document in documents:
                path = raw_dir / document["filename"]
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                checks.append({"name": f"{document['key']}_sha256", "passed": digest == document["sha256"]})
                checks.append({"name": f"{document['key']}_nonempty", "passed": len(raw) > 1000})
                if digest != document["sha256"]:
                    raise ValueError(f"{document['key']}: SHA-256 mismatch")
                normalized_documents.append({
                    key: value for key, value in document.items() if key != "filename"
                } | {"bytes": len(raw), "parser_version": PARSER_VERSION})
            snapshot = BUILDERS[ticker]()
            snapshot.setdefault("status", "verified")
            snapshot.update({
                "currency": "USD", "accounting_standard": "US GAAP",
                "collected_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "parser_version": PARSER_VERSION, "documents": normalized_documents,
                "scope": "local_personal_only",
            })
            for period in snapshot["periods"]:
                values = period["metrics"]
                if values.get("cfo") is not None and values.get("capex") is not None:
                    checks.append({"name": f"{period['label']}_fcf_arithmetic", "passed": abs(values["fcf"] - (values["cfo"] - values["capex"])) < 1e-6})
                checks.append({"name": f"{period['label']}_period_end", "passed": bool(period.get("period_end"))})
            failed = [row for row in checks if not row["passed"]]
            if failed:
                raise ValueError("extended financial audit failed")
            audit = {"ticker": ticker, "status": "verified", "checks_passed": len(checks), "checks_total": len(checks), "checks": checks, "fixture": False, "parser_version": PARSER_VERSION}
            atomic_write_json(financial_dir / f"{ticker}.json", snapshot)
            atomic_write_json(audit_dir / f"{ticker}.json", audit)
            results.append({"ticker": ticker, "status": snapshot["status"], "periods": len(snapshot["periods"]), "checks_passed": len(checks), "checks_total": len(checks)})
        except (OSError, ValueError) as exc:
            results.append({"ticker": ticker, "status": "blocked", "reason": str(exc), "last_good_preserved": bool(existing), "stored_periods": len((existing or {}).get("periods", []))})
    return {
        "results": results,
        "tickers_connected": sum(row["status"] in {"verified", "partial"} for row in results),
        "periods_connected": sum(row.get("periods", row.get("stored_periods", 0)) for row in results),
        "audit_checks_passed": sum(row.get("checks_passed", 0) for row in results),
        "audit_checks_total": sum(row.get("checks_total", 0) for row in results),
        "parser_version": PARSER_VERSION,
    }
