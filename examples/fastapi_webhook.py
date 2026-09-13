"""Minimal webhook receiver.

pip install paysell fastapi uvicorn
PAYSELL_WEBHOOK_SECRET=... uvicorn examples.fastapi_webhook:app --port 8000
"""

import os

from fastapi import FastAPI, Request, Response

from paysell import PaymentCredited, PaymentRejected, WebhookVerificationError, construct_event

WEBHOOK_SECRET = os.environ["PAYSELL_WEBHOOK_SECRET"]
app = FastAPI()
seen: set[str] = set()  # use a unique column in your database instead


@app.post("/paysell/webhook")
async def paysell_webhook(request: Request) -> Response:
    try:
        event = construct_event(await request.body(), request.headers, WEBHOOK_SECRET)
    except WebhookVerificationError:
        return Response(status_code=400)

    if event.event_id in seen:
        return Response(status_code=200)
    seen.add(event.event_id)

    if isinstance(event.data, PaymentCredited):
        if event.data.is_paid:
            print(f"order {event.data.order_id} paid, credited {event.data.credited_decimal}")
        else:
            print(f"order {event.data.order_id}: {event.data.status}, waiting for the rest")
    elif isinstance(event.data, PaymentRejected):
        print(f"deposit for order {event.data.order_id} rejected: {event.data.reason}")

    return Response(status_code=200)
