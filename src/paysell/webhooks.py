"""Webhook verification.

Every delivery is signed with your key's **webhook secret** (shown once, next
to the API key, when the key is created) — not with the API key itself::

    X-Paysell-Timestamp: 1789000000
    X-Paysell-Signature: sha256=<hex HMAC-SHA256(secret, "{timestamp}.{raw_body}")>

Verify against the raw request bytes, before any JSON parsing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Mapping, Union

from ._errors import WebhookVerificationError
from ._models import WebhookEvent

SIGNATURE_HEADER = "X-Paysell-Signature"
TIMESTAMP_HEADER = "X-Paysell-Timestamp"
EVENT_ID_HEADER = "X-Paysell-Event-Id"
EVENT_TYPE_HEADER = "X-Paysell-Event"

DEFAULT_TOLERANCE = 300
"""Maximum distance, in seconds, between the signed timestamp and your clock."""

Body = Union[bytes, bytearray, memoryview, str]


def _as_bytes(body: Body) -> bytes:
    if isinstance(body, str):
        return body.encode("utf-8")
    return bytes(body)


def compute_signature(body: Body, timestamp: int | str, secret: str) -> str:
    """Return the ``X-Paysell-Signature`` value for ``body`` — ``"sha256=<hex>"``."""
    signed = str(timestamp).encode() + b"." + _as_bytes(body)
    return "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def verify_signature(
    body: Body,
    signature: str | None,
    timestamp: str | int | None,
    secret: str,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    now: float | None = None,
) -> bool:
    """Check a signature and its timestamp. Never raises.

    :param body: the raw request body, exactly as received.
    :param signature: the ``X-Paysell-Signature`` header.
    :param timestamp: the ``X-Paysell-Timestamp`` header.
    :param secret: the webhook secret of your API key.
    :param tolerance: allowed clock distance in seconds, either direction.
        ``0`` or less disables the check — don't, outside of tests.
    :param now: current unix time; defaults to ``time.time()``.
    """
    if not secret or not signature or timestamp is None:
        return False
    try:
        sent_at = int(str(timestamp).strip())
    except ValueError:
        return False
    if tolerance > 0:
        current = time.time() if now is None else now
        if abs(current - sent_at) > tolerance:
            return False
    expected = compute_signature(body, str(timestamp).strip(), secret)
    return hmac.compare_digest(expected.encode(), signature.strip().encode())


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    target = name.lower()
    getter = getattr(headers, "get", None)
    # Fast path for case-insensitive mappings: Starlette, aiohttp, Flask,
    # httpx and Django's request.headers all match names case-insensitively.
    if getter is not None:
        value = getter(name)
        if value is not None:
            return str(value)
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value)
    return None


def construct_event(
    body: Body,
    headers: Mapping[str, Any],
    secret: str,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> WebhookEvent:
    """Verify a delivery and parse it.

    :param body: the raw request body, exactly as received.
    :param headers: request headers (any mapping; names are matched case-insensitively).
    :param secret: the webhook secret of your API key.
    :raises WebhookVerificationError: answer the request with ``400``/``401``.
    """
    if not secret:
        raise WebhookVerificationError("webhook secret is empty")
    signature = _header(headers, SIGNATURE_HEADER)
    timestamp = _header(headers, TIMESTAMP_HEADER)
    if signature is None:
        raise WebhookVerificationError(
            f"missing {SIGNATURE_HEADER} header (is a webhook secret set for this key?)"
        )
    if timestamp is None:
        raise WebhookVerificationError(f"missing {TIMESTAMP_HEADER} header")
    if not verify_signature(body, signature, timestamp, secret, tolerance=tolerance):
        raise WebhookVerificationError("signature mismatch or timestamp outside tolerance")
    try:
        payload = json.loads(_as_bytes(body))
        if not isinstance(payload, dict):
            raise ValueError("body is not a JSON object")
        return WebhookEvent.from_dict(payload)
    except (ValueError, KeyError, TypeError) as exc:
        raise WebhookVerificationError(f"malformed webhook body: {exc}") from exc


def generate_test_headers(
    body: Body,
    secret: str,
    *,
    timestamp: int | None = None,
    event_id: str | None = None,
    event_type: str | None = None,
) -> dict[str, str]:
    """Headers for a correctly signed delivery — to test your own handler
    without a real payment."""
    ts = int(time.time()) if timestamp is None else timestamp
    headers = {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: str(ts),
        SIGNATURE_HEADER: compute_signature(body, ts, secret),
    }
    if event_id is not None:
        headers[EVENT_ID_HEADER] = event_id
    if event_type is not None:
        headers[EVENT_TYPE_HEADER] = event_type
    return headers


__all__ = [
    "DEFAULT_TOLERANCE",
    "EVENT_ID_HEADER",
    "EVENT_TYPE_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "compute_signature",
    "construct_event",
    "generate_test_headers",
    "verify_signature",
]
