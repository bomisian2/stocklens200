from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import requests

from .financials import (
    KNOWN_CIKS,
    SEC_COMPANY_FACTS_URL,
    SEC_COMPANY_TICKERS_URL,
    SEC_SUBMISSIONS_URL,
    FinancialDataError,
    ProviderDiagnostic,
    build_cik_map,
    merge_cik_maps,
    normalize_sec_ticker,
)
from .sec_files import SecFileProvider, build_manifest_artifact


SAMPLE_TICKERS = ("GOOGL", "AMAT", "AVGO", "QCOM")
OFFICIAL_SEC_HOSTS = {"www.sec.gov", "data.sec.gov"}
DEFAULT_MINIMUM_INTERVAL_SECONDS = 0.6
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 45.0
MAX_JSON_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class DownloadedSecArtifact:
    role: str
    ticker: str | None
    cik: int | None
    path: str
    source_url: str
    sha256: str
    byte_size: int


def _safe_headers(headers: Any) -> dict[str, str]:
    allowed = {
        "server", "content-type", "retry-after", "date", "cache-control",
        "content-length", "via", "x-cache",
    }
    return {
        str(key).lower(): str(value)[:160]
        for key, value in headers.items()
        if str(key).lower() in allowed
    }


def _endpoint_name(url: str) -> str:
    path = urlparse(url).path.lower()
    if "company_tickers" in path:
        return "company_tickers"
    if "companyfacts" in path:
        return "companyfacts"
    if "submissions" in path:
        return "submissions"
    return "other"


def _official_sec_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in OFFICIAL_SEC_HOSTS:
        raise ValueError("only official HTTPS SEC endpoints are allowed")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(payload: Any) -> bytes:
    content = getattr(payload, "content", b"")
    if isinstance(content, str):
        return content.encode("utf-8")
    return bytes(content)


