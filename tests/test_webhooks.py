from __future__ import annotations

import json
from decimal import Decimal

import pytest

from paysell import (
    PaymentCredited,
    PaymentRejected,
    WebhookVerificationError,
    construct_event,
    verify_signature,
)
from paysell.webhooks import compute_signature, generate_test_headers

SECRET = "whsec_test_secret"
TIMESTAMP = 1789000000
# Byte-for-byte what the Paysell backend sends, and the signature its own
# signing code produces for it (services/webhook_delivery.sign).
BODY = (
    b'{"event_id":"99f74f58-efbb-4af1-b0a3-76b0073f9e6b","type":"payment.credited","data":'
    b'{"invoice_id":"12c22c1a-a496-4c1e-abe3-72661ef8706e","order_id":"order-1042","asset":"USDT_TON",'
    b'"amount":"5000000","credited":"4905000","fee":"95000","status":"paid","tx_hash":"97a1f0",'
    b'"paid_minor":"5000000"}}'
)
SIGNATURE = "sha256=7c4896162f38aaaa40f0ae522d835bdbb2853a80b98e6940fceef78061b5416a"


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr("paysell.webhooks.time.time", lambda: TIMESTAMP + 10)


def test_signature_matches_backend_vector():
    assert compute_signature(BODY, TIMESTAMP, SECRET) == SIGNATURE
    assert verify_signature(BODY, SIGNATURE, str(TIMESTAMP), SECRET)


def test_construct_credited_event_with_lowercase_headers():
    headers = {"x-paysell-signature": SIGNATURE, "x-paysell-timestamp": str(TIMESTAMP)}
    event = construct_event(BODY, headers, SECRET)
    assert event.event_id == "99f74f58-efbb-4af1-b0a3-76b0073f9e6b"
    assert event.is_payment_credited
    data = event.data
    assert isinstance(data, PaymentCredited)
    assert data.is_paid
    assert data.order_id == "order-1042"
    assert data.credited_int == 4_905_000
    assert data.credited_decimal == Decimal("4.905")
    assert data.fee_decimal == Decimal("0.095")
    assert data.paid_decimal == Decimal("5")
    assert not data.asset_mismatch


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b, h: (b.replace(b"4905000", b"9905000"), h),  # body tampered
        lambda b, h: (b, {**h, "X-Paysell-Timestamp": str(TIMESTAMP + 1)}),  # timestamp edited
        lambda b, h: (b, {**h, "X-Paysell-Signature": SIGNATURE.upper()}),
        lambda b, h: (b, {"X-Paysell-Timestamp": str(TIMESTAMP)}),  # no signature
        lambda b, h: (b, {"X-Paysell-Signature": SIGNATURE}),  # no timestamp
        lambda b, h: (b, {**h, "X-Paysell-Timestamp": "soon"}),
    ],
)
def test_rejects_tampering(mutate):
    body, headers = mutate(
        BODY, {"X-Paysell-Signature": SIGNATURE, "X-Paysell-Timestamp": str(TIMESTAMP)}
    )
    with pytest.raises(WebhookVerificationError):
        construct_event(body, headers, SECRET)


def test_rejects_wrong_secret_eg_api_key():
    headers = {"X-Paysell-Signature": SIGNATURE, "X-Paysell-Timestamp": str(TIMESTAMP)}
    with pytest.raises(WebhookVerificationError):
        construct_event(BODY, headers, "sk_live_the_api_key")
    with pytest.raises(WebhookVerificationError):
        construct_event(BODY, headers, "")


def test_replay_window(monkeypatch):
    monkeypatch.setattr("paysell.webhooks.time.time", lambda: TIMESTAMP + 301)
    assert not verify_signature(BODY, SIGNATURE, TIMESTAMP, SECRET)
    assert verify_signature(BODY, SIGNATURE, TIMESTAMP, SECRET, tolerance=600)
    monkeypatch.setattr("paysell.webhooks.time.time", lambda: TIMESTAMP - 301)
    assert not verify_signature(BODY, SIGNATURE, TIMESTAMP, SECRET)


def test_rejected_event_and_str_body():
    body = json.dumps(
        {
            "event_id": "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d",
            "type": "payment.rejected",
            "data": {
                "invoice_id": "i",
                "order_id": "o",
                "asset": "TON",
                "amount": "1500000000",
                "tx_hash": "h",
                "reason": "no order",
            },
        }
    )
    event = construct_event(body, generate_test_headers(body, SECRET, timestamp=TIMESTAMP), SECRET)
    assert isinstance(event.data, PaymentRejected)
    assert event.data.reason == "no order"
    assert event.data.amount_decimal == Decimal("1.5")


def test_asset_mismatch_and_unknown_type():
    body = json.dumps(
        {
            "event_id": "e",
            "type": "payment.credited",
            "data": {
                "asset": "TON",
                "status": "pending",
                "asset_mismatch": True,
                "invoice_asset": "USDT_TON",
                "paid_minor": "0",
            },
        }
    ).encode()
    event = construct_event(body, generate_test_headers(body, SECRET, timestamp=TIMESTAMP), SECRET)
    assert event.data.asset_mismatch and not event.data.is_paid
    assert event.data.invoice_asset == "USDT_TON"

    future = json.dumps({"event_id": "e2", "type": "invoice.future", "data": {"x": 1}}).encode()
    event2 = construct_event(
        future, generate_test_headers(future, SECRET, timestamp=TIMESTAMP), SECRET
    )
    assert event2.data == {"x": 1}


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1,2]",
        b'{"type":"payment.credited"}',
        b'{"event_id":"e","type":"payment.credited","data":[1]}',
    ],
)
def test_malformed_but_signed_body(body):
    with pytest.raises(WebhookVerificationError):
        construct_event(body, generate_test_headers(body, SECRET, timestamp=TIMESTAMP), SECRET)
