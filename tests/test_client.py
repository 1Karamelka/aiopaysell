from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from paysell import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    Paysell,
    PaysellError,
    RateLimitError,
    ServiceUnavailableError,
)
from tests.conftest import INVOICE, respond


def test_create_invoice_request_and_parsing(make_client):
    client, rec = make_client(respond(201))
    invoice = client.create_invoice(
        "USDT_TON", Decimal("5.00"), order_id="order-1042", ttl_minutes=30
    )

    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://paysell.me/api/merchant/v1/invoices"
    assert req.headers["Authorization"] == "Bearer sk_live_test"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["User-Agent"].startswith("paysell-python/")
    body = rec.json()
    assert body["asset"] == "USDT_TON"
    assert body["amount"] == "5"
    assert body["order_id"] == "order-1042"
    assert body["ttl_minutes"] == 30
    assert len(body["idempotency_key"]) == 32
    assert "description" not in body

    assert invoice.invoice_id == INVOICE["invoice_id"]
    assert invoice.payment_url.startswith("https://paysell.me/pay/")
    assert invoice.amount_minor_int == 5_000_000
    assert invoice.remaining_minor == 5_000_000
    assert invoice.expires_at == datetime(2026, 9, 6, 17, 20, 55, tzinfo=timezone.utc)
    assert invoice.created_at.microsecond == 123456
    assert invoice.is_open and not invoice.is_paid and not invoice.is_final
    assert invoice.raw["order_id"] == "order-1042"


def test_explicit_idempotency_key_is_sent(make_client):
    client, rec = make_client(respond(201))
    client.create_invoice("TON", "1.5", idempotency_key="order-1042-v1")
    assert rec.json()["idempotency_key"] == "order-1042-v1"
    assert rec.json()["amount"] == "1.5"


@pytest.mark.parametrize(
    ("amount", "error"),
    [
        (1.5, TypeError),
        ("1.1234567", ValueError),
        (0, ValueError),
        ("-1", ValueError),
        ("abc", ValueError),
    ],
)
def test_bad_amounts_fail_before_any_request(make_client, amount, error):
    client, rec = make_client()
    with pytest.raises(error):
        client.create_invoice("USDT_TON", amount)
    assert rec.requests == []


def test_bad_ttl_and_asset(make_client):
    client, rec = make_client()
    with pytest.raises(ValueError):
        client.create_invoice("TON", "1", ttl_minutes=0)
    with pytest.raises(ValueError):
        client.create_invoice("BTC", "1")
    assert rec.requests == []


def test_get_and_cancel_paths_are_escaped(make_client):
    client, rec = make_client(respond(), respond(body={**INVOICE, "status": "cancelled"}))
    invoice = client.get_invoice("../shops")
    assert rec.requests[0].url.raw_path == b"/api/merchant/v1/invoices/..%2Fshops"
    cancelled = client.cancel_invoice(invoice)
    assert rec.requests[1].method == "POST"
    assert rec.requests[1].url.path.endswith(f"/invoices/{INVOICE['invoice_id']}/cancel")
    assert cancelled.is_final


def test_public_invoice_sends_no_key(make_client):
    public = {
        k: v for k, v in INVOICE.items() if k not in ("order_id", "created_at", "payment_url")
    }
    public |= {"shop_name": "Shop", "shop_url": "https://shop.example"}
    client, rec = make_client(respond(body=public))
    result = client.get_public_invoice(INVOICE["invoice_id"])
    assert "Authorization" not in rec.requests[0].headers
    assert rec.requests[0].url.path.endswith(f"/public/invoices/{INVOICE['invoice_id']}")
    assert result.shop_name == "Shop"


@pytest.mark.parametrize(
    ("status", "cls", "code"),
    [
        (401, AuthenticationError, "invalid_api_key"),
        (404, NotFoundError, "not_found"),
        (409, ConflictError, "conflict"),
        (422, InvalidRequestError, "invalid_input"),
    ],
)
def test_error_mapping(make_client, status, cls, code):
    body = {"detail": {"code": code, "message": "nope", "details": ["a", "b"]}}
    client, _ = make_client(respond(status, body))
    with pytest.raises(cls) as info:
        client.get_invoice("x")
    assert info.value.status_code == status
    assert info.value.code == code
    assert info.value.message == "nope"
    assert info.value.details == ["a", "b"]
    assert "nope" in str(info.value)


def test_validation_error_list(make_client):
    body = {
        "detail": [
            {
                "type": "string_type",
                "loc": ["body", "amount"],
                "msg": "Input should be a valid string",
            }
        ]
    }
    client, _ = make_client(respond(422, body))
    with pytest.raises(InvalidRequestError) as info:
        client.get_invoice("x")
    assert info.value.code == "validation_error"
    assert info.value.message == "amount: Input should be a valid string"
    assert info.value.errors == body["detail"]


def test_string_detail_and_non_json_errors(make_client):
    client, _ = make_client(
        respond(404, {"detail": "Invoice not found"}),
        httpx.Response(418, text="teapot"),
    )
    with pytest.raises(NotFoundError) as info:
        client.get_public_invoice("x")
    assert (info.value.code, info.value.message) == ("not_found", "Invoice not found")
    with pytest.raises(APIError) as info2:
        client.get_invoice("x")
    assert info2.value.status_code == 418 and info2.value.body == "teapot"


