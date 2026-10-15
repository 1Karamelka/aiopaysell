# Paysell for Python

Python client for the [Paysell](https://paysell.me) API. Accept **TON** and **USDT on TON** with hosted invoices and signed webhooks.

- Sync and async clients, same methods
- Typed models, one dependency `(httpx)`
- Webhook verification `(byte-for-byte)`
- Safe retries, idempotent invoice creation

```bash
pip install paysell
```

Python 3.9+. API reference: **[paysell.me/docs](https://paysell.me/docs)**.

## Create an invoice

```python
from paysell import Paysell

paysell = Paysell("sk_live_...")  # or set PAYSELL_API_KEY

invoice = paysell.create_invoice(
    "USDT_TON",
    "5",  # normal units: 5 USDT
    order_id="order-1042",  # comes back in every webhook
    description="Pro subscription",
    idempotency_key="order-1042",  # store with the order; retries return the same invoice
)

redirect(invoice.payment_url)  # amount, address, QR and live status on that page
```

Async:

```python
from paysell import AsyncPaysell

async with AsyncPaysell("sk_live_...") as paysell:
    invoice = await paysell.create_invoice("TON", "1.5", order_id="order-1042")
```

Reuse one client for your app's lifetime. Close it with `close()` or use it as a context manager.

### Other calls

```python
invoice = paysell.get_invoice(invoice_id)  # current state
invoice = paysell.cancel_invoice(invoice_id)  # release the address
public = paysell.get_public_invoice(invoice_id)  # public view, no API key
```

## Receive webhooks

Paysell sends `payment.credited` and `payment.rejected` to the webhook URL of your API key. Each delivery is signed with the key's **webhook secret**, shown once next to the key. It is not the API key.

```python
from paysell import PaymentCredited, WebhookVerificationError, construct_event

WEBHOOK_SECRET = os.environ["PAYSELL_WEBHOOK_SECRET"]


def handle(raw_body: bytes, headers) -> int:
    try:
        event = construct_event(raw_body, headers, WEBHOOK_SECRET)
    except WebhookVerificationError:
        return 400

    if already_handled(event.event_id):  # the same event can arrive twice
        return 200
    remember(event.event_id)

    if isinstance(event.data, PaymentCredited) and event.data.is_paid:
        mark_order_paid(event.data.order_id)

    return 200
```

`construct_event` checks the HMAC over the **raw** body and rejects timestamps more than five minutes from your clock. Pass the bytes exactly as received, not re-serialized JSON.

<details>
<summary>FastAPI</summary>

```python
from fastapi import FastAPI, Request, Response
from paysell import PaymentCredited, WebhookVerificationError, construct_event

app = FastAPI()


@app.post("/paysell/webhook")
async def paysell_webhook(request: Request):
    try:
        event = construct_event(await request.body(), request.headers, WEBHOOK_SECRET)
    except WebhookVerificationError:
        return Response(status_code=400)
    if isinstance(event.data, PaymentCredited) and event.data.is_paid:
        await mark_order_paid(event.data.order_id, event_id=event.event_id)
    return Response(status_code=200)
```
</details>

<details>
<summary>Flask</summary>

```python
@app.post("/paysell/webhook")
def paysell_webhook():
    try:
        event = construct_event(request.get_data(), request.headers, WEBHOOK_SECRET)
    except WebhookVerificationError:
        return "", 400
    if isinstance(event.data, PaymentCredited) and event.data.is_paid:
        mark_order_paid(event.data.order_id, event_id=event.event_id)
    return "", 200
```
</details>

<details>
<summary>Django</summary>

```python
@csrf_exempt
@require_POST
def paysell_webhook(request):
    try:
        event = construct_event(request.body, request.headers, settings.PAYSELL_WEBHOOK_SECRET)
    except WebhookVerificationError:
        return HttpResponse(status=400)
    if isinstance(event.data, PaymentCredited) and event.data.is_paid:
        mark_order_paid(event.data.order_id, event_id=event.event_id)
    return HttpResponse(status=200)
```
</details>

<details>
<summary>aiohttp</summary>

```python
async def paysell_webhook(request: web.Request) -> web.Response:
    try:
        event = construct_event(await request.read(), request.headers, WEBHOOK_SECRET)
    except WebhookVerificationError:
        return web.Response(status=400)
    if isinstance(event.data, PaymentCredited) and event.data.is_paid:
        await mark_order_paid(event.data.order_id, event_id=event.event_id)
    return web.Response(status=200)
```
</details>

Rules that matter:

- **Release goods only when `event.data.is_paid`** (status `paid` or `overpaid`). `underpaid` is still open; `payment.rejected` never means paid.
- **Answer 2xx within 10 seconds**, then do slow work. Anything else is retried for about 31 hours.
- **Deduplicate on `event.event_id`** (a unique column in your database).
- `asset_mismatch=True` means the buyer sent a different coin. You are credited, the invoice stays unpaid.

Test your handler without a real payment:

```python
from paysell.webhooks import generate_test_headers

body = b'{"event_id":"test-1","type":"payment.credited","data":{"order_id":"order-1042","status":"paid"}}'
headers = generate_test_headers(body, WEBHOOK_SECRET)
# POST body with these headers to your endpoint
```

## Amounts

The REST API speaks **normal units** as strings: `"5"` is 5 USDT, `"1.5"` is 1.5 TON. Webhook amounts are **smallest units** (USDT has 6 decimals, TON 9).

```python
create_invoice("USDT_TON", "5")  # str, int or Decimal; floats are refused
invoice.amount, invoice.amount_minor  # "5", "5000000"
event.data.credited_decimal  # Decimal("4.905") from "4905000"

from paysell import to_minor, from_minor

to_minor("1.5", "TON")  # 1500000000
from_minor("4905000", "USDT_TON")  # Decimal("4.905")
```

Limits are 0.1 to 7000 TON, 3 to 10000 USDT per invoice.

## Errors

| Exception | HTTP | When |
|---|---|---|
| `AuthenticationError` | 401 | Key missing, wrong or revoked |
| `NotFoundError` | 404 | No such invoice, or another shop's |
| `ConflictError` | 409 | For example, cancelling an invoice that is no longer open |
| `InvalidRequestError` | 422 | Bad input or amount outside limits |
| `RateLimitError` | 429 | `retry_after` says how long to wait |
| `ServiceUnavailableError` | 5xx | Processing unavailable; retry with the same `idempotency_key` |
| `APIConnectionError` / `APITimeoutError` | — | No answer received |

All API errors carry `status_code`, `code` (branch on it), `message` and `details`. Everything inherits from `PaysellError`.

```python
from paysell import InvalidRequestError, RateLimitError

try:
    invoice = paysell.create_invoice("USDT_TON", "1", idempotency_key=order.key)
except InvalidRequestError as e:
    print(e.code, e.message)  # invalid_input invoice amount below the minimum
```

### Retries

By default the client retries twice: on `429` (after `Retry-After`, if ≤60 s), and on network errors and `5xx` for safe-to-repeat requests: reads and invoice creation, which always carries an `idempotency_key`. `cancel_invoice` is not repeated after a lost answer. Set `max_retries=0` to handle everything yourself.

A lost answer to `create_invoice` does not mean no invoice was created. Pass your own `idempotency_key` stored with the order, so a retry after a crash returns the same invoice.

## Polling fallback

Webhooks are the primary channel. For scripts, bots and local development:

```python
invoice = paysell.wait_for_invoice(invoice.invoice_id, interval=5, timeout=3600)
if invoice.is_paid:
    ...
```

Returns once the invoice is `paid`, `overpaid`, `expired` or `cancelled`.

## Configuration

```python
Paysell(
    api_key=None,  # default: $PAYSELL_API_KEY
    timeout=30.0,  # seconds or httpx.Timeout
    max_retries=2,
    http_client=None,  # your own httpx.Client (proxies, transports)
)
```

## License

MIT
