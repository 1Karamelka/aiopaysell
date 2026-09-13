from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TypeVar
from urllib.parse import quote

import httpx

from ._amounts import AmountLike, format_amount
from ._errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    PaysellError,
    RateLimitError,
    error_class_for,
)
from ._models import Invoice, PublicInvoice
from ._version import __version__

DEFAULT_BASE_URL = "https://paysell.me/api/merchant/v1"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_WAIT = 60.0
"""A ``Retry-After`` longer than this is not waited out; the error is raised instead."""

_API_KEY_ENV = "PAYSELL_API_KEY"

_FALLBACK_CODES = {
    401: "invalid_api_key",
    404: "not_found",
    409: "conflict",
    422: "invalid_input",
    429: "too_many_requests",
    502: "cbc_unreachable",
}

T = TypeVar("T")


@dataclass(frozen=True)
class _Request:
    method: str
    path: str
    body: dict[str, Any] | None
    auth: bool
    # Whether a retry after a lost answer or a 5xx is safe. A 429 is always
    # retried: the rate limiter rejects the request before it does anything.
    replay_safe: bool


def _path_id(value: str | Invoice | PublicInvoice) -> str:
    raw = value.invoice_id if isinstance(value, (Invoice, PublicInvoice)) else str(value)
    raw = raw.strip()
    if not raw:
        raise ValueError("invoice_id must not be empty")
    return quote(raw, safe="")


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(seconds, 0.0)


def _raise_for_response(response: httpx.Response) -> None:
    status = response.status_code
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text

    code = _FALLBACK_CODES.get(status, "http_error")
    message = response.reason_phrase or f"HTTP {status}"
    details: list[str] | None = None
    errors: list[dict[str, Any]] | None = None

    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        code = str(detail.get("code") or code)
        message = str(detail.get("message") or message)
        raw_details = detail.get("details")
        if isinstance(raw_details, list):
            details = [str(d) for d in raw_details]
    elif isinstance(detail, list):
        code = "validation_error"
        errors = [e for e in detail if isinstance(e, dict)]
        parts = []
        for err in errors:
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
        message = "; ".join(parts) or "request validation failed"
    elif isinstance(detail, str):
        message = detail

    cls = error_class_for(status)
    kwargs: dict[str, Any] = {"details": details, "errors": errors, "body": body}
    if cls is RateLimitError:
        kwargs["retry_after"] = _parse_retry_after(response.headers.get("Retry-After"))
    raise cls(status, code, message, **kwargs)


def _parse(response: httpx.Response, parse: Callable[[Mapping[str, Any]], T]) -> T:
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError("not a JSON object")
        return parse(data)
    except (ValueError, KeyError, TypeError) as exc:
        raise APIError(
            response.status_code,
            "invalid_response",
            f"unexpected response from the API: {exc!r}",
            body=response.text,
        ) from exc


class _BaseClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str | None,
        max_retries: int,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(_API_KEY_ENV)
        self._api_key = key.strip() if key else None
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.max_retries = max_retries

    def __repr__(self) -> str:
        # Never print the key.
        return f"{type(self).__name__}(base_url={self.base_url!r})"

    @property
    def _default_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": f"paysell-python/{__version__}",
        }

    def _headers(self, request: _Request) -> dict[str, str]:
        headers = {}
        if request.auth:
            if not self._api_key:
                raise PaysellError(
                    f"no API key: pass api_key=... or set the {_API_KEY_ENV} environment variable"
                )
            headers["Authorization"] = f"Bearer {self._api_key}"
        if request.body is not None:
            headers["Content-Type"] = "application/json"
        return headers

    def _content(self, request: _Request) -> bytes | None:
        if request.body is None:
            return None
        return json.dumps(request.body, separators=(",", ":")).encode()

    def _retry_delay(self, attempt: int, request: _Request, error: Exception) -> float | None:
        """Seconds to wait before the next attempt, or ``None`` to give up."""
        if attempt >= self.max_retries:
            return None
        if isinstance(error, RateLimitError):
            if error.retry_after is not None:
                return error.retry_after if error.retry_after <= MAX_RETRY_WAIT else None
        elif isinstance(error, APIConnectionError) or (
            isinstance(error, APIError) and error.status_code >= 500
        ):
            if not request.replay_safe:
                return None
        else:
            return None
        backoff: float = min(0.5 * (2**attempt), 8.0)
        return backoff + random.uniform(0, backoff / 4)

    # --- request builders, shared by both clients ---------------------------

    @staticmethod
    def _create_invoice_request(
        asset: str,
        amount: AmountLike,
        order_id: str | None,
        description: str | None,
        ttl_minutes: int | None,
        idempotency_key: str | None,
    ) -> _Request:
        asset = str(asset)
        body: dict[str, Any] = {
            "asset": asset,
            "amount": format_amount(amount, asset),
            # Always sent: it is what makes retrying this call safe. Pass your
            # own, stored with the order, to stay safe across process restarts.
            "idempotency_key": idempotency_key or uuid.uuid4().hex,
        }
        if order_id is not None:
            body["order_id"] = order_id
        if description is not None:
            body["description"] = description
        if ttl_minutes is not None:
            if isinstance(ttl_minutes, bool) or not isinstance(ttl_minutes, int):
                raise TypeError("ttl_minutes must be an int")
            if not 1 <= ttl_minutes <= 1440:
                raise ValueError("ttl_minutes must be between 1 and 1440")
            body["ttl_minutes"] = ttl_minutes
        return _Request("POST", "/invoices", body, auth=True, replay_safe=True)

    @staticmethod
    def _get_invoice_request(invoice: str | Invoice) -> _Request:
        return _Request("GET", f"/invoices/{_path_id(invoice)}", None, auth=True, replay_safe=True)

    @staticmethod
    def _cancel_invoice_request(invoice: str | Invoice) -> _Request:
        # Not replay-safe: if the first attempt went through, a retry answers
        # 409 and hides the success.
        return _Request(
            "POST", f"/invoices/{_path_id(invoice)}/cancel", None, auth=True, replay_safe=False
        )

    @staticmethod
    def _get_public_invoice_request(invoice: str | Invoice | PublicInvoice) -> _Request:
        return _Request(
            "GET", f"/public/invoices/{_path_id(invoice)}", None, auth=False, replay_safe=True
        )


