"""Offline parser registry for Same-Date evidence replay.

Only deterministic, network-free parsers registered in this module may turn
provider-native bytes into normalized contract records.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class EvidenceParserError(ValueError):
    """Raised when a parser is unknown or provider bytes cannot be replayed."""


RAW_SHA256_SENTINEL = "$PROVIDER_RAW_SHA256"
JSON_RECORDS_PARSER_ID = "stocklens-provider-json-records"
JSON_RECORDS_PARSER_VERSION = "1.0.0"


@dataclass(frozen=True)
class EvidenceParserSpec:
    parser_id: str
    version: str
    code_sha256: str
    parse: Callable[[bytes, str, str], Any]


def _replace_raw_hash_sentinel(value: Any, raw_sha256: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_raw_hash_sentinel(item, raw_sha256)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_raw_hash_sentinel(item, raw_sha256) for item in value]
    return raw_sha256 if value == RAW_SHA256_SENTINEL else value


def _parse_json_records(raw: bytes, expected_role: str, expected_provider: str) -> Any:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceParserError("provider-native bytes are not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise EvidenceParserError("provider-native JSON root must be an object")
    if payload.get("provider") != expected_provider:
        raise EvidenceParserError("provider-native provider does not match normalized metadata")
    if payload.get("role") != expected_role:
        raise EvidenceParserError("provider-native role does not match contract role")
    records = payload.get("records")
    if not isinstance(records, (list, dict)):
        raise EvidenceParserError("provider-native records must be an array or object")
    raw_sha256 = hashlib.sha256(raw).hexdigest().upper()
    return _replace_raw_hash_sentinel(records, raw_sha256)


def _module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest().upper()


def get_evidence_parser(
    parser_id: str,
    version: str,
    expected_code_sha256: str,
) -> EvidenceParserSpec:
    """Resolve one allowlisted parser and bind it to the installed code bytes."""

    if (parser_id, version) != (JSON_RECORDS_PARSER_ID, JSON_RECORDS_PARSER_VERSION):
        raise EvidenceParserError("provider-native parser is not registered")
    actual_code_sha256 = _module_sha256()
    if expected_code_sha256.upper() != actual_code_sha256:
        raise EvidenceParserError("provider-native parser code SHA-256 mismatch")
    return EvidenceParserSpec(
        parser_id=JSON_RECORDS_PARSER_ID,
        version=JSON_RECORDS_PARSER_VERSION,
        code_sha256=actual_code_sha256,
        parse=_parse_json_records,
    )


def registered_parser_code_sha256() -> str:
    """Return the installed allowlisted parser module hash for candidate creation."""

    return _module_sha256()