def test_rate_limit_is_retried_after_retry_after(make_client, no_sleep):
    limited = respond(
        429, {"detail": {"code": "too_many_requests", "message": "slow"}}, {"Retry-After": "7"}
    )
    client, rec = make_client(limited, respond())
    client.get_invoice("x")
    assert len(rec.requests) == 2
    assert no_sleep == [7.0]


def test_rate_limit_gives_up_after_retries(make_client):
    limited = lambda: respond(
        429, {"detail": {"code": "too_many_requests", "message": "slow"}}, {"Retry-After": "1"}
    )
    client, rec = make_client(limited(), limited(), limited())
    with pytest.raises(RateLimitError) as info:
        client.get_invoice("x")
    assert info.value.retry_after == 1.0
    assert len(rec.requests) == 3


def test_long_retry_after_is_not_waited(make_client, no_sleep):
    client, rec = make_client(
        respond(
            429, {"detail": {"code": "too_many_requests", "message": "x"}}, {"Retry-After": "3600"}
        )
    )
    with pytest.raises(RateLimitError):
        client.create_invoice("TON", "1")
    assert len(rec.requests) == 1 and no_sleep == []


def test_502_on_create_is_retried_with_same_idempotency_key(make_client):
    bad = respond(502, {"detail": {"code": "cbc_unreachable", "message": "down"}})
    client, rec = make_client(bad, httpx.ConnectError("boom"), respond(201))
    client.create_invoice("TON", "1")
    keys = {rec.json(i)["idempotency_key"] for i in range(3)}
    assert len(rec.requests) == 3 and len(keys) == 1


def test_cancel_is_not_replayed_after_lost_answer(make_client):
    client, rec = make_client(httpx.ReadTimeout("slow"), respond())
    with pytest.raises(APITimeoutError):
        client.cancel_invoice("x")
    assert len(rec.requests) == 1


def test_cancel_502_not_retried(make_client):
    client, rec = make_client(
        respond(502, {"detail": {"code": "cbc_unreachable", "message": "down"}})
    )
    with pytest.raises(ServiceUnavailableError):
        client.cancel_invoice("x")
    assert len(rec.requests) == 1


def test_max_retries_zero(make_client):
    client, rec = make_client(httpx.ConnectError("boom"), max_retries=0)
    with pytest.raises(APIConnectionError):
        client.get_invoice("x")
    assert len(rec.requests) == 1


def test_malformed_success_body(make_client):
    client, _ = make_client(respond(200, {"invoice_id": "x"}), httpx.Response(200, text="<html>"))
    with pytest.raises(APIError) as info:
        client.get_invoice("x")
    assert info.value.code == "invalid_response"
    with pytest.raises(APIError):
        client.get_invoice("x")


def test_missing_key(monkeypatch):
    monkeypatch.delenv("PAYSELL_API_KEY", raising=False)
    client = Paysell(http_client=httpx.Client(transport=httpx.MockTransport(lambda r: respond())))
    with pytest.raises(PaysellError, match="no API key"):
        client.get_invoice("x")
    # The public endpoint works without one.
    client.get_public_invoice("x")


def test_key_from_env_and_repr_hides_key(monkeypatch):
    monkeypatch.setenv("PAYSELL_API_KEY", "sk_live_from_env")
    client = Paysell()
    assert client._api_key == "sk_live_from_env"
    assert "sk_live" not in repr(client)
    client.close()


def test_wait_for_invoice(make_client, no_sleep):
    client, rec = make_client(
        respond(),
        respond(body={**INVOICE, "status": "underpaid", "paid_minor": "1000000"}),
        respond(body={**INVOICE, "status": "paid", "paid_minor": "5000000"}),
    )
    invoice = client.wait_for_invoice("x", interval=2)
    assert invoice.is_paid and len(rec.requests) == 3 and no_sleep == [2, 2]


def test_wait_for_invoice_timeout(make_client):
    client, _ = make_client(respond())
    with pytest.raises(TimeoutError):
        client.wait_for_invoice("x", interval=5, timeout=1)


def test_context_manager_closes_own_client():
    with Paysell("sk_live_x") as client:
        pass
    assert client._client.is_closed


async def test_async_client(make_async_client, no_sleep):
    client, rec = make_async_client(
        respond(
            429, {"detail": {"code": "too_many_requests", "message": "x"}}, {"Retry-After": "2"}
        ),
        respond(201),
        respond(body={**INVOICE, "status": "paid"}),
    )
    async with client:
        invoice = await client.create_invoice("USDT_TON", 5, order_id="order-1042")
        assert invoice.order_id == "order-1042"
        paid = await client.wait_for_invoice(invoice)
    assert paid.is_paid
    assert no_sleep == [2.0]
    assert len(rec.requests) == 3


async def test_async_errors(make_async_client):
    client, _ = make_async_client(respond(409, {"detail": {"code": "conflict", "message": "paid"}}))
    with pytest.raises(ConflictError):
        await client.cancel_invoice("x")
