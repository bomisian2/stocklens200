from __future__ import annotations

import hashlib
import io
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

from .catalog import IRDocument, PILOT_IR_DOCUMENTS
from .financial_audit import audit_normalized_snapshot
from .storage import atomic_write_bytes, atomic_write_json


USER_AGENT = "Mozilla/5.0 StockLens200-PersonalResearch"


class FinancialExtractionError(RuntimeError):
    pass


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"$", "%", "—", "-"}:
        return None
    negative = text.startswith("(") or text.endswith(")")
    text = text.strip("()").rstrip("%").lstrip("+")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    parsed = float(text)
    return -parsed if negative and parsed > 0 else parsed


def _row_numbers(frame: pd.DataFrame, label: str) -> list[list[float]]:
    matches: list[list[float]] = []
    for _, row in frame.iterrows():
        if str(row.iloc[0]).strip().casefold() != label.casefold():
            continue
        values = [_number(value) for value in row.iloc[1:]]
        matches.append([value for value in values if value is not None])
    return matches


def _find_table(tables: Iterable[pd.DataFrame], required_labels: Iterable[str]) -> pd.DataFrame:
    labels = {label.casefold() for label in required_labels}
    for frame in tables:
        first_column = {str(value).strip().casefold() for value in frame.iloc[:, 0].tolist()}
        if labels.issubset(first_column):
            return frame
    raise FinancialExtractionError(f"Required table not found: {sorted(labels)}")


def _one_row(frame: pd.DataFrame, label: str, *, at_least: int) -> list[float]:
    rows = [values for values in _row_numbers(frame, label) if len(values) >= at_least]
    if len(rows) != 1:
        raise FinancialExtractionError(f"Expected one '{label}' row, found {len(rows)}")
    return rows[0]


def _select_duplicate_row(
    frame: pd.DataFrame, label: str, *, at_least: int, minimum_first: float, maximum_first: float
) -> list[float]:
    rows = [
        values
        for values in _row_numbers(frame, label)
        if len(values) >= at_least and minimum_first <= abs(values[0]) <= maximum_first
    ]
    if len(rows) != 1:
        raise FinancialExtractionError(f"Could not disambiguate '{label}' row")
    return rows[0]


def _nth_row(frame: pd.DataFrame, label: str, *, at_least: int, index: int) -> list[float]:
    rows = [values for values in _row_numbers(frame, label) if len(values) >= at_least]
    try:
        return rows[index]
    except IndexError as exc:
        raise FinancialExtractionError(f"Could not select occurrence {index} of '{label}'") from exc


def _table_set(raw: bytes) -> list[pd.DataFrame]:
    return pd.read_html(io.StringIO(raw.decode("utf-8", errors="replace")))


def _period(
    label: str,
    kind: str,
    period_end: str,
    values: dict[str, float | None],
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
        "metrics": values,
        "note": note,
    }


