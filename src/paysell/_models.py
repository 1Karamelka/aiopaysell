from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Union

from ._amounts import from_minor


class Asset:
    """Assets an invoice can be issued in."""

    TON = "TON"
    USDT_TON = "USDT_TON"


class InvoiceStatus:
    """Invoice statuses.

    Statuses are plain strings on the models, not an enum: the API may add
    new ones, and an unknown status must not crash your integration.
    """

    PENDING = "pending"
    """Waiting for payment. Keep the order open."""
    PAID = "paid"
    """Paid in full. Release the goods."""
    OVERPAID = "overpaid"
    """More arrived than asked; the surplus is credited in full. Release the goods."""
    UNDERPAID = "underpaid"
    """Less arrived. Still open for a top-up to the same address, for its
    lifetime plus a 24-hour grace period. Do not release the goods."""
    EXPIRED = "expired"
    """The window closed. May still carry money — check ``paid_minor``."""
    CANCELLED = "cancelled"
    """Cancelled by you."""

    PAID_STATUSES = frozenset({PAID, OVERPAID})
    """The only statuses on which goods may be released."""
    OPEN_STATUSES = frozenset({PENDING, UNDERPAID})
    """Statuses in which the invoice still accepts money."""
    FINAL_STATUSES = frozenset({PAID, OVERPAID, EXPIRED, CANCELLED})
    """Statuses that will not change any more."""


class EventType:
    """Webhook event types."""

    PAYMENT_CREDITED = "payment.credited"
    PAYMENT_REJECTED = "payment.rejected"


_FRACTION = re.compile(r"\.(\d+)")


def _parse_datetime(value: Any) -> datetime | None:
    """Parse RFC 3339 timestamps, including ``Z`` and nanosecond fractions,
    which ``datetime.fromisoformat`` only accepts from Python 3.11."""
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _minor(value: str | None) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True)
class Invoice:
    """An invoice, as returned by ``create_invoice``, ``get_invoice`` and ``cancel_invoice``.

    Amounts come in pairs: ``amount`` is normal units, exactly as you sent it
    (display this); ``amount_minor`` is the integer in smallest units (compute
    with this).
    """

    invoice_id: str
    status: str
    asset: str
    amount: str
    amount_minor: str
    address: str
    """Receiving address, non-bounceable. If you render your own checkout,
    show it exactly as returned — never re-encode it."""
    expires_at: datetime
    """When the invoice stops accepting money. ``underpaid`` gets 24 more hours."""
    created_at: datetime
    payment_url: str | None = None
    """Send the buyer here. The page shows amount, address, QR and live status."""
    paid: str | None = None
    """How much has arrived so far, in normal units."""
    paid_minor: str | None = None
    """How much has arrived so far, in smallest units."""
    order_id: str | None = None
    description: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    """The response exactly as received, including fields this version does not know."""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Invoice:
        return cls(
            invoice_id=str(data["invoice_id"]),
            status=str(data["status"]),
            asset=str(data["asset"]),
            amount=str(data["amount"]),
            amount_minor=str(data["amount_minor"]),
            address=str(data["address"]),
            expires_at=_parse_datetime(data["expires_at"]),  # type: ignore[arg-type]
            created_at=_parse_datetime(data["created_at"]),  # type: ignore[arg-type]
            payment_url=_opt_str(data.get("payment_url")),
            paid=_opt_str(data.get("paid")),
            paid_minor=_opt_str(data.get("paid_minor")),
            order_id=_opt_str(data.get("order_id")),
            description=_opt_str(data.get("description")),
            raw=dict(data),
        )

    @property
    def amount_decimal(self) -> Decimal:
        return Decimal(self.amount)

    @property
    def amount_minor_int(self) -> int:
        return int(self.amount_minor)

    @property
    def paid_minor_int(self) -> int:
        """Smallest units received so far; ``0`` when the API did not say."""
        return int(self.paid_minor) if self.paid_minor is not None else 0

    @property
    def remaining_minor(self) -> int:
        """Smallest units still owed; ``0`` once paid or overpaid."""
        return max(self.amount_minor_int - self.paid_minor_int, 0)

    @property
    def is_paid(self) -> bool:
        """``True`` for ``paid`` and ``overpaid`` — the only time to release goods."""
        return self.status in InvoiceStatus.PAID_STATUSES

    @property
    def is_open(self) -> bool:
        """``True`` while the invoice still accepts money (``pending``, ``underpaid``)."""
        return self.status in InvoiceStatus.OPEN_STATUSES

    @property
    def is_final(self) -> bool:
        """``True`` once the status can no longer change."""
        return self.status in InvoiceStatus.FINAL_STATUSES


