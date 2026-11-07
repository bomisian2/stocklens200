"""Public-key verification for independent Same-Date approval receipts.

Production trust is fail-closed: a QA public key must be committed to the
trusted-key registry after independent review. No private key belongs here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any


class ApprovalSignatureError(ValueError):
    """Raised when a receipt is unsigned, untrusted, revoked, or altered."""


SIGNATURE_ALGORITHM = "rsa-pkcs1v15-sha256"
SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300D060960864801650304020105000420")


@dataclass(frozen=True)
class TrustedApprovalKey:
    key_id: str
    approved_by: str
    modulus_hex: str
    exponent: int
    allowed_scopes: frozenset[str]
    revoked: bool = False


# Intentionally empty until an independently controlled QA public key is
# reviewed and committed. Tests inject a synthetic public key at runtime only.
TRUSTED_APPROVAL_KEYS: dict[str, TrustedApprovalKey] = {}


def canonical_approval_payload(receipt: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _key_fingerprint(key: TrustedApprovalKey) -> str:
    material = {
        "algorithm": SIGNATURE_ALGORITHM,
        "exponent": key.exponent,
        "key_id": key.key_id,
        "modulus_hex": key.modulus_hex.upper(),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest().upper()


def _verify_rsa_pkcs1v15_sha256(
    signature: bytes,
    payload: bytes,
    key: TrustedApprovalKey,
) -> None:
    try:
        modulus = int(key.modulus_hex, 16)
    except ValueError as exc:
        raise ApprovalSignatureError("trusted QA public key is malformed") from exc
    if modulus <= 0 or key.exponent <= 1:
        raise ApprovalSignatureError("trusted QA public key is malformed")
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        raise ApprovalSignatureError("approval receipt signature length is invalid")
    decoded = pow(int.from_bytes(signature, "big"), key.exponent, modulus).to_bytes(size, "big")
    digest_info = SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(payload).digest()
    padding_length = size - len(digest_info) - 3
    if padding_length < 8:
        raise ApprovalSignatureError("trusted QA public key is too short")
    expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
    if not hmac.compare_digest(decoded, expected):
        raise ApprovalSignatureError("approval receipt signature verification failed")


def verify_approval_receipt_signature(
    receipt: dict[str, Any],
    *,
    expected_scope: str,
) -> dict[str, str]:
    signature_value = receipt.get("signature")
    if not isinstance(signature_value, dict):
        raise ApprovalSignatureError("approval receipt signature is missing")
    if set(signature_value) != {"algorithm", "key_id", "value_base64"}:
        raise ApprovalSignatureError("approval receipt signature fields are incomplete")
    if signature_value.get("algorithm") != SIGNATURE_ALGORITHM:
        raise ApprovalSignatureError("approval receipt signature algorithm is not approved")
    key_id = signature_value.get("key_id")
    if not isinstance(key_id, str) or not key_id:
        raise ApprovalSignatureError("approval receipt signature key_id is missing")
    key = TRUSTED_APPROVAL_KEYS.get(key_id)
    if key is None:
        raise ApprovalSignatureError("approval receipt QA public key is not trusted")
    if key.revoked:
        raise ApprovalSignatureError("approval receipt QA public key is revoked")
    if expected_scope not in key.allowed_scopes:
        raise ApprovalSignatureError("approval receipt QA key is not approved for this scope")
    if receipt.get("approved_by") != key.approved_by:
        raise ApprovalSignatureError("approval receipt signer identity does not match QA key")
    encoded = signature_value.get("value_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ApprovalSignatureError("approval receipt signature value is missing")
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ApprovalSignatureError("approval receipt signature is not valid base64") from exc
    _verify_rsa_pkcs1v15_sha256(signature, canonical_approval_payload(receipt), key)
    return {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": key.key_id,
        "key_fingerprint_sha256": _key_fingerprint(key),
    }
