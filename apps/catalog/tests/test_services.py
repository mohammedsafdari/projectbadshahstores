"""Tests for the catalog pricing helpers. No database is needed."""

from decimal import Decimal

import pytest

from apps.catalog.models import ProductVariant
from apps.catalog.services import (
    gst_breakdown,
    round_money,
    savings_amount,
    savings_percent,
    unit_price,
)

# Short alias so the number lists below stay readable.
D = Decimal


def test_round_money_rounds_halves_up() -> None:
    assert round_money(D("0.125")) == D("0.13")
    assert round_money(D("0.124")) == D("0.12")
    assert round_money(D("2.5")) == D("2.50")


def test_gst_breakdown_for_5_percent() -> None:
    result = gst_breakdown(D("105.00"), D("5"))
    assert result.gst_amount == D("5.00")
    assert result.taxable_value == D("100.00")
    assert result.cgst == D("2.50")
    assert result.sgst == D("2.50")


def test_gst_breakdown_for_18_percent() -> None:
    result = gst_breakdown(D("118.00"), D("18"))
    assert result.gst_amount == D("18.00")
    assert result.taxable_value == D("100.00")
    assert result.cgst == D("9.00")
    assert result.sgst == D("9.00")


def test_gst_breakdown_with_awkward_price() -> None:
    # 100 * 5 / 105 = 4.7619..., which rounds to 4.76 paise-exact.
    result = gst_breakdown(D("100.00"), D("5"))
    assert result.gst_amount == D("4.76")
    assert result.taxable_value == D("95.24")
    assert result.cgst == D("2.38")
    assert result.sgst == D("2.38")
    assert result.gst_amount.as_tuple().exponent == -2


def test_gst_breakdown_odd_paise_split_still_adds_up() -> None:
    result = gst_breakdown(D("1.05"), D("5"))
    assert result.gst_amount == D("0.05")
    assert result.cgst == D("0.03")
    assert result.sgst == D("0.02")


def test_gst_breakdown_with_zero_rate() -> None:
    result = gst_breakdown(D("40.00"), D("0"))
    assert result.gst_amount == D("0.00")
    assert result.taxable_value == D("40.00")
    assert result.cgst == D("0.00")
    assert result.sgst == D("0.00")


# Two stacked parametrize decorators run every price with every rate.
@pytest.mark.parametrize(
    "price",
    [D("0.01"), D("1.05"), D("49.99"), D("99.00"), D("1234.56")],
)
@pytest.mark.parametrize("rate", [D("0"), D("5"), D("12"), D("18"), D("28")])
def test_gst_breakdown_always_adds_up(price: Decimal, rate: Decimal) -> None:
    result = gst_breakdown(price, rate)
    assert result.taxable_value + result.gst_amount == price
    assert result.cgst + result.sgst == result.gst_amount


def test_gst_breakdown_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        gst_breakdown(D("-1.00"), D("5"))
    with pytest.raises(ValueError):
        gst_breakdown(D("10.00"), D("101"))
    with pytest.raises(ValueError):
        gst_breakdown(D("10.00"), D("-1"))


def test_savings_amount() -> None:
    assert savings_amount(D("100.00"), D("95.00")) == D("5.00")
    assert savings_amount(D("100.00"), D("100.00")) == D("0.00")


def test_savings_percent_is_rounded_down() -> None:
    assert savings_percent(D("100.00"), D("95.00")) == D("5")
    assert savings_percent(D("200.00"), D("150.00")) == D("25")
    # 9 / 99 = 9.09...%, shown as 9%.
    assert savings_percent(D("99.00"), D("90.00")) == D("9")
    # 6.25% is shown as 6%, never rounded up to 7%.
    assert savings_percent(D("80.00"), D("75.00")) == D("6")
    assert savings_percent(D("100.00"), D("100.00")) == D("0")


def test_savings_reject_bad_input() -> None:
    with pytest.raises(ValueError):
        savings_amount(D("0.00"), D("0.00"))
    with pytest.raises(ValueError):
        savings_percent(D("100.00"), D("101.00"))
    with pytest.raises(ValueError):
        savings_percent(D("100.00"), D("-1.00"))


@pytest.mark.parametrize(
    ("price", "pack_size", "unit", "amount", "label"),
    [
        (D("95.00"), D("5"), "KG", D("19.00"), "per kg"),
        (D("50.00"), D("500"), "G", D("10.00"), "per 100 g"),
        (D("90.00"), D("1"), "L", D("90.00"), "per litre"),
        (D("30.00"), D("200"), "ML", D("15.00"), "per 100 ml"),
        (D("60.00"), D("6"), "PIECE", D("10.00"), "per piece"),
        (D("25.00"), D("1"), "PACKET", D("25.00"), "per packet"),
        (D("100.00"), D("3"), "KG", D("33.33"), "per kg"),
    ],
)
def test_unit_price(
    price: Decimal,
    pack_size: Decimal,
    unit: str,
    amount: Decimal,
    label: str,
) -> None:
    result = unit_price(price, pack_size, unit)
    assert result.amount == amount
    assert result.label == label


def test_unit_price_accepts_the_model_unit_choices() -> None:
    result = unit_price(D("95.00"), D("5"), ProductVariant.Unit.KG)
    assert result.amount == D("19.00")
    assert isinstance(result.amount, Decimal)


def test_unit_price_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        unit_price(D("10.00"), D("0"), "KG")
    with pytest.raises(ValueError):
        unit_price(D("10.00"), D("1"), "XX")