class Paysell(_BaseClient):
    """Synchronous Paysell client.

    ::

        from paysell import Paysell

        with Paysell("sk_live_...") as paysell:
            invoice = paysell.create_invoice("USDT_TON", "5", order_id="order-1042")
            redirect(invoice.payment_url)

    :param api_key: your ``sk_live_`` key. Defaults to ``$PAYSELL_API_KEY``.
    :param base_url: API root; you should not need to change it.
    :param timeout: seconds, or an ``httpx.Timeout``.
    :param max_retries: automatic retries for rate limits, network errors and
        5xx on requests that are safe to repeat. ``0`` disables them.
    :param http_client: your own ``httpx.Client`` (proxies, custom transport).
        It is not closed by :meth:`close`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(api_key, base_url=base_url, max_retries=max_retries)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout)

    def __enter__(self) -> Paysell:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connection pool."""
        if self._owns_client:
            self._client.close()

    def _send(self, request: _Request, parse: Callable[[Mapping[str, Any]], T]) -> T:
        attempt = 0
        while True:
            try:
                response = self._client.request(
                    request.method,
                    self.base_url + request.path,
                    content=self._content(request),
                    headers={**self._default_headers, **self._headers(request)},
                )
                if response.is_success:
                    return _parse(response, parse)
                _raise_for_response(response)
            except httpx.TimeoutException as exc:
                error: Exception = APITimeoutError(f"request timed out: {exc}")
            except httpx.TransportError as exc:
                error = APIConnectionError(f"connection error: {exc}")
            except APIError as exc:
                error = exc
            delay = self._retry_delay(attempt, request, error)
            if delay is None:
                raise error
            time.sleep(delay)
            attempt += 1

    def create_invoice(
        self,
        asset: str,
        amount: AmountLike,
        *,
        order_id: str | None = None,
        description: str | None = None,
        ttl_minutes: int | None = None,
        idempotency_key: str | None = None,
    ) -> Invoice:
        """Create an invoice and get a ``payment_url`` to send the buyer to.

        :param asset: ``"TON"`` or ``"USDT_TON"``.
        :param amount: in **normal units** — ``"5"``, ``5``, ``Decimal("1.5")``.
            Floats are refused. At most 9 decimals for TON, 6 for USDT_TON.
            Limits: 0.1–7000 TON, 3–10000 USDT.
        :param order_id: your reference (≤200 chars), echoed back in webhooks.
        :param description: shown to the buyer (≤1000 chars).
        :param ttl_minutes: lifetime, 1–1440. Default on the server: 120.
        :param idempotency_key: reuse the same value when retrying the same
            order and you get the existing invoice, never a second one. If
            omitted, one is generated per call so automatic retries are safe.
        :raises InvalidRequestError: bad amount or outside limits.
        :raises RateLimitError: too many invoices this hour or open at once.
        """
        request = self._create_invoice_request(
            asset, amount, order_id, description, ttl_minutes, idempotency_key
        )
        return self._send(request, Invoice.from_dict)

    def get_invoice(self, invoice: str | Invoice) -> Invoice:
        """Read the current state of an invoice.

        :raises NotFoundError: unknown id, or another shop's invoice.
        """
        return self._send(self._get_invoice_request(invoice), Invoice.from_dict)

    def cancel_invoice(self, invoice: str | Invoice) -> Invoice:
        """Cancel an open invoice and release its address.

        :raises ConflictError: the invoice is no longer open.
        """
        return self._send(self._cancel_invoice_request(invoice), Invoice.from_dict)

    def get_public_invoice(self, invoice: str | Invoice | PublicInvoice) -> PublicInvoice:
        """Read the buyer-facing view of an invoice. Needs no API key; limited per IP."""
        return self._send(self._get_public_invoice_request(invoice), PublicInvoice.from_dict)

    def wait_for_invoice(
        self,
        invoice: str | Invoice,
        *,
        interval: float = 5.0,
        timeout: float | None = None,
    ) -> Invoice:
        """Poll until the invoice reaches a final status (paid, overpaid, expired, cancelled).

        A fallback for scripts and local development. In production, webhooks
        are the primary channel.

        :param interval: seconds between reads; keep it at a few seconds.
        :param timeout: give up after this many seconds.
        :raises TimeoutError: the invoice did not become final in time.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            current = self.get_invoice(invoice)
            if current.is_final:
                return current
            if deadline is not None and time.monotonic() + interval > deadline:
                raise TimeoutError(
                    f"invoice {current.invoice_id} is still {current.status!r} after {timeout}s"
                )
            time.sleep(interval)


class AsyncPaysell(_BaseClient):
    """Asynchronous Paysell client. Same methods as :class:`Paysell`, awaited.

    ::

        from paysell import AsyncPaysell

        async with AsyncPaysell("sk_live_...") as paysell:
            invoice = await paysell.create_invoice("TON", "1.5", order_id="order-1042")
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key, base_url=base_url, max_retries=max_retries)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> AsyncPaysell:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying connection pool."""
        if self._owns_client:
            await self._client.aclose()

    async def _send(self, request: _Request, parse: Callable[[Mapping[str, Any]], T]) -> T:
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    request.method,
                    self.base_url + request.path,
                    content=self._content(request),
                    headers={**self._default_headers, **self._headers(request)},
                )
                if response.is_success:
                    return _parse(response, parse)
                _raise_for_response(response)
            except httpx.TimeoutException as exc:
                error: Exception = APITimeoutError(f"request timed out: {exc}")
            except httpx.TransportError as exc:
                error = APIConnectionError(f"connection error: {exc}")
            except APIError as exc:
                error = exc
            delay = self._retry_delay(attempt, request, error)
            if delay is None:
                raise error
            await asyncio.sleep(delay)
            attempt += 1

    async def create_invoice(
        self,
        asset: str,
        amount: AmountLike,
        *,
        order_id: str | None = None,
        description: str | None = None,
        ttl_minutes: int | None = None,
        idempotency_key: str | None = None,
    ) -> Invoice:
        """Create an invoice. See :meth:`Paysell.create_invoice`."""
        request = self._create_invoice_request(
            asset, amount, order_id, description, ttl_minutes, idempotency_key
        )
        return await self._send(request, Invoice.from_dict)

    async def get_invoice(self, invoice: str | Invoice) -> Invoice:
        """Read an invoice. See :meth:`Paysell.get_invoice`."""
        return await self._send(self._get_invoice_request(invoice), Invoice.from_dict)

    async def cancel_invoice(self, invoice: str | Invoice) -> Invoice:
        """Cancel an open invoice. See :meth:`Paysell.cancel_invoice`."""
        return await self._send(self._cancel_invoice_request(invoice), Invoice.from_dict)

    async def get_public_invoice(self, invoice: str | Invoice | PublicInvoice) -> PublicInvoice:
        """Buyer-facing view, no API key. See :meth:`Paysell.get_public_invoice`."""
        return await self._send(self._get_public_invoice_request(invoice), PublicInvoice.from_dict)

    async def wait_for_invoice(
        self,
        invoice: str | Invoice,
        *,
        interval: float = 5.0,
        timeout: float | None = None,
    ) -> Invoice:
        """Poll until final. See :meth:`Paysell.wait_for_invoice`."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            current = await self.get_invoice(invoice)
            if current.is_final:
                return current
            if deadline is not None and time.monotonic() + interval > deadline:
                raise TimeoutError(
                    f"invoice {current.invoice_id} is still {current.status!r} after {timeout}s"
                )
            await asyncio.sleep(interval)
