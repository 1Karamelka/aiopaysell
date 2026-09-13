"""Create an invoice and wait for it to be paid.

PAYSELL_API_KEY=sk_live_... python examples/create_invoice.py
"""

from paysell import Paysell

with Paysell() as paysell:
    invoice = paysell.create_invoice("TON", "0.1", order_id="example-1", description="SDK example")
    print("Pay here:", invoice.payment_url)

    invoice = paysell.wait_for_invoice(invoice, interval=5)
    print("Final status:", invoice.status, "received:", invoice.paid)
