from __future__ import annotations

import json
import math
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .financials import (
    FinancialDataError,
    FinancialPeriod,
    ProviderDiagnostic,
    merge_cik_maps,
    normalize_sec_ticker,
    validate_ticker_cik,
)
from .sec_files import normalized_periods_sha256, sha256_file


MANIFEST_SCHEMA_VERSION = 1
NORMALIZATION_VERSION = "issuer-ir-manifest-v1"
SUPPORTED_ARTIFACT_ROLE = "financial_report_pdf"
OFFICIAL_IR_HOSTS = {
    "GOOGL": {"abc.xyz", "s206.q4cdn.com"},
}
MONEY_FIELDS = {
    "revenue", "operating_income", "net_income", "pretax_income", "income_tax",
    "cfo", "capex", "fcf", "cash", "total_debt", "equity", "sbc",
    "depreciation_amortization", "buybacks",
}
REQUIRED_PERIOD_FIELDS = {
    "ticker", "cik", "company_name", "fiscal_year", "period_type", "period_end",
    "filed_at", "available_at_utc", "availability_basis", "availability_estimated",
    "form", "currency", "revenue", "operating_income", "net_income", "diluted_eps",
    "diluted_shares", "cfo", "capex", "artifact",
}


def _utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601 date") from exc


def _resolve(base_dir: Path, relative: str) -> Path:
    candidate = (base_dir / relative).resolve()
    if not candidate.is_relative_to(base_dir.resolve()):
        raise ValueError("artifact path must stay inside the manifest directory")
    if not candidate.is_file():
        raise ValueError(f"artifact file does not exist: {relative}")
    return candidate


def _official_ir_url(ticker: str, source_url: str) -> None:
    parsed = urlparse(source_url)
    hosts = OFFICIAL_IR_HOSTS.get(ticker, set())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in hosts:
        raise ValueError(f"{ticker} source_url must use an allow-listed official IR host")


