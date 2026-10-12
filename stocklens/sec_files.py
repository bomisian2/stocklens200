from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .financials import (
    SEC_COMPANY_FACTS_BULK_URL,
    SEC_COMPANY_FACTS_URL,
    SEC_COMPANY_TICKERS_URL,
    SEC_SUBMISSIONS_BULK_URL,
    SEC_SUBMISSIONS_URL,
    FinancialDataError,
    FinancialPeriod,
    ProviderDiagnostic,
    build_cik_map,
    extract_financial_periods,
)


MANIFEST_SCHEMA_VERSION = 1
NORMALIZATION_VERSION = "sec-gaap-v1"
OFFICIAL_SEC_HOSTS = {"www.sec.gov", "data.sec.gov"}
SUPPORTED_ROLES = {"companyfacts", "submissions", "ticker_map"}


@dataclass(frozen=True)
class SecFileArtifact:
    role: str
    path: Path
    source_url: str
    sha256: str
    byte_size: int
    cik: int | None = None

    def public_dict(self, base_dir: Path) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.path.relative_to(base_dir).as_posix(),
            "source_url": self.source_url,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "cik": self.cik,
        }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_periods_sha256(periods: Iterable[FinancialPeriod]) -> str:
    rows = sorted(
        (period.to_dict() for period in periods),
        key=lambda row: (
            str(row.get("ticker") or ""),
            str(row.get("period_type") or ""),
            str(row.get("period_end") or ""),
            str(row.get("available_at_utc") or ""),
        ),
    )
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _validate_official_url(url: str, role: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in OFFICIAL_SEC_HOSTS:
        raise ValueError(f"{role} source_url must be an official HTTPS SEC URL")


def _resolve_local_file(base_dir: Path, relative: str) -> Path:
    candidate = (base_dir / relative).resolve()
    base = base_dir.resolve()
    if not candidate.is_relative_to(base):
        raise ValueError("artifact path must stay inside the manifest directory")
    if not candidate.is_file():
        raise ValueError(f"artifact file does not exist: {relative}")
    return candidate


class SecFileProvider:
    """Offline-only provider for user-supplied official SEC JSON or bulk ZIP files."""

    name = "sec_offline_files"

    def __init__(
        self,
        *,
        manifest_path: Path,
        obtained_at_utc: datetime,
        artifacts: list[SecFileArtifact],
    ) -> None:
        self.manifest_path = manifest_path
        self.base_dir = manifest_path.parent.resolve()
        self.obtained_at_utc = obtained_at_utc
        self.artifacts = artifacts

    @classmethod
    def from_manifest(cls, manifest_path: str | Path) -> "SecFileProvider":
        path = Path(manifest_path).resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("SEC import manifest is unreadable") from exc
        if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported SEC import manifest schema")
        if payload.get("provider") != "sec_edgar_official_files":
            raise ValueError("manifest provider must be sec_edgar_official_files")
        if payload.get("fixture") is not False:
            raise ValueError("operational SEC import manifest must declare fixture=false")
        obtained = _parse_utc(str(payload.get("obtained_at_utc") or ""), "obtained_at_utc")
        if obtained > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("obtained_at_utc cannot be in the future")
        base_dir = path.parent.resolve()
        artifacts: list[SecFileArtifact] = []
        for raw in payload.get("artifacts") or []:
            if not isinstance(raw, dict) or raw.get("role") not in SUPPORTED_ROLES:
                raise ValueError("manifest contains an unsupported artifact role")
            role = str(raw["role"])
            source_url = str(raw.get("source_url") or "")
            _validate_official_url(source_url, role)
            artifact_path = _resolve_local_file(base_dir, str(raw.get("path") or ""))
            actual_hash = sha256_file(artifact_path)
            expected_hash = str(raw.get("sha256") or "").lower()
            if len(expected_hash) != 64 or actual_hash != expected_hash:
                raise ValueError(f"{role} SHA-256 mismatch")
            cik = raw.get("cik")
            artifacts.append(SecFileArtifact(
                role=role,
                path=artifact_path,
                source_url=source_url,
                sha256=actual_hash,
                byte_size=artifact_path.stat().st_size,
                cik=int(cik) if cik is not None else None,
            ))
        if not any(item.role == "companyfacts" for item in artifacts):
            raise ValueError("manifest requires at least one companyfacts artifact")
        return cls(manifest_path=path, obtained_at_utc=obtained, artifacts=artifacts)

    def _artifact(self, role: str, cik: int | None = None) -> SecFileArtifact | None:
        candidates = [item for item in self.artifacts if item.role == role]
        exact = [item for item in candidates if item.cik == cik]
        if exact:
            return exact[0]
        shared = [item for item in candidates if item.cik is None]
        return shared[0] if shared else None

    @staticmethod
    def _read_json(artifact: SecFileArtifact, cik: int | None = None) -> dict[str, Any]:
        try:
            if artifact.path.suffix.lower() == ".zip":
                if cik is None and artifact.role == "ticker_map":
                    raise ValueError("ticker map ZIP is unsupported")
                member = f"CIK{int(cik):010d}.json"
                with zipfile.ZipFile(artifact.path) as archive:
                    with archive.open(member) as handle:
                        payload = json.load(handle)
            else:
                payload = json.loads(artifact.path.read_text(encoding="utf-8"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise FinancialDataError(ProviderDiagnostic(
                provider="sec_offline_files",
                domain="local",
                endpoint=artifact.path.name,
                category="offline_artifact",
                error_type=type(exc).__name__,
            )) from exc
        if not isinstance(payload, dict):
            raise FinancialDataError(ProviderDiagnostic(
                "sec_offline_files", "local", artifact.path.name, "offline_schema"
            ))
        return payload

    def cik_map(self) -> dict[str, int]:
        artifact = self._artifact("ticker_map")
        if not artifact:
            return {}
        return build_cik_map(self._read_json(artifact))

    def fetch_periods(self, ticker: str, cik: int, *, as_of: datetime) -> list[FinancialPeriod]:
        facts_artifact = self._artifact("companyfacts", cik)
        if facts_artifact is None:
            raise FinancialDataError(ProviderDiagnostic(
                "sec_offline_files", "local", "companyfacts", "artifact_missing"
            ))
        companyfacts = self._read_json(facts_artifact, cik)
        payload_cik = companyfacts.get("cik")
        if payload_cik is not None and int(payload_cik) != cik:
            raise FinancialDataError(ProviderDiagnostic(
                "sec_offline_files", "local", facts_artifact.path.name, "cik_mismatch"
            ))
        submissions_artifact = self._artifact("submissions", cik)
        submissions: dict[str, Any] = {}
        hashes = [facts_artifact.sha256]
        if submissions_artifact:
            submissions = self._read_json(submissions_artifact, cik)
            submission_cik = submissions.get("cik")
            if submission_cik is not None and int(submission_cik) != cik:
                raise FinancialDataError(ProviderDiagnostic(
                    "sec_offline_files", "local", submissions_artifact.path.name, "cik_mismatch"
                ))
            hashes.append(submissions_artifact.sha256)
        combined_hash = hashlib.sha256(":".join(sorted(hashes)).encode("ascii")).hexdigest()
        return extract_financial_periods(
            ticker=ticker,
            cik=cik,
            companyfacts=companyfacts,
            submissions=submissions,
            as_of=as_of,
            source="sec_edgar_offline_import",
            source_artifact_sha256=combined_hash,
            source_obtained_at_utc=self.obtained_at_utc.isoformat(),
        )

    def provenance(self, *, periods: Iterable[FinancialPeriod]) -> dict[str, Any]:
        period_list = list(periods)
        return {
            "schema_version": 1,
            "provider": "sec_edgar_official_files",
            "manifest": self.manifest_path.name,
            "obtained_at_utc": self.obtained_at_utc.isoformat(),
            "normalization_version": NORMALIZATION_VERSION,
            "artifacts": [item.public_dict(self.base_dir) for item in self.artifacts],
            "normalized_period_count": len(period_list),
            "normalized_sha256": normalized_periods_sha256(period_list),
            "fixture": False,
        }


def canonical_source_url(role: str, path: Path, cik: int | None) -> str:
    if role == "ticker_map":
        return SEC_COMPANY_TICKERS_URL
    if role == "companyfacts":
        return SEC_COMPANY_FACTS_BULK_URL if path.suffix.lower() == ".zip" else SEC_COMPANY_FACTS_URL.format(cik=int(cik))
    if role == "submissions":
        return SEC_SUBMISSIONS_BULK_URL if path.suffix.lower() == ".zip" else SEC_SUBMISSIONS_URL.format(cik=int(cik))
    raise ValueError(f"unsupported role: {role}")


def detect_json_cik(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload["cik"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot determine CIK from {path.name}") from exc


def build_manifest_artifact(role: str, path: str | Path, *, cik: int | None = None) -> dict[str, Any]:
    artifact_path = Path(path).resolve()
    if role not in SUPPORTED_ROLES:
        raise ValueError(f"unsupported role: {role}")
    if artifact_path.suffix.lower() != ".zip" and role != "ticker_map" and cik is None:
        cik = detect_json_cik(artifact_path)
    return {
        "role": role,
        "path": artifact_path.name,
        "source_url": canonical_source_url(role, artifact_path, cik),
        "sha256": sha256_file(artifact_path),
        "cik": cik,
    }
