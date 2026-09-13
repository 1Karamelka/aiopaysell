from decimal import Decimal

import pytest

from paysell import format_amount, from_minor, to_minor


@pytest.mark.parametrize(
    ("amount", "asset", "expected"),
    [
        (5, "USDT_TON", "5"),
        ("5", "USDT_TON", "5"),
        (" 1.50 ", "TON", "1.5"),
        (Decimal("10"), "TON", "10"),
        (Decimal("1E+1"), "TON", "10"),
        ("0.000000001", "TON", "0.000000001"),
        ("1.5000000000", "TON", "1.5"),
        ("7000", "TON", "7000"),
    ],
)
def test_format_amount(amount, asset, expected):
    assert format_amount(amount, asset) == expected


@pytest.mark.parametrize(
    ("amount", "asset", "error"),
    [
        (1.5, "TON", TypeError),
        (True, "TON", TypeError),
        ("0.0000001", "USDT_TON", ValueError),
        ("0.0000000001", "TON", ValueError),
        ("0", "TON", ValueError),
        ("NaN", "TON", ValueError),
        ("Infinity", "TON", ValueError),
        ("1e", "TON", ValueError),
        ("1", "BTC", ValueError),
    ],
)
def test_format_amount_rejects(amount, asset, error):
    with pytest.raises(error):
        format_amount(amount, asset)


def test_minor_conversions():
    assert to_minor("1.5", "USDT_TON") == 1_500_000
    assert to_minor(5, "TON") == 5_000_000_000
    assert from_minor("4905000", "USDT_TON") == Decimal("4.905")
    assert str(from_minor(10_000_000_000, "TON")) == "10"
    assert from_minor(0, "TON") == 0
