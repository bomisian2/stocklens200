"""Read-only inventory for Same-Date P4 local evidence onboarding.

The inventory deliberately does not normalize provider data, build candidate
registries, approve evidence, or create a decision.  Its job is to establish
what bytes are actually preserved before a provider-specific parser is written.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INVENTORY_VERSION = "same-date-evidence-inventory-p4-v1"
TARGET_TICKERS = ("AVGO", "NVDA")
EVIDENCE_CLASSES = {"provider_native", "normalized_only", "derived_only", "missing"}


class EvidenceInventoryError(ValueError):
    """Raised when local evidence metadata is malformed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceInventoryError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise EvidenceInventoryError(f"JSON root must be an object: {path}")
    return value


def _file_record(path: Path, *, role: str, source: str) -> dict[str, Any]:
    return {
        "role": role,
        "path": str(path.resolve()),
        "source": source,
        "source_hash": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _csv_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"rows": 0, "first_date": None, "last_date": None, "columns": []}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    dates = [str(row.get("date")) for row in rows if row.get("date")]
    return {
        "rows": len(rows),
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
        "columns": list(rows[0]) if rows else [],
    }


def _alpha_market_candidate(private_root: Path, ticker: str) -> dict[str, Any]:
    market_root = private_root / "market"
    data_path = market_root / f"{ticker}.csv"
    manifest_path = market_root / f"{ticker}.manifest.json"
    manifest = _read_json(manifest_path)
    if manifest is None or not data_path.is_file():
        return _missing_target(
            f"alpha-vantage-{ticker.lower()}-daily",
            "Alpha Vantage local CSV or manifest is missing",
            "preserve a permitted provider-native response before normalization; do not reconstruct it from CSV",
        )
    summary = _csv_summary(data_path)
    data_hash = _sha256(data_path)
    declared_hash = str(manifest.get("sha256") or "").upper()
    hash_match = len(declared_hash) == 64 and declared_hash == data_hash
    captured_at = _iso_timestamp(manifest.get("collected_at_utc"))
    return {
        "candidate_id": f"alpha-vantage-{ticker.lower()}-daily",
        "classification": "normalized_only",
        "provider_native_raw_exists": False,
        "normalized_only": True,
        "source": "Alpha Vantage TIME_SERIES_DAILY local normalized CSV",
        "source_hash": data_hash,
        "as_of": manifest.get("market_date") or summary["last_date"],
        "available_at": captured_at,
        "captured_at_exact": captured_at is not None,
        "provider_timestamp": None,
        "provider_date": manifest.get("provider_market_date"),
        "revision_vintage": None,
        "pit_status": "capture_time_only",
        "rights_status": "local_only_manifest_claim_unverified_document",
        "parser_eligible": False,
        "parser_block_reason": "provider response bytes were not preserved",
        "price_policy": manifest.get("price_policy"),
        "adjusted": bool(manifest.get("adjusted_prices_available")),
        "manifest_hash_matches_data": hash_match,
        "rows": summary["rows"],
        "history_start": summary["first_date"],
        "history_end": summary["last_date"],
        "columns": summary["columns"],
        "files": [
            _file_record(data_path, role="normalized_data", source="Alpha Vantage"),
            _file_record(manifest_path, role="local_manifest", source="StockLens"),
        ],
        "reason": (
            "actual local observations exist, but only normalized CSV and a StockLens manifest remain"
        ),
    }


