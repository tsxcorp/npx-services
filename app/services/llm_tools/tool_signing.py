"""
HMAC-SHA256 sign/verify for NexClaude hard-action commit payloads.
Prevents users from tampering with preview payloads before confirming.
Signature has a short TTL (default 5 min) to prevent replay attacks.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from pydantic import BaseModel

from app.settings import settings


class SignedPayload(BaseModel):
    """A confirm payload bundled with HMAC signature + expiration."""
    payload: dict[str, Any]
    signature: str
    expires_at: int  # Unix timestamp


def _canonical_body(payload: dict[str, Any], expires_at: int) -> bytes:
    """
    Build a canonical JSON representation for hashing.
    Sort keys so sign/verify produce identical bytes regardless of dict order.
    """
    body = {**payload, "exp": expires_at}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(payload: dict[str, Any], ttl_seconds: int = 300) -> SignedPayload:
    """
    Sign a confirm payload. Default TTL 5 min.
    The caller includes tenant_id, user_id, and the action data in payload.
    """
    expires_at = int(time.time()) + ttl_seconds
    body = _canonical_body(payload, expires_at)
    sig_bytes = hmac.new(
        settings.nexclaude_signing_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    return SignedPayload(
        payload=payload,
        signature=base64.urlsafe_b64encode(sig_bytes).decode("ascii"),
        expires_at=expires_at,
    )


def verify_payload(signed: SignedPayload) -> bool:
    """
    Verify HMAC + expiration. Returns True on valid, False on invalid/expired.
    Uses constant-time comparison to prevent timing attacks.
    """
    if time.time() > signed.expires_at:
        return False
    body = _canonical_body(signed.payload, signed.expires_at)
    expected = hmac.new(
        settings.nexclaude_signing_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    try:
        actual = base64.urlsafe_b64decode(signed.signature.encode("ascii"))
    except Exception:
        return False
    return hmac.compare_digest(expected, actual)
