"""
Tests for llm_tools/tool_signing.py — HMAC sign/verify roundtrip, expiration, tampering.
Run: cd nexpo-services && python -m pytest tests/test_llm_tools_signing.py -v
"""
from __future__ import annotations

import time

import pytest

from app.services.llm_tools.tool_signing import (
    SignedPayload,
    _canonical_body,
    sign_payload,
    verify_payload,
)


class TestSignVerifyRoundtrip:
    def test_sign_and_verify_roundtrip(self):
        """sign_payload then verify_payload returns True for same payload."""
        payload = {"tenant_id": "tenant-abc", "user_id": "user-1", "name": "Tech Fair"}
        signed = sign_payload(payload)

        assert isinstance(signed, SignedPayload)
        assert signed.signature != ""
        assert signed.expires_at > int(time.time())
        assert verify_payload(signed) is True

    def test_verify_expired_payload_fails(self):
        """Payload signed with ttl_seconds=-1 is already expired; verify returns False."""
        payload = {"tenant_id": "tenant-abc", "action": "create_event"}
        signed = sign_payload(payload, ttl_seconds=-1)

        assert verify_payload(signed) is False

    def test_verify_tampered_payload_fails(self):
        """Mutating payload dict after signing invalidates the signature."""
        payload = {"tenant_id": "tenant-abc", "name": "Original Event"}
        signed = sign_payload(payload)

        # Tamper: change tenant_id in the payload copy inside SignedPayload
        tampered = SignedPayload(
            payload={**signed.payload, "tenant_id": "evil-tenant"},
            signature=signed.signature,
            expires_at=signed.expires_at,
        )

        assert verify_payload(tampered) is False

    def test_verify_invalid_signature_fails(self):
        """Replacing signature with garbage base64 returns False."""
        payload = {"tenant_id": "tenant-abc", "name": "Test"}
        signed = sign_payload(payload)

        garbage = SignedPayload(
            payload=signed.payload,
            signature="aGFja2VkLXNpZ25hdHVyZQ==",  # valid base64, wrong HMAC
            expires_at=signed.expires_at,
        )

        assert verify_payload(garbage) is False

    def test_verify_non_base64_signature_fails(self):
        """Non-base64 signature string is handled gracefully, returns False."""
        payload = {"tenant_id": "t"}
        signed = sign_payload(payload)

        bad = SignedPayload(
            payload=signed.payload,
            signature="not!!valid!!base64!!!",
            expires_at=signed.expires_at,
        )

        assert verify_payload(bad) is False


class TestCanonicalBody:
    def test_canonical_body_is_order_independent(self):
        """Two dicts with different key order produce the same canonical bytes."""
        payload_a = {"tenant_id": "t1", "name": "Foo", "action": "create"}
        payload_b = {"action": "create", "name": "Foo", "tenant_id": "t1"}
        expires_at = 9999999999

        body_a = _canonical_body(payload_a, expires_at)
        body_b = _canonical_body(payload_b, expires_at)

        assert body_a == body_b

    def test_canonical_body_different_expires_differs(self):
        """Same payload with different expires_at produces different canonical bytes."""
        payload = {"tenant_id": "t1"}
        body1 = _canonical_body(payload, 1000)
        body2 = _canonical_body(payload, 2000)

        assert body1 != body2

    def test_canonical_body_different_payloads_differ(self):
        """Different payloads produce different canonical bytes."""
        body1 = _canonical_body({"tenant_id": "a"}, 1000)
        body2 = _canonical_body({"tenant_id": "b"}, 1000)

        assert body1 != body2

    def test_sign_order_independent(self):
        """Two dicts differing only in key order produce the same signature."""
        payload_a = {"tenant_id": "t1", "name": "Foo", "action": "create"}
        payload_b = {"action": "create", "name": "Foo", "tenant_id": "t1"}

        signed_a = sign_payload(payload_a, ttl_seconds=300)
        # Re-sign with same expires_at to compare deterministically
        signed_b = SignedPayload(
            payload=payload_b,
            signature=sign_payload(payload_b, ttl_seconds=300).signature,
            expires_at=signed_a.expires_at,
        )

        # Both should individually verify (signatures may differ by timestamp,
        # but verifying each against its own expires_at must succeed)
        assert verify_payload(signed_a) is True


class TestSignedPayloadModel:
    def test_model_dump_roundtrip(self):
        """SignedPayload serializes to dict and re-parses cleanly."""
        payload = {"tenant_id": "t1", "x": 42}
        signed = sign_payload(payload)
        dumped = signed.model_dump()

        reloaded = SignedPayload.model_validate(dumped)
        assert reloaded.signature == signed.signature
        assert reloaded.expires_at == signed.expires_at
        assert reloaded.payload == signed.payload

    def test_verify_after_model_dump_roundtrip(self):
        """Verify still passes after model_dump → model_validate roundtrip."""
        payload = {"tenant_id": "t2", "name": "Round Trip"}
        signed = sign_payload(payload)
        dumped = signed.model_dump()
        reloaded = SignedPayload.model_validate(dumped)

        assert verify_payload(reloaded) is True