def _top10_candidate(
    top10_root: Path,
    *,
    candidate_id: str,
    filename: str,
    source: str,
    classification: str,
    as_of: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if classification not in EVIDENCE_CLASSES - {"provider_native"}:
        raise EvidenceInventoryError("Top10 local artifacts cannot be labelled provider_native")
    path = top10_root / filename
    value = _read_json(path)
    if value is None:
        return {
            "candidate_id": candidate_id,
            "classification": "missing",
            "provider_native_raw_exists": False,
            "files": [],
            "reason": f"local {filename} is missing",
        }
    generated_at = _iso_timestamp(value.get("generatedAt"))
    return {
        "candidate_id": candidate_id,
        "classification": classification,
        "provider_native_raw_exists": False,
        "normalized_only": classification == "normalized_only",
        "source": source,
        "source_hash": _sha256(path),
        "as_of": as_of or value.get("asOfDate"),
        "available_at": generated_at,
        "captured_at_exact": False,
        "provider_timestamp": None,
        "revision_vintage": None,
        "pit_status": "unverified_without_provider_raw",
        "rights_status": "unverified_no_preserved_terms_document",
        "parser_eligible": False,
        "parser_block_reason": "file is a cross-project normalized or derived artifact, not provider-native bytes",
        "files": [_file_record(path, role="cross_project_artifact", source=source)],
        "reason": "provider response bytes and a hash-linked rights document were not preserved",
        **(details or {}),
    }


def _price_history_details(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if value is None:
        return {}
    records = value.get("records")
    output: dict[str, Any] = {"ticker_rows": {}, "adjustment_policy": None, "as_of": None}
    if not isinstance(records, dict):
        return output
    for ticker in TARGET_TICKERS:
        rows = records.get(ticker)
        if not isinstance(rows, list):
            output["ticker_rows"][ticker] = {"rows": 0, "first": None, "last": None}
            continue
        dates = [str(row.get("date")) for row in rows if isinstance(row, dict) and row.get("date")]
        output["ticker_rows"][ticker] = {
            "rows": len(rows),
            "first": min(dates) if dates else None,
            "last": max(dates) if dates else None,
            "fields": sorted(rows[0]) if rows and isinstance(rows[0], dict) else [],
        }
    last_dates = [
        row["last"] for row in output["ticker_rows"].values() if row.get("last")
    ]
    output["as_of"] = max(last_dates) if last_dates else None
    fields = {
        field
        for row in output["ticker_rows"].values()
        for field in row.get("fields", [])
    }
    output["adjustment_policy"] = (
        "adjusted_field_claim_not_replayable_without_raw"
        if "adjusted_close" in fields
        else "missing_or_unverified"
    )
    return output


def _consensus_details(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if value is None:
        return {}
    records = value.get("records")
    selected: dict[str, Any] = {}
    if isinstance(records, dict):
        for ticker in TARGET_TICKERS:
            row = records.get(ticker)
            if isinstance(row, dict):
                selected[ticker] = {
                    "collected_at": _iso_timestamp(row.get("collectedAt")),
                    "fiscal_end": row.get("fiscalEnd"),
                    "next_fiscal_end": row.get("nextFiscalEnd"),
                    "analyst_count": row.get("analystCount"),
                    "accounting_basis": None,
                    "currency": None,
                    "unit": None,
                }
    return {"records": selected, "basis_status": "unknown_gaap_non_gaap_and_unit"}


def _decision_ledger_details(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if value is None:
        return {}
    rows = value.get("records")
    latest: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for ticker in TARGET_TICKERS:
            ticker_rows = [
                row for row in rows
                if isinstance(row, dict) and row.get("ticker") == ticker and row.get("date")
            ]
            if ticker_rows:
                row = sorted(ticker_rows, key=lambda item: str(item["date"]))[-1]
                inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
                latest[ticker] = {
                    "date": row.get("date"),
                    "price_as_of": inputs.get("priceAsOf"),
                    "eps_as_of": inputs.get("epsAsOf"),
                    "forward_pe_present": inputs.get("forwardPe") is not None,
                }
    dates = [str(row["date"]) for row in latest.values() if row.get("date")]
    return {"latest_records": latest, "as_of": max(dates) if dates else None}


def _missing_target(candidate_id: str, reason: str, acquisition_path: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "classification": "missing",
        "provider_native_raw_exists": False,
        "normalized_only": False,
        "source": None,
        "source_hash": None,
        "as_of": None,
        "available_at": None,
        "captured_at_exact": False,
        "provider_timestamp": None,
        "revision_vintage": None,
        "pit_status": "missing",
        "rights_status": "missing",
        "parser_eligible": False,
        "parser_block_reason": reason,
        "files": [],
        "reason": reason,
        "acquisition_path": acquisition_path,
    }


def _issuer_supporting_evidence(private_root: Path) -> list[dict[str, Any]]:
    """List issuer-native files that do not satisfy a Same-Date target role."""

    results: list[dict[str, Any]] = []
    for ticker in TARGET_TICKERS:
        normalized_path = private_root / "normalized" / "financials" / f"{ticker}.json"
        normalized = _read_json(normalized_path)
        if normalized is None:
            continue
        documents = normalized.get("documents")
        if not isinstance(documents, list):
            continue
        for document in documents:
            if not isinstance(document, dict):
                continue
            declared_hash = str(document.get("sha256") or "").upper()
            matching_files = [
                path
                for path in (private_root / "raw").glob(f"{ticker}_*")
                if _sha256(path) == declared_hash
            ]
            if len(matching_files) != 1:
                continue
            raw_path = matching_files[0]
            results.append({
                "ticker": ticker,
                "classification": "provider_native",
                "provider_native_raw_exists": True,
                "source": document.get("distribution"),
                "source_hash": declared_hash,
                "as_of": document.get("period_end"),
                "available_at": _iso_timestamp(document.get("published_at")),
                "provider_timestamp": _iso_timestamp(document.get("published_at")),
                "revision_vintage": None,
                "rights_status": "local_personal_only_no_redistribution_claim",
                "same_date_target_roles_satisfied": [],
                "reason": (
                    "issuer financial document is genuine supporting evidence, but it is not "
                    "a PIT consensus, price, peer-multiple, acceptance-time, regime, or rights artifact"
                ),
                "file": _file_record(
                    raw_path,
                    role="issuer_financial_raw",
                    source=str(document.get("distribution") or "issuer"),
                ),
            })
    return results


def build_p4_evidence_inventory(
    *,
    private_data_root: str | Path,
    top10_data_root: str | Path,
    inspected_at: str,
) -> dict[str, Any]:
    """Inventory exact local evidence without network access or promotion."""

    inspected = _iso_timestamp(inspected_at)
    if inspected is None:
        raise EvidenceInventoryError("inspected_at must be a timezone-aware ISO timestamp")
    private_root = Path(private_data_root)
    top10_root = Path(top10_data_root)

    alpha_avgo = _alpha_market_candidate(private_root, "AVGO")
    alpha_nvda = _alpha_market_candidate(private_root, "NVDA")
    price_path = top10_root / "price-history.json"
    consensus_path = top10_root / "top100-consensus.json"
    ledger_path = top10_root / "decision-ledger.json"
    sec_path = top10_root / "sec-quarterly-ledger.json"
    macro_path = top10_root / "macro-snapshot.json"
    price_details = _price_history_details(price_path)
    ledger_details = _decision_ledger_details(ledger_path)

    candidates = [
        alpha_avgo,
        alpha_nvda,
        _top10_candidate(
            top10_root,
            candidate_id="top10-avgo-nvda-latest-price",
            filename="decision-ledger.json",
            source="Nasdaq Top10 derived decision ledger price",
            classification="derived_only",
            as_of=ledger_details.get("as_of"),
            details=ledger_details,
        ),
        _top10_candidate(
            top10_root,
            candidate_id="top10-avgo-nvda-price-history",
            filename="price-history.json",
            source="Nasdaq Top10 normalized Yahoo price history",
            classification="normalized_only",
            as_of=price_details.get("as_of"),
            details=price_details,
        ),
        _top10_candidate(
            top10_root,
            candidate_id="top10-avgo-nvda-forward-eps",
            filename="top100-consensus.json",
            source="Nasdaq Top10 normalized Nasdaq Analyst Research consensus",
            classification="normalized_only",
            details=_consensus_details(consensus_path),
        ),
        _top10_candidate(
            top10_root,
            candidate_id="top10-company-forward-per-ledger",
            filename="decision-ledger.json",
            source="Nasdaq Top10 derived decision ledger",
            classification="derived_only",
            as_of=ledger_details.get("as_of"),
            details={
                **ledger_details,
                "basis_status": "forward PE values lack a hash-linked underlying EPS basis",
            },
        ),
        _missing_target(
            "independent-qqq-soxx-peer-forward-multiples",
            "no independent same-basis QQQ, SOXX, and peer Forward multiple ledger was found",
            "preserve provider-native bytes plus basis, constituent, weighting, timestamp, and rights metadata",
        ),
        _top10_candidate(
            top10_root,
            candidate_id="target-sec-acceptance-ledger",
            filename="sec-quarterly-ledger.json",
            source="Nasdaq Top10 normalized SEC quarterly ledger",
            classification="missing",
            details={
                "target_records": {"AVGO": 0, "NVDA": 0},
                "acceptance_rule_status": "missing_for_targets",
                "reason": "local SEC ledger exists but contains no AVGO or NVDA target record",
            },
        ),
        _top10_candidate(
            top10_root,
            candidate_id="top10-market-regime-proxy",
            filename="macro-snapshot.json",
            source="Nasdaq Top10 derived Yahoo market proxy",
            classification="derived_only",
            details={"official_macro": False, "proxy_label": "cross_project_market_proxy"},
        ),
        _missing_target(
            "usage-rights-documents",
            "no provider terms document bytes with SHA-256 and review metadata were found",
            "save the applicable terms document and review record without copying restricted market data into Git",
        ),
    ]

    parser_eligible = [row for row in candidates if row.get("parser_eligible") is True]
    native = [row for row in candidates if row.get("provider_native_raw_exists") is True]
    supporting_native = _issuer_supporting_evidence(private_root)
    missing = [row["candidate_id"] for row in candidates if row["classification"] == "missing"]
    blocked = [
        row["candidate_id"]
        for row in candidates
        if row["classification"] != "missing" and row.get("parser_eligible") is not True
    ]
    return {
        "inventory_version": INVENTORY_VERSION,
        "inspected_at": inspected,
        "target_tickers": list(TARGET_TICKERS),
        "network_calls": 0,
        "candidate_generation_attempted": False,
        "approval_requested": False,
        "v1_1_snapshot_created": False,
        "decision_created": False,
        "operational_eligible": False,
        "provider_native_raw_count": len(native),
        "same_date_target_provider_native_raw_count": len(native),
        "supporting_provider_native_raw_count": len(supporting_native),
        "provider_parser_eligible_count": len(parser_eligible),
        "provider_parsers_implemented": [],
        "status": "blocked",
        "block_stage": "provider_native_evidence_inventory",
        "block_reason": (
            "no required Same-Date target has preserved provider-native bytes with hash-linked rights evidence"
        ),
        "candidates": candidates,
        "supporting_provider_native_evidence": supporting_native,
        "missing_targets": missing,
        "blocked_local_targets": blocked,
        "qa_handoff_ready": False,
    }


def write_p4_inventory(report: dict[str, Any], output_path: str | Path) -> Path:
    """Write a private inventory atomically without modifying source evidence."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination
