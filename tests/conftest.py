from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

INVOICE = {
    "invoice_id": "12c22c1a-a496-4c1e-abe3-72661ef8706e",
    "payment_url": "https://paysell.me/pay/12c22c1a-a496-4c1e-abe3-72661ef8706e",
    "address": "UQAvDJp7QDwqRcuNQBiK2GhBt71Xh1_UMYPCzMkQAoBPmZKl",
    "asset": "USDT_TON",
    "amount": "5",
    "amount_minor": "5000000",
    "status": "pending",
    "paid": "0",
    "paid_minor": "0",
    "order_id": "order-1042",
    "description": "Pro subscription",
    "expires_at": "2026-09-06T17:20:55Z",
    "created_at": "2026-09-06T15:20:55.123456789Z",
}


class Recorder:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def json(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


def respond(
    status: int = 200, body: Any = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=INVOICE if body is None else body, headers=headers)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def fake_async_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("paysell._client.time.sleep", slept.append)
    monkeypatch.setattr("paysell._client.asyncio.sleep", fake_async_sleep)
    return slept


@pytest.fixture
def make_client() -> Callable[..., tuple[Any, Recorder]]:
    from paysell import Paysell

    def factory(*responses: httpx.Response | Exception, **kwargs: Any) -> tuple[Paysell, Recorder]:
        recorder = Recorder(list(responses))
        http = httpx.Client(transport=httpx.MockTransport(recorder))
        return Paysell("sk_live_test", http_client=http, **kwargs), recorder

    return factory


@pytest.fixture
def make_async_client() -> Callable[..., tuple[Any, Recorder]]:
    from paysell import AsyncPaysell

    def factory(
        *responses: httpx.Response | Exception, **kwargs: Any
    ) -> tuple[AsyncPaysell, Recorder]:
        recorder = Recorder(list(responses))
        http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
        return AsyncPaysell("sk_live_test", http_client=http, **kwargs), recorder

    return factory
