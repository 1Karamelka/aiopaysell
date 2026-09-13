from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Union

AmountLike = Union[str, int, Decimal]

ASSET_DECIMALS: dict[str, int] = {
    "TON": 9,
    "USDT_TON": 6,
}
"""Decimal places per asset. ``1.5`` TON is ``1500000000`` in smallest units."""


def _decimals(asset: str) -> int:
    try:
        return ASSET_DECIMALS[str(asset)]
    except KeyError:
        known = ", ".join(ASSET_DECIMALS)
        raise ValueError(f"unknown asset {asset!r}; expected one of: {known}") from None


def _to_decimal(amount: AmountLike) -> Decimal:
    if isinstance(amount, (bool, float)):
        # float is refused on purpose: 0.1 + 0.2 is not 0.3, and this is money.
        raise TypeError(
            "amount must be str, int or Decimal, not "
            f"{type(amount).__name__}; pass Decimal('1.5') or '1.5' instead"
        )
    if not isinstance(amount, (str, int, Decimal)):
        raise TypeError(f"amount must be str, int or Decimal, not {type(amount).__name__}")
    try:
        value = Decimal(amount.strip() if isinstance(amount, str) else amount)
    except InvalidOperation:
        raise ValueError(f"not a number: {amount!r}") from None
    if not value.is_finite():
        raise ValueError(f"amount must be finite, got {amount!r}")
    if value <= 0:
        raise ValueError(f"amount must be positive, got {amount!r}")
    return value


def format_amount(amount: AmountLike, asset: str) -> str:
    """Format an amount in normal units the way the API expects it.

    ``5`` -> ``"5"``, ``Decimal("1.50")`` -> ``"1.5"``, ``"0.1"`` -> ``"0.1"``.

    :raises ValueError: unknown asset, non-positive amount, or more decimal
        places than the asset has (TON 9, USDT_TON 6).
    :raises TypeError: ``amount`` is a float or another unsupported type.
    """
    decimals = _decimals(asset)
    # normalize() drops trailing zeros, so "1.5000000000" TON counts as 1.5.
    value = _plain(_to_decimal(amount))
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)  # finite values only, checked above
    if -exponent > decimals:
        raise ValueError(f"{asset} has {decimals} decimal places; {amount!r} has more")
    return format(value, "f")


def _plain(value: Decimal) -> Decimal:
    """Strip trailing zeros without turning 10 into 1E+1."""
    value = value.normalize()
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)
    return value.quantize(Decimal(1)) if exponent > 0 else value


def to_minor(amount: AmountLike, asset: str) -> int:
    """Convert normal units to the integer smallest-unit value.

    ``to_minor("1.5", "USDT_TON")`` -> ``1500000``.
    """
    decimals = _decimals(asset)
    return int(Decimal(format_amount(amount, asset)).scaleb(decimals))


def from_minor(minor: int | str, asset: str) -> Decimal:
    """Convert a smallest-unit integer (as in webhooks) to normal units.

    ``from_minor("4905000", "USDT_TON")`` -> ``Decimal("4.905")``.
    """
    decimals = _decimals(asset)
    return _plain(Decimal(int(minor)).scaleb(-decimals))