class IssuerIrFileProvider:
    """Hash-verified, offline provider for issuer-published financial-report PDFs.

    The provider deliberately consumes a reviewed normalization manifest instead of
    guessing arbitrary PDF table layouts. It verifies the original PDF hash, issuer
    identity, GAAP units, availability timing, and arithmetic before creating
    ``FinancialPeriod`` objects.
    """

    name = "issuer_ir_official_files"

    def __init__(self, *, manifest_path: Path, payload: dict[str, Any]) -> None:
        self.manifest_path = manifest_path.resolve()
        self.base_dir = self.manifest_path.parent
        self.obtained_at_utc = _utc(str(payload["obtained_at_utc"]), "obtained_at_utc")
        self._period_rows = list(payload.get("periods") or [])
        self._artifacts: dict[str, dict[str, Any]] = {}
        for raw in payload.get("artifacts") or []:
            if raw.get("role") != SUPPORTED_ARTIFACT_ROLE:
                raise ValueError("unsupported issuer IR artifact role")
            relative = str(raw.get("path") or "")
            path = _resolve(self.base_dir, relative)
            if path.suffix.lower() != ".pdf":
                raise ValueError("issuer IR artifact must be a PDF")
            if path.read_bytes()[:5] != b"%PDF-":
                raise ValueError("issuer IR artifact does not have a PDF signature")
            ticker = normalize_sec_ticker(str(raw.get("ticker") or ""))
            cik = int(raw.get("cik"))
            validate_ticker_cik(ticker, cik, provider=self.name)
            source_url = str(raw.get("source_url") or "")
            _official_ir_url(ticker, source_url)
            digest = sha256_file(path)
            if digest != str(raw.get("sha256") or "").lower():
                raise ValueError(f"{relative} SHA-256 mismatch")
            self._artifacts[relative] = {
                "role": SUPPORTED_ARTIFACT_ROLE,
                "path": path,
                "ticker": ticker,
                "cik": cik,
                "source_url": source_url,
                "sha256": digest,
                "byte_size": path.stat().st_size,
            }
        if not self._artifacts:
            raise ValueError("issuer IR manifest requires at least one PDF artifact")

    @classmethod
    def from_manifest(cls, manifest_path: str | Path) -> "IssuerIrFileProvider":
        path = Path(manifest_path).resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("issuer IR import manifest is unreadable") from exc
        if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported issuer IR manifest schema")
        if payload.get("provider") != "issuer_ir_official_files":
            raise ValueError("manifest provider must be issuer_ir_official_files")
        if payload.get("fixture") is not False:
            raise ValueError("operational issuer IR manifest must declare fixture=false")
        obtained = _utc(str(payload.get("obtained_at_utc") or ""), "obtained_at_utc")
        if obtained > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("obtained_at_utc cannot be in the future")
        return cls(manifest_path=path, payload=payload)

    def cik_map(self) -> dict[str, int]:
        return merge_cik_maps(
            {item["ticker"]: int(item["cik"]) for item in self._artifacts.values()},
            provider=self.name,
        )

    def _period(self, raw: dict[str, Any], artifact: dict[str, Any]) -> FinancialPeriod:
        missing = sorted(REQUIRED_PERIOD_FIELDS - set(raw))
        if missing:
            raise ValueError(f"issuer IR period is missing fields: {', '.join(missing)}")
        ticker = normalize_sec_ticker(str(raw["ticker"]))
        cik = int(raw["cik"])
        if ticker != artifact["ticker"] or cik != artifact["cik"]:
            raise ValueError("period ticker/CIK does not match its source artifact")
        validate_ticker_cik(ticker, cik, provider=self.name)
        period_type = str(raw["period_type"])
        if period_type not in {"FY", "Q"}:
            raise ValueError("issuer IR period_type must be FY or Q")
        period_end = _date(str(raw["period_end"]), "period_end")
        period_start = raw.get("period_start")
        if period_start is not None and _date(str(period_start), "period_start") > period_end:
            raise ValueError("period_start cannot be after period_end")
        filed_at = _date(str(raw["filed_at"]), "filed_at")
        available_at = _utc(str(raw["available_at_utc"]), "available_at_utc")
        if available_at.date() < filed_at:
            raise ValueError("available_at_utc cannot precede filed_at")
        if str(raw["currency"]) != "USD" or str(raw.get("taxonomy") or "") != "us-gaap":
            raise ValueError("issuer IR pilot accepts only USD US-GAAP periods")
        values: dict[str, float | None] = {}
        for field in MONEY_FIELDS | {"diluted_eps", "diluted_shares"}:
            value = raw.get(field)
            if value is None:
                values[field] = None
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{field} must be a finite number or null")
            else:
                values[field] = float(value)
        if values["capex"] is not None and values["capex"] < 0:
            raise ValueError("capex must be recorded as a positive cash outflow")
        if values["cfo"] is not None and values["capex"] is not None:
            expected_fcf = values["cfo"] - values["capex"]
            if values["fcf"] is None or abs(values["fcf"] - expected_fcf) > max(1e-6, abs(expected_fcf) * 1e-9):
                raise ValueError("fcf must equal cfo minus capex")
        state = "degraded" if bool(raw["availability_estimated"]) or period_type == "Q" else "verified"
        notes = str(raw.get("validation_notes") or "")
        if bool(raw["availability_estimated"]):
            notes = "; ".join(filter(None, [notes, "exact publication time unavailable; conservative availability estimate used"]))
        allowed = {field.name for field in fields(FinancialPeriod)}
        payload = {
            **{key: value for key, value in raw.items() if key in allowed},
            **values,
            "ticker": ticker,
            "cik": cik,
            "period_end": period_end.isoformat(),
            "period_start": None if period_start is None else str(period_start),
            "filed_at": filed_at.isoformat(),
            "available_at_utc": available_at.isoformat(),
            "source": "alphabet_official_ir",
            "source_url": artifact["source_url"],
            "taxonomy": "us-gaap",
            "validation_state": state,
            "validation_notes": notes,
            "source_artifact_sha256": artifact["sha256"],
            "source_obtained_at_utc": self.obtained_at_utc.isoformat(),
            "normalization_version": NORMALIZATION_VERSION,
        }
        return FinancialPeriod(**payload)

    def fetch_periods(self, ticker: str, cik: int, *, as_of: datetime) -> list[FinancialPeriod]:
        ticker = normalize_sec_ticker(ticker)
        validate_ticker_cik(ticker, cik, provider=self.name)
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        periods: list[FinancialPeriod] = []
        try:
            for raw in self._period_rows:
                if normalize_sec_ticker(str(raw.get("ticker") or "")) != ticker:
                    continue
                artifact = self._artifacts.get(str(raw.get("artifact") or ""))
                if artifact is None:
                    raise ValueError("period references an unknown source artifact")
                period = self._period(raw, artifact)
                if period.cik != int(cik):
                    raise ValueError("requested CIK does not match normalized period")
                if _utc(period.available_at_utc, "available_at_utc") <= as_of.astimezone(timezone.utc):
                    periods.append(period)
        except ValueError as exc:
            raise FinancialDataError(ProviderDiagnostic(
                self.name, "local", ticker, "offline_schema", error_type=type(exc).__name__
            )) from exc
        return sorted(periods, key=lambda row: (row.period_end, row.period_type, row.available_at_utc))

    def provenance(self, *, periods: Iterable[FinancialPeriod]) -> dict[str, Any]:
        period_list = list(periods)
        return {
            "schema_version": 1,
            "provider": self.name,
            "manifest": self.manifest_path.name,
            "obtained_at_utc": self.obtained_at_utc.isoformat(),
            "normalization_version": NORMALIZATION_VERSION,
            "artifacts": [
                {
                    "role": item["role"],
                    "path": item["path"].relative_to(self.base_dir).as_posix(),
                    "ticker": item["ticker"],
                    "cik": item["cik"],
                    "source_url": item["source_url"],
                    "sha256": item["sha256"],
                    "byte_size": item["byte_size"],
                }
                for item in self._artifacts.values()
            ],
            "normalized_period_count": len(period_list),
            "normalized_sha256": normalized_periods_sha256(period_list),
            "fixture": False,
        }