@dataclass(frozen=True)
class PublicInvoice:
    """The buyer-facing view of an invoice, from ``get_public_invoice``.

    Narrower than :class:`Invoice` on purpose: no ``order_id``, no ``created_at``.
    """

    invoice_id: str
    status: str
    asset: str
    amount: str
    amount_minor: str
    address: str
    expires_at: datetime
    paid: str | None = None
    paid_minor: str | None = None
    description: str | None = None
    shop_name: str | None = None
    shop_url: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PublicInvoice:
        return cls(
            invoice_id=str(data["invoice_id"]),
            status=str(data["status"]),
            asset=str(data["asset"]),
            amount=str(data["amount"]),
            amount_minor=str(data["amount_minor"]),
            address=str(data["address"]),
            expires_at=_parse_datetime(data["expires_at"]),  # type: ignore[arg-type]
            paid=_opt_str(data.get("paid")),
            paid_minor=_opt_str(data.get("paid_minor")),
            description=_opt_str(data.get("description")),
            shop_name=_opt_str(data.get("shop_name")),
            shop_url=_opt_str(data.get("shop_url")),
            raw=dict(data),
        )

    @property
    def amount_minor_int(self) -> int:
        return int(self.amount_minor)

    @property
    def paid_minor_int(self) -> int:
        return int(self.paid_minor) if self.paid_minor is not None else 0

    @property
    def remaining_minor(self) -> int:
        return max(self.amount_minor_int - self.paid_minor_int, 0)

    @property
    def is_paid(self) -> bool:
        return self.status in InvoiceStatus.PAID_STATUSES