def _ttm_values(
    annual: dict[str, float | None], current_ytd: dict[str, float | None], prior_ytd: dict[str, float | None]
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for key in ("revenue", "operating_income", "net_income", "cfo", "capex", "fcf"):
        inputs = (annual.get(key), current_ytd.get(key), prior_ytd.get(key))
        result[key] = None if any(value is None for value in inputs) else inputs[0] + inputs[1] - inputs[2]
    result["diluted_eps"] = None
    result["diluted_shares"] = None
    return result


def _parse_amat(annual_raw: bytes, quarter_raw: bytes) -> dict[str, Any]:
    annual_tables = _table_set(annual_raw)
    quarter_tables = _table_set(quarter_raw)

    annual_ops = _find_table(
        annual_tables,
        ["Net revenue", "Income from operations", "Net income", "Weighted average number of shares:"],
    )
    annual_cf = _find_table(annual_tables, ["Cash provided by operating activities", "Capital expenditures"])
    q_ops = _find_table(
        quarter_tables,
        ["Revenue", "Income from operations", "Net income", "Weighted average number of shares:"],
    )
    q_cf = _find_table(quarter_tables, ["Cash provided by operating activities", "Capital expenditures"])
    balance = _find_table(
        quarter_tables,
        ["Cash and cash equivalents", "Short-term investments", "Short-term debt", "Long-term debt"],
    )
    guidance = _find_table(quarter_tables, ["Total revenue", "Non-GAAP diluted EPS"])

    annual_revenue = _one_row(annual_ops, "Net revenue", at_least=4)
    annual_operating = _one_row(annual_ops, "Income from operations", at_least=4)
    annual_net = _one_row(annual_ops, "Net income", at_least=4)
    annual_eps = _select_duplicate_row(
        annual_ops, "Diluted", at_least=4, minimum_first=0, maximum_first=50
    )
    annual_shares = _select_duplicate_row(
        annual_ops, "Diluted", at_least=4, minimum_first=100, maximum_first=10000
    )
    annual_cfo = _one_row(annual_cf, "Cash provided by operating activities", at_least=4)
    annual_capex = _one_row(annual_cf, "Capital expenditures", at_least=4)
    fy = {
        "revenue": annual_revenue[2],
        "operating_income": annual_operating[2],
        "net_income": annual_net[2],
        "diluted_eps": annual_eps[2],
        "diluted_shares": annual_shares[2],
        "cfo": annual_cfo[2],
        "capex": abs(annual_capex[2]),
    }
    fy["fcf"] = fy["cfo"] - fy["capex"]

    revenue = _one_row(q_ops, "Revenue", at_least=4)
    operating = _one_row(q_ops, "Income from operations", at_least=4)
    net_income = _one_row(q_ops, "Net income", at_least=4)
    eps = _select_duplicate_row(q_ops, "Diluted", at_least=4, minimum_first=0, maximum_first=50)
    shares = _select_duplicate_row(q_ops, "Diluted", at_least=4, minimum_first=100, maximum_first=10000)
    cfo = _one_row(q_cf, "Cash provided by operating activities", at_least=4)
    capex = _one_row(q_cf, "Capital expenditures", at_least=4)
    quarter = {
        "revenue": revenue[0],
        "operating_income": operating[0],
        "net_income": net_income[0],
        "diluted_eps": eps[0],
        "diluted_shares": shares[0],
        "cfo": cfo[0],
        "capex": abs(capex[0]),
    }
    quarter["fcf"] = quarter["cfo"] - quarter["capex"]
    ytd = {
        "revenue": revenue[2],
        "operating_income": operating[2],
        "net_income": net_income[2],
        "diluted_eps": eps[2],
        "diluted_shares": shares[2],
        "cfo": cfo[2],
        "capex": abs(capex[2]),
    }
    ytd["fcf"] = ytd["cfo"] - ytd["capex"]
    prior_ytd = {
        "revenue": revenue[3],
        "operating_income": operating[3],
        "net_income": net_income[3],
        "cfo": cfo[3],
        "capex": abs(capex[3]),
    }
    prior_ytd["fcf"] = prior_ytd["cfo"] - prior_ytd["capex"]
    cash = _one_row(balance, "Cash and cash equivalents", at_least=2)
    short_investments = _one_row(balance, "Short-term investments", at_least=2)
    long_investments = _one_row(balance, "Long-term investments", at_least=2)
    short_debt = _one_row(balance, "Short-term debt", at_least=2)
    long_debt = _one_row(balance, "Long-term debt", at_least=2)
    revenue_guide = _one_row(guidance, "Total revenue", at_least=2)
    eps_guide = _one_row(guidance, "Non-GAAP diluted EPS", at_least=2)
    return {
        "ticker": "AMAT",
        "company": "Applied Materials, Inc.",
        "periods": [
            _period("FY2025", "fiscal_year", "2025-10-26", fy),
            _period("Q3 FY2026", "quarter", "2026-07-26", quarter),
            _period("9M FY2026", "year_to_date", "2026-07-26", ytd),
            _period(
                "TTM through Q3 FY2026",
                "ttm",
                "2026-07-26",
                _ttm_values(fy, ytd, prior_ytd),
                basis="derived_from_reported_gaap",
                note="FY2025 + 9M FY2026 - 9M FY2025; diluted EPS withheld because a consistent TTM share denominator is unavailable.",
            ),
        ],
        "balance_sheet": {
            "as_of": "2026-07-26",
            "cash_and_equivalents": cash[0],
            "short_term_investments": short_investments[0],
            "long_term_investments": long_investments[0],
            "short_term_debt": short_debt[0],
            "long_term_debt": long_debt[0],
        },
        "guidance": {
            "period": "Q4 FY2026",
            "revenue_midpoint": revenue_guide[0],
            "revenue_plus_minus": revenue_guide[1],
            "non_gaap_eps_midpoint": eps_guide[0],
            "non_gaap_eps_plus_minus": eps_guide[1],
            "status": "issuer_guidance_non_gaap_eps",
        },
    }


def _parse_avgo(annual_raw: bytes, quarter_raw: bytes) -> dict[str, Any]:
    annual_tables = _table_set(annual_raw)
    quarter_tables = _table_set(quarter_raw)
    annual_ops = _find_table(
        annual_tables,
        ["Net revenue", "Operating income", "Net income", "Weighted-average shares used in per share calculations:"],
    )
    q_ops = _find_table(
        quarter_tables,
        ["Net revenue", "Operating income", "Net income", "Net income per share:"],
    )
    annual_cf = _find_table(annual_tables, ["Net cash provided by operating activities", "Purchases of property, plant and equipment"])
    q_cf = _find_table(quarter_tables, ["Net cash provided by operating activities", "Purchases of property, plant and equipment"])
    balance = _find_table(quarter_tables, ["Cash and cash equivalents", "Short-term debt", "Long-term debt"])

    ar = _one_row(annual_ops, "Net revenue", at_least=5)
    ao = _one_row(annual_ops, "Operating income", at_least=5)
    an = _one_row(annual_ops, "Net income", at_least=5)
    ae = _nth_row(annual_ops, "Net income per share", at_least=5, index=-1)
    ash = _select_duplicate_row(annual_ops, "Diluted", at_least=5, minimum_first=100, maximum_first=10000)
    acfo = _one_row(annual_cf, "Net cash provided by operating activities", at_least=5)
    acapex = _one_row(annual_cf, "Purchases of property, plant and equipment", at_least=5)
    fy = {
        "revenue": ar[3],
        "operating_income": ao[3],
        "net_income": an[3],
        "diluted_eps": ae[3],
        "diluted_shares": ash[3],
        "cfo": acfo[3],
        "capex": abs(acapex[3]),
    }
    fy["fcf"] = fy["cfo"] - fy["capex"]

    revenue = _one_row(q_ops, "Net revenue", at_least=5)
    operating = _one_row(q_ops, "Operating income", at_least=5)
    net_income = _one_row(q_ops, "Net income", at_least=5)
    eps = _select_duplicate_row(q_ops, "Diluted", at_least=5, minimum_first=0, maximum_first=50)
    shares = _select_duplicate_row(q_ops, "Diluted", at_least=5, minimum_first=100, maximum_first=10000)
    cfo = _one_row(q_cf, "Net cash provided by operating activities", at_least=5)
    capex = _one_row(q_cf, "Purchases of property, plant and equipment", at_least=5)
    quarter = {
        "revenue": revenue[0],
        "operating_income": operating[0],
        "net_income": net_income[0],
        "diluted_eps": eps[0],
        "diluted_shares": shares[0],
        "cfo": cfo[0],
        "capex": abs(capex[0]),
    }
    quarter["fcf"] = quarter["cfo"] - quarter["capex"]
    ytd = {
        "revenue": revenue[3],
        "operating_income": operating[3],
        "net_income": net_income[3],
        "diluted_eps": eps[3],
        "diluted_shares": shares[3],
        "cfo": cfo[3],
        "capex": abs(capex[3]),
    }
    ytd["fcf"] = ytd["cfo"] - ytd["capex"]
    prior_ytd = {
        "revenue": revenue[4],
        "operating_income": operating[4],
        "net_income": net_income[4],
        "cfo": cfo[4],
        "capex": abs(capex[4]),
    }
    prior_ytd["fcf"] = prior_ytd["cfo"] - prior_ytd["capex"]
    cash = _one_row(balance, "Cash and cash equivalents", at_least=2)
    short_debt = _one_row(balance, "Short-term debt", at_least=2)
    long_debt = _one_row(balance, "Long-term debt", at_least=2)
    plain_text = re.sub(r"\s+", " ", quarter_raw.decode("utf-8", errors="replace"))
    revenue_match = re.search(r"Fourth quarter fiscal year 2026 revenue guidance of approximately \$([\d.]+) billion", plain_text)
    margin_match = re.search(r"Non-GAAP operating income guidance of approximately (\d+) percent", plain_text)
    if not revenue_match or not margin_match:
        raise FinancialExtractionError("Broadcom guidance anchors were not found")
    return {
        "ticker": "AVGO",
        "company": "Broadcom Inc.",
        "periods": [
            _period("FY2025", "fiscal_year", "2025-11-02", fy),
            _period("Q3 FY2026", "quarter", "2026-08-02", quarter),
            _period("9M FY2026", "year_to_date", "2026-08-02", ytd),
            _period(
                "TTM through Q3 FY2026",
                "ttm",
                "2026-08-02",
                _ttm_values(fy, ytd, prior_ytd),
                basis="derived_from_reported_gaap",
                note="FY2025 + 9M FY2026 - 9M FY2025; diluted EPS withheld because a consistent TTM share denominator is unavailable.",
            ),
        ],
        "balance_sheet": {
            "as_of": "2026-08-02",
            "cash_and_equivalents": cash[0],
            "short_term_debt": short_debt[0],
            "long_term_debt": long_debt[0],
        },
        "guidance": {
            "period": "Q4 FY2026",
            "revenue_approx": float(revenue_match.group(1)) * 1000,
            "non_gaap_operating_margin_percent": float(margin_match.group(1)),
            "status": "issuer_guidance_non_gaap_margin",
        },
    }


def _pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise FinancialExtractionError(
            "PDF extraction requires: pip install -r requirements-personal.txt"
        ) from exc
    reader = PdfReader(io.BytesIO(raw))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _groups(text: str, pattern: str, count: int) -> list[float]:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match or len(match.groups()) != count:
        raise FinancialExtractionError(f"PDF anchor did not match: {pattern[:60]}")
    return [float(group.replace(",", "")) for group in match.groups()]


def _parse_qcom(annual_raw: bytes, quarter_raw: bytes) -> dict[str, Any]:
    annual_text = re.sub(r"\s+", " ", _pdf_text(annual_raw))
    q_text = re.sub(r"\s+", " ", _pdf_text(quarter_raw))
    ar = _groups(annual_text, r"Total revenues\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", 4)
    ao = _groups(annual_text, r"Operating income\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", 4)
    an = _groups(annual_text, r"Net \(loss\) income \$ \([\d,]+\) \$ [\d,]+ \$ ([\d,]+) \$ ([\d,]+)", 2)
    ae = _groups(annual_text, r"Diluted \(loss\) earnings per share:.*?Net income \$ \([\d.]+\) \$ [\d.]+ \$ ([\d.]+) \$ ([\d.]+)", 2)
    ash = _groups(annual_text, r"Shares used in per share calculations: Basic.*?Diluted\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", 4)
    acf = _groups(annual_text, r"Net cash provided by operating activities\s+([\d,]+)\s+([\d,]+)", 2)
    acapex = _groups(annual_text, r"Capital expenditures\s+\(([\d,]+)\)\s+\(([\d,]+)\)", 2)
    fy = {
        "revenue": ar[2],
        "operating_income": ao[2],
        "net_income": an[0],
        "diluted_eps": ae[0],
        "diluted_shares": ash[2],
        "cfo": acf[0],
        "capex": acapex[0],
    }
    fy["fcf"] = fy["cfo"] - fy["capex"]

    revenue = _groups(q_text, r"Total revenues\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", 4)
    operating = _groups(q_text, r"Operating income\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", 4)
    net_income = _groups(q_text, r"Net income \$\s*([\d,]+) \$\s*([\d,]+) \$\s*([\d,]+) \$\s*([\d,]+)", 4)
    eps = _groups(q_text, r"Diluted earnings per share \$\s*([\d.]+) \$\s*([\d.]+) \$\s*([\d.]+) \$\s*([\d.]+)", 4)
    shares = _groups(q_text, r"Shares used in per share calculations: Basic.*?Diluted\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", 4)
    cfo = _groups(q_text, r"Net cash provided by operating activities\s+([\d,]+)\s+([\d,]+)", 2)
    capex = _groups(q_text, r"Capital expenditures\s+\(([\d,]+)\)\s+\(([\d,]+)\)", 2)
    quarter = {
        "revenue": revenue[0],
        "operating_income": operating[0],
        "net_income": net_income[0],
        "diluted_eps": eps[0],
        "diluted_shares": shares[0],
        "cfo": None,
        "capex": None,
        "fcf": None,
    }
    ytd = {
        "revenue": revenue[2],
        "operating_income": operating[2],
        "net_income": net_income[2],
        "diluted_eps": eps[2],
        "diluted_shares": shares[2],
        "cfo": cfo[0],
        "capex": capex[0],
    }
    ytd["fcf"] = ytd["cfo"] - ytd["capex"]
    prior_ytd = {
        "revenue": revenue[3],
        "operating_income": operating[3],
        "net_income": net_income[3],
        "cfo": cfo[1],
        "capex": capex[1],
    }
    prior_ytd["fcf"] = prior_ytd["cfo"] - prior_ytd["capex"]
    cash = _groups(q_text, r"Cash and cash equivalents \$\s*([\d,]+) \$\s*([\d,]+)", 2)
    marketable = _groups(q_text, r"Marketable securities\s+([\d,]+)\s+([\d,]+)", 2)
    short_debt = _groups(q_text, r"Short-term debt\s+([\d,]+)\s+—", 1)
    long_debt = _groups(q_text, r"Long-term debt\s+([\d,]+)\s+([\d,]+)", 2)
    guidance = _groups(q_text, r"Current Guidance Q4 FY26 Estimates1 Revenues \$([\d.]+)B - \$([\d.]+)B", 2)
    gaap_eps_guidance = _groups(q_text, r"GAAP diluted EPS \$([\d.]+) - \$([\d.]+)", 2)
    return {
        "ticker": "QCOM",
        "company": "QUALCOMM Incorporated",
        "periods": [
            _period("FY2025", "fiscal_year", "2025-09-28", fy),
            _period(
                "Q3 FY2026",
                "quarter",
                "2026-06-28",
                quarter,
                note="The earnings release provides CFO and CAPEX only on a nine-month basis.",
            ),
            _period("9M FY2026", "year_to_date", "2026-06-28", ytd),
            _period(
                "TTM through Q3 FY2026",
                "ttm",
                "2026-06-28",
                _ttm_values(fy, ytd, prior_ytd),
                basis="derived_from_reported_gaap",
                note="FY2025 + 9M FY2026 - 9M FY2025; diluted EPS withheld because tax items and a consistent TTM share denominator require separate validation.",
            ),
        ],
        "balance_sheet": {
            "as_of": "2026-06-28",
            "cash_and_equivalents": cash[0],
            "marketable_securities": marketable[0],
            "short_term_debt": short_debt[0],
            "long_term_debt": long_debt[0],
        },
        "guidance": {
            "period": "Q4 FY2026",
            "revenue_low": guidance[0] * 1000,
            "revenue_high": guidance[1] * 1000,
            "gaap_eps_low": gaap_eps_guidance[0],
            "gaap_eps_high": gaap_eps_guidance[1],
            "status": "issuer_guidance",
        },
        "earnings_quality": {
            "status": "requires_normalization_review",
            "investment_and_other_income_q3": 1014.0,
            "investment_and_other_income_9m": 1458.0,
            "tax_benefit_9m": 4136.0,
            "note": "GAAP values retained; no normalized EPS was generated.",
        },
    }


PARSERS = {"AMAT": _parse_amat, "AVGO": _parse_avgo, "QCOM": _parse_qcom}


def _download(document: IRDocument, session: requests.Session) -> bytes:
    response = session.get(document.url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if response.status_code != 200:
        raise FinancialExtractionError(f"{document.ticker}/{document.key}: HTTP {response.status_code}")
    raw = response.content
    if len(raw) < 10_000:
        raise FinancialExtractionError(f"{document.ticker}/{document.key}: response is unexpectedly small")
    if document.media_type == "application/pdf" and not raw.startswith(b"%PDF"):
        raise FinancialExtractionError(f"{document.ticker}/{document.key}: expected PDF content")
    return raw


def collect_pilot_financials(
    data_dir: str | Path,
    *,
    session: requests.Session | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Download, verify, normalize, and atomically store three issuer releases.

    A ticker's previous normalized snapshot is never overwritten unless both its
    annual and quarterly sources download and parse successfully.
    """

    root = Path(data_dir)
    client = session or requests.Session()
    collected_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    for ticker, documents in PILOT_IR_DOCUMENTS.items():
        try:
            payloads = [_download(document, client) for document in documents]
            normalized = PARSERS[ticker](payloads[0], payloads[1])
            normalized["schema_version"] = 1
            normalized["status"] = "verified"
            normalized["collected_at_utc"] = collected_at
            normalized["documents"] = [
                {
                    "key": document.key,
                    "url": document.url,
                    "published_at": document.published_at,
                    "period_end": document.period_end,
                    "media_type": document.media_type,
                    "issuer": document.issuer,
                    "distribution": document.distribution,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "redistribution_authorized": False,
                    "storage_scope": "local_personal_only",
                }
                for document, raw in zip(documents, payloads, strict=True)
            ]
            payload_map = {
                document.key: raw for document, raw in zip(documents, payloads, strict=True)
            }
            audit = audit_normalized_snapshot(normalized, payload_map, verified_at=now)
            if audit["status"] != "verified":
                raise FinancialExtractionError(
                    f"{ticker}: independent fact audit failed ({len(audit['mismatches'])} mismatches)"
                )
            for document, raw in zip(documents, payloads, strict=True):
                extension = "pdf" if document.media_type == "application/pdf" else "html"
                atomic_write_bytes(root / "raw" / f"{ticker}_{document.key}.{extension}", raw)
            atomic_write_json(root / "audit" / "financials" / f"{ticker}.json", audit)
            atomic_write_json(root / "normalized" / "financials" / f"{ticker}.json", normalized)
            results.append(
                {
                    "ticker": ticker,
                    "status": "verified",
                    "periods": len(normalized["periods"]),
                    "latest_period_end": normalized["periods"][-1]["period_end"],
                    "audit_checks": audit["checks_passed"],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ticker": ticker,
                    "status": "blocked",
                    "periods": 0,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
    status = {
        "schema_version": 1,
        "mode": "local_personal_research",
        "public_publish_allowed": False,
        "collected_at_utc": collected_at,
        "financials": results,
        "financial_tickers_verified": sum(row["status"] == "verified" for row in results),
        "financial_periods_verified": sum(row["periods"] for row in results),
        "financial_audits_verified": sum(row["status"] == "verified" for row in results),
        "market": {
            "status": "blocked",
            "reason": "No ALPHAVANTAGE_API_KEY was supplied; Yahoo automated collection is disabled by policy.",
            "tickers_verified": 0,
        },
        "scheduler": {
            "status": "not_verified",
            "reason": "Manual collection is not evidence of a scheduled event.",
        },
    }
    atomic_write_json(root / "status.json", status)
    return status