class SecWindowsDownloader:
    """One-shot official SEC downloader for an operator-controlled Windows PC.

    There is deliberately no retry loop. The ticker map is the single access
    probe; any non-200 response stops the run before company endpoints are used.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        session: requests.Session | None = None,
        minimum_interval: float = DEFAULT_MINIMUM_INTERVAL_SECONDS,
        timeout: tuple[float, float] = (
            DEFAULT_CONNECT_TIMEOUT_SECONDS,
            DEFAULT_READ_TIMEOUT_SECONDS,
        ),
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not user_agent.strip() or "@" not in user_agent or "http" not in user_agent:
            raise ValueError("SEC User-Agent must include a contact email and project URL")
        if minimum_interval < DEFAULT_MINIMUM_INTERVAL_SECONDS:
            raise ValueError(
                f"minimum_interval must be at least {DEFAULT_MINIMUM_INTERVAL_SECONDS} seconds"
            )
        self.user_agent = user_agent.strip()
        self.session = session or requests.Session()
        self.minimum_interval = minimum_interval
        self.timeout = timeout
        self.sleep = sleep
        self.monotonic = monotonic
        self.now = now
        self._last_request: float | None = None
        self.request_count = 0
        self.company_requests_started = False

    def _diagnostic(
        self,
        url: str,
        category: str,
        *,
        status: int | None = None,
        error_type: str | None = None,
        headers: Any = None,
    ) -> ProviderDiagnostic:
        return ProviderDiagnostic(
            provider="sec_windows_pc",
            domain=urlparse(url).hostname or "unknown",
            endpoint=_endpoint_name(url),
            category=category,
            http_status=status,
            timeout_seconds=self.timeout[1],
            error_type=error_type,
            response_headers=_safe_headers(headers or {}),
        )

    def get_json_once(self, url: str) -> tuple[dict[str, Any], bytes, ProviderDiagnostic]:
        _official_sec_url(url)
        self.request_count += 1
        if _endpoint_name(url) in {"companyfacts", "submissions"}:
            self.company_requests_started = True
        if self._last_request is not None:
            elapsed = self.monotonic() - self._last_request
            if elapsed < self.minimum_interval:
                self.sleep(self.minimum_interval - elapsed)
        try:
            response = self.session.get(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                },
                timeout=self.timeout,
                allow_redirects=False,
            )
            self._last_request = self.monotonic()
        except requests.ConnectTimeout as exc:
            raise FinancialDataError(
                self._diagnostic(url, "connect_timeout", error_type=type(exc).__name__)
            ) from exc
        except requests.ReadTimeout as exc:
            raise FinancialDataError(
                self._diagnostic(url, "read_timeout", error_type=type(exc).__name__)
            ) from exc
        except requests.exceptions.SSLError as exc:
            raise FinancialDataError(
                self._diagnostic(url, "tls", error_type=type(exc).__name__)
            ) from exc
        except requests.ConnectionError as exc:
            error_name = type(exc.__cause__ or exc).__name__
            category = "dns_or_connection"
            raise FinancialDataError(
                self._diagnostic(url, category, error_type=error_name)
            ) from exc
        except requests.RequestException as exc:
            raise FinancialDataError(
                self._diagnostic(url, "request", error_type=type(exc).__name__)
            ) from exc

        diagnostic = self._diagnostic(
            url,
            "ok" if response.status_code == 200 else "http",
            status=int(response.status_code),
            headers=response.headers,
        )
        if response.status_code != 200:
            if hasattr(response, "close"):
                response.close()
            raise FinancialDataError(diagnostic)
        content = _json_bytes(response)
        if hasattr(response, "close"):
            response.close()
        if not content or len(content) > MAX_JSON_BYTES:
            raise FinancialDataError(
                self._diagnostic(url, "response_size", status=200, headers=response.headers)
            )
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinancialDataError(
                self._diagnostic(
                    url, "invalid_json", status=200,
                    error_type=type(exc).__name__, headers=response.headers,
                )
            ) from exc
        if not isinstance(payload, dict):
            raise FinancialDataError(self._diagnostic(url, "invalid_json_root", status=200))
        return payload, content, diagnostic

    def collect(
        self,
        *,
        output_root: str | Path,
        tickers: Iterable[str] = SAMPLE_TICKERS,
        as_of: datetime,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        normalized_tickers = tuple(normalize_sec_ticker(ticker) for ticker in tickers)
        unknown = [ticker for ticker in normalized_tickers if ticker not in KNOWN_CIKS]
        if unknown:
            raise ValueError(f"sample ticker CIK is not pinned: {unknown}")

        root = Path(output_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".staging-{uuid.uuid4().hex}"
        staging.mkdir()
        diagnostics: list[dict[str, Any]] = []
        artifacts: list[DownloadedSecArtifact] = []
        try:
            # GOOGL Company Facts is the single access probe. A 403 or any
            # other failure raises immediately. A successful response is kept
            # and reused below, so the probe never creates a duplicate request.
            probe_ticker = normalized_tickers[0]
            probe_cik = KNOWN_CIKS[probe_ticker]
            probe_url = SEC_COMPANY_FACTS_URL.format(cik=probe_cik)
            probe_payload, probe_bytes, probe = self.get_json_once(probe_url)
            diagnostics.append(probe.to_dict())
            try:
                probe_payload_cik = int(probe_payload["cik"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FinancialDataError(ProviderDiagnostic(
                    "sec_windows_pc", "data.sec.gov", "companyfacts",
                    "cik_missing_or_invalid", error_type=type(exc).__name__,
                )) from exc
            if probe_payload_cik != probe_cik:
                raise FinancialDataError(ProviderDiagnostic(
                    "sec_windows_pc", "data.sec.gov", "companyfacts", "cik_mismatch",
                    error_type=(
                        f"expected_CIK{probe_cik:010d}_received_CIK{probe_payload_cik:010d}"
                    ),
                ))
            probe_path = staging / f"CIK{probe_cik:010d}.json"
            probe_path.write_bytes(probe_bytes)
            artifacts.append(DownloadedSecArtifact(
                role="companyfacts", ticker=probe_ticker, cik=probe_cik,
                path=probe_path.name, source_url=probe_url,
                sha256=_sha256_bytes(probe_bytes), byte_size=len(probe_bytes),
            ))

            ticker_payload, ticker_bytes, ticker_diagnostic = self.get_json_once(
                SEC_COMPANY_TICKERS_URL
            )
            diagnostics.append(ticker_diagnostic.to_dict())
            official_map = merge_cik_maps(build_cik_map(ticker_payload), provider="sec_windows_pc")
            for ticker in normalized_tickers:
                expected = KNOWN_CIKS[ticker]
                if official_map.get(ticker) != expected:
                    raise FinancialDataError(ProviderDiagnostic(
                        provider="sec_windows_pc",
                        domain="www.sec.gov",
                        endpoint=ticker,
                        category="ticker_cik_mismatch",
                        error_type=(
                            f"expected_CIK{expected:010d}_received_"
                            f"CIK{int(official_map.get(ticker) or 0):010d}"
                        ),
                    ))
            ticker_path = staging / "company_tickers.json"
            ticker_path.write_bytes(ticker_bytes)
            artifacts.append(DownloadedSecArtifact(
                role="ticker_map", ticker=None, cik=None, path=ticker_path.name,
                source_url=SEC_COMPANY_TICKERS_URL, sha256=_sha256_bytes(ticker_bytes),
                byte_size=len(ticker_bytes),
            ))

            for ticker in normalized_tickers:
                cik = KNOWN_CIKS[ticker]
                for role, url, filename in (
                    (
                        "companyfacts", SEC_COMPANY_FACTS_URL.format(cik=cik),
                        f"CIK{cik:010d}.json",
                    ),
                    (
                        "submissions", SEC_SUBMISSIONS_URL.format(cik=cik),
                        f"CIK{cik:010d}-submissions.json",
                    ),
                ):
                    if ticker == probe_ticker and role == "companyfacts":
                        continue
                    payload, content, diagnostic = self.get_json_once(url)
                    diagnostics.append(diagnostic.to_dict())
                    try:
                        payload_cik = int(payload["cik"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise FinancialDataError(ProviderDiagnostic(
                            "sec_windows_pc", urlparse(url).hostname or "unknown",
                            _endpoint_name(url), "cik_missing_or_invalid",
                            error_type=type(exc).__name__,
                        )) from exc
                    if payload_cik != cik:
                        raise FinancialDataError(ProviderDiagnostic(
                            "sec_windows_pc", urlparse(url).hostname or "unknown",
                            _endpoint_name(url), "cik_mismatch",
                            error_type=f"expected_CIK{cik:010d}_received_CIK{payload_cik:010d}",
                        ))
                    if role == "submissions":
                        payload_tickers = {
                            normalize_sec_ticker(value)
                            for value in payload.get("tickers", [])
                            if isinstance(value, str)
                        }
                        if ticker not in payload_tickers:
                            raise FinancialDataError(ProviderDiagnostic(
                                "sec_windows_pc", "data.sec.gov", ticker,
                                "ticker_cik_identity_unverified",
                            ))
                    path = staging / filename
                    path.write_bytes(content)
                    artifacts.append(DownloadedSecArtifact(
                        role=role, ticker=ticker, cik=cik, path=filename,
                        source_url=url, sha256=_sha256_bytes(content),
                        byte_size=len(content),
                    ))

            obtained_at = self.now().astimezone(timezone.utc).replace(microsecond=0)
            manifest_artifacts = []
            for artifact in artifacts:
                item = build_manifest_artifact(
                    artifact.role, staging / artifact.path, cik=artifact.cik
                )
                manifest_artifacts.append(item)
            manifest = {
                "schema_version": 1,
                "provider": "sec_edgar_official_files",
                "obtained_at_utc": obtained_at.isoformat(),
                "artifacts": manifest_artifacts,
                "fixture": False,
            }
            manifest_path = staging / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            provider = SecFileProvider.from_manifest(manifest_path)
            periods_by_ticker = {
                ticker: provider.fetch_periods(
                    ticker, KNOWN_CIKS[ticker], as_of=as_of.astimezone(timezone.utc)
                )
                for ticker in normalized_tickers
            }
            raw_checks = {
                ticker: analyze_raw_sec_files(
                    ticker=ticker,
                    cik=KNOWN_CIKS[ticker],
                    companyfacts=json.loads(
                        (staging / f"CIK{KNOWN_CIKS[ticker]:010d}.json").read_text(
                            encoding="utf-8"
                        )
                    ),
                    submissions=json.loads(
                        (
                            staging
                            / f"CIK{KNOWN_CIKS[ticker]:010d}-submissions.json"
                        ).read_text(encoding="utf-8")
                    ),
                )
                for ticker in normalized_tickers
            }
            report = {
                "schema_version": 1,
                "status": "collected_and_offline_validated",
                "probe_request_count": 1,
                "total_request_count": len(diagnostics),
                "retry_count": 0,
                "as_of_utc": as_of.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
                "obtained_at_utc": obtained_at.isoformat(),
                "tickers": list(normalized_tickers),
                "artifact_count": len(artifacts),
                "period_count": sum(len(rows) for rows in periods_by_ticker.values()),
                "period_counts": {
                    ticker: len(rows) for ticker, rows in periods_by_ticker.items()
                },
                "diagnostics": diagnostics,
                "artifacts": [asdict(artifact) for artifact in artifacts],
                "raw_validation": raw_checks,
            }
            (staging / "collection_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            run_id = obtained_at.strftime("%Y%m%dT%H%M%SZ")
            final_dir = root / run_id
            if final_dir.exists():
                final_dir = root / f"{run_id}-{uuid.uuid4().hex[:8]}"
            staging.replace(final_dir)
            report["run_directory"] = str(final_dir)
            report["manifest"] = str(final_dir / "manifest.json")
            return report
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def _fact_rows(companyfacts: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    fact = ((companyfacts.get("facts") or {}).get("us-gaap") or {}).get(tag) or {}
    rows: list[dict[str, Any]] = []
    for values in (fact.get("units") or {}).values():
        if isinstance(values, list):
            rows.extend(row for row in values if isinstance(row, dict))
    return rows


def analyze_raw_sec_files(
    *, ticker: str, cik: int, companyfacts: dict[str, Any], submissions: dict[str, Any]
) -> dict[str, Any]:
    """Summarize identity, amendment, timing, coverage, and restatement evidence."""
    facts = companyfacts.get("facts") or {}
    gaap = facts.get("us-gaap") or {}
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    forms = [str(value) for value in recent.get("form") or []]
    acceptances = [value for value in recent.get("acceptanceDateTime") or [] if value]
    accessions = [value for value in recent.get("accessionNumber") or [] if value]

    restated_years: set[int] = set()
    eps_rows = _fact_rows(companyfacts, "EarningsPerShareDiluted")
    grouped: dict[tuple[str, str], set[float]] = {}
    for row in eps_rows:
        if str(row.get("form") or "") not in {"10-K", "10-K/A"}:
            continue
        key = (str(row.get("fy") or ""), str(row.get("end") or ""))
        try:
            value = round(float(row["val"]), 8)
        except (KeyError, TypeError, ValueError):
            continue
        grouped.setdefault(key, set()).add(value)
    for (year, _), values in grouped.items():
        if len(values) > 1 and year.isdigit():
            low, high = min(values), max(values)
            if abs(high - low) / max(abs(high), abs(low), 0.01) > 0.10:
                restated_years.add(int(year))

    metric_tags = {
        "revenue": (
            "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
            "SalesRevenueNet",
        ),
        "operating_income": ("OperatingIncomeLoss",),
        "net_income": ("NetIncomeLoss", "ProfitLoss"),
        "diluted_eps": ("EarningsPerShareDiluted",),
        "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
        "cfo": ("NetCashProvidedByUsedInOperatingActivities",),
        "capex": (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForAdditionsToPropertyPlantAndEquipment",
        ),
        "cash": (
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "CashAndCashEquivalentsAtCarryingValue",
        ),
        "debt": (
            "LongTermDebtAndFinanceLeaseObligations", "LongTermDebt",
            "LongTermDebtCurrent", "LongTermDebtNoncurrent",
        ),
    }
    coverage = {
        metric: any(tag in gaap for tag in tags)
        for metric, tags in metric_tags.items()
    }
    fiscal_years = sorted({
        int(row["fy"])
        for tag in gaap.values()
        if isinstance(tag, dict)
        for values in (tag.get("units") or {}).values()
        if isinstance(values, list)
        for row in values
        if isinstance(row, dict) and str(row.get("fy") or "").isdigit()
    })
    return {
        "ticker": ticker,
        "expected_cik": cik,
        "companyfacts_cik": int(companyfacts.get("cik") or 0),
        "submissions_cik": int(submissions.get("cik") or 0),
        "submissions_tickers": sorted(
            normalize_sec_ticker(value)
            for value in submissions.get("tickers", [])
            if isinstance(value, str)
        ),
        "metric_tag_coverage": coverage,
        "fiscal_year_min": min(fiscal_years) if fiscal_years else None,
        "fiscal_year_max": max(fiscal_years) if fiscal_years else None,
        "recent_accession_count": len(accessions),
        "acceptance_timestamp_count": len(acceptances),
        "amendment_form_count": sum(form.endswith("/A") for form in forms),
        "eps_restatement_or_split_adjustment_candidate_years": sorted(restated_years),
        "split_policy": (
            "later point-in-time filings may provide split-adjusted comparatives; "
            "the normalizer selects only filings available by the valuation timestamp"
        ),
    }