@dataclass(frozen=True)
class PaymentCredited:
    """``data`` of a ``payment.credited`` webhook.

    **All money here is in smallest units** — the opposite of the REST API.
    Use ``*_decimal`` properties for normal units.
    """

    invoice_id: str | None
    order_id: str | None
    """Your own reference, exactly as sent when creating the invoice."""
    asset: str | None
    """The coin that actually arrived — not necessarily the invoice's coin."""
    amount: str | None
    """What arrived in this transfer."""
    credited: str | None
    """What landed on your balance: ``amount`` minus ``fee``."""
    fee: str | None
    status: str | None
    """The invoice's status after this payment. Release goods only on paid/overpaid."""
    paid_minor: str | None
    """Total received on the invoice so far, in the invoice's coin."""
    tx_hash: str | None
    asset_mismatch: bool = False
    """``True`` when a different coin arrived than the invoice asks for. The
    money is credited to you, but the invoice stays unpaid."""
    invoice_asset: str | None = None
    """With ``asset_mismatch``: the coin the invoice asks for."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PaymentCredited:
        return cls(
            invoice_id=_opt_str(data.get("invoice_id")),
            order_id=_opt_str(data.get("order_id")),
            asset=_opt_str(data.get("asset")),
            amount=_opt_str(data.get("amount")),
            credited=_opt_str(data.get("credited")),
            fee=_opt_str(data.get("fee")),
            status=_opt_str(data.get("status")),
            paid_minor=_opt_str(data.get("paid_minor")),
            tx_hash=_opt_str(data.get("tx_hash")),
            asset_mismatch=bool(data.get("asset_mismatch", False)),
            invoice_asset=_opt_str(data.get("invoice_asset")),
            raw=dict(data),
        )

    @property
    def is_paid(self) -> bool:
        """``True`` when the invoice is now ``paid`` or ``overpaid``."""
        return self.status in InvoiceStatus.PAID_STATUSES

    @property
    def amount_int(self) -> int | None:
        return _minor(self.amount)

    @property
    def credited_int(self) -> int | None:
        return _minor(self.credited)

    @property
    def fee_int(self) -> int | None:
        return _minor(self.fee)

    @property
    def paid_minor_int(self) -> int | None:
        return _minor(self.paid_minor)

    @property
    def amount_decimal(self) -> Decimal | None:
        return self._decimal(self.amount, self.asset)

    @property
    def credited_decimal(self) -> Decimal | None:
        return self._decimal(self.credited, self.asset)

    @property
    def fee_decimal(self) -> Decimal | None:
        return self._decimal(self.fee, self.asset)

    @property
    def paid_decimal(self) -> Decimal | None:
        """``paid_minor`` in normal units of the invoice's coin."""
        return self._decimal(self.paid_minor, self.invoice_asset or self.asset)

    @staticmethod
    def _decimal(minor: str | None, asset: str | None) -> Decimal | None:
        if minor is None or asset is None:
            return None
        return from_minor(minor, asset)


@dataclass(frozen=True)
class PaymentRejected:
    """``data`` of a ``payment.rejected`` webhook.

    A deposit held for review was declined; the money will not reach your
    balance and the invoice status does not change. Release nothing.
    """

    invoice_id: str | None
    order_id: str | None
    asset: str | None
    amount: str | None
    """What arrived on chain, in smallest units."""
    tx_hash: str | None
    reason: str | None
    """Why it was declined — you will need it to explain to the buyer."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PaymentRejected:
        return cls(
            invoice_id=_opt_str(data.get("invoice_id")),
            order_id=_opt_str(data.get("order_id")),
            asset=_opt_str(data.get("asset")),
            amount=_opt_str(data.get("amount")),
            tx_hash=_opt_str(data.get("tx_hash")),
            reason=_opt_str(data.get("reason")),
            raw=dict(data),
        )

    @property
    def amount_int(self) -> int | None:
        return _minor(self.amount)

    @property
    def amount_decimal(self) -> Decimal | None:
        if self.amount is None or self.asset is None:
            return None
        return from_minor(self.amount, self.asset)


EventData = Union[PaymentCredited, PaymentRejected, Mapping[str, Any]]


@dataclass(frozen=True)
class WebhookEvent:
    """A verified webhook delivery.

    ``data`` is a :class:`PaymentCredited` or :class:`PaymentRejected`; for an
    event type this version does not know, it is the raw ``dict``.
    """

    event_id: str
    """Unique per event. The same event can arrive more than once — deduplicate on this."""
    type: str
    data: EventData
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WebhookEvent:
        event_type = str(payload["type"])
        data = payload.get("data") or {}
        if not isinstance(data, Mapping):
            raise ValueError("webhook 'data' is not an object")
        parsed: EventData
        if event_type == EventType.PAYMENT_CREDITED:
            parsed = PaymentCredited.from_dict(data)
        elif event_type == EventType.PAYMENT_REJECTED:
            parsed = PaymentRejected.from_dict(data)
        else:
            parsed = dict(data)
        return cls(
            event_id=str(payload["event_id"]), type=event_type, data=parsed, raw=dict(payload)
        )

    @property
    def is_payment_credited(self) -> bool:
        return self.type == EventType.PAYMENT_CREDITED

    @property
    def is_payment_rejected(self) -> bool:
        return self.type == EventType.PAYMENT_REJECTED
