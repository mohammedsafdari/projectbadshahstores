"""Tests for the cart quantity rules, with no database (D42, D43, D60)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.cart.rules import (
    MAX_LOOSE_PER_LINE,
    MAX_PACKETS_PER_LINE,
    InvalidCartQuantityError,
    QuantityTooLargeError,
    check_cart_quantity,
)
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory


def _packet() -> ProductVariant:
    """An unsaved packet variant: the rules only read its fields."""
    return ProductVariantFactory.build(
        is_sold_loose=False,
        quantity_step=Decimal("1"),
        unit=ProductVariant.Unit.PACKET,
    )


def _loose(step: str = "0.001") -> ProductVariant:
    return ProductVariantFactory.build(
        is_sold_loose=True,
        quantity_step=Decimal(step),
        unit=ProductVariant.Unit.KG,
        pack_size=Decimal("1.000"),
    )


def test_packet_accepts_a_whole_number() -> None:
    assert check_cart_quantity(_packet(), 2) == Decimal("2.000")


def test_packet_refuses_a_fraction() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_packet(), Decimal("1.500"))


def test_packet_refuses_zero() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_packet(), 0)


def test_packet_refuses_a_negative_number() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_packet(), -1)


def test_loose_accepts_one_gram() -> None:
    assert check_cart_quantity(_loose(), Decimal("0.001")) == Decimal("0.001")


def test_loose_accepts_a_three_decimal_quantity() -> None:
    assert check_cart_quantity(_loose(), Decimal("1.234")) == Decimal("1.234")


def test_loose_refuses_a_fourth_decimal() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_loose(), Decimal("0.0001"))


def test_loose_refuses_a_fourth_decimal_that_would_round_to_a_valid_quantity() -> None:
    # 1.2345 is positive and could round to 1.234, so only the decimal rule
    # can refuse it. The old 0.0001 test was also refused by the zero rule.
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_loose(), Decimal("1.2345"))


def test_packet_refuses_a_fraction_even_if_its_step_is_not_one() -> None:
    # The database forbids a non-loose variant with a step other than 1, so
    # this unsaved instance guards the whole-number rule independently of it.
    packet = ProductVariantFactory.build(
        is_sold_loose=False,
        quantity_step=Decimal("0.500"),
        unit=ProductVariant.Unit.PACKET,
    )

    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(packet, Decimal("1.5"))


def test_loose_refuses_zero() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_loose(), Decimal("0.000"))


def test_loose_follows_the_variants_own_step() -> None:
    assert check_cart_quantity(_loose("0.100"), Decimal("0.300")) == Decimal("0.300")


def test_loose_refuses_a_quantity_that_is_not_a_multiple_of_the_step() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_loose("0.100"), Decimal("0.250"))


def test_float_is_refused() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_loose(), 1.0)


def test_string_is_refused() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_packet(), "2")


def test_bool_is_refused() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_packet(), True)


def test_nan_and_infinity_are_refused() -> None:
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_loose(), Decimal("NaN"))
    with pytest.raises(InvalidCartQuantityError):
        check_cart_quantity(_loose(), Decimal("Infinity"))


def test_trailing_zeros_are_accepted_and_the_result_has_three_decimals() -> None:
    result = check_cart_quantity(_loose(), Decimal("2.5000"))

    assert result == Decimal("2.500")
    assert result.as_tuple().exponent == -3


def test_packet_limit_per_line_is_99() -> None:
    assert MAX_PACKETS_PER_LINE == 99
    assert check_cart_quantity(_packet(), 99) == Decimal("99.000")
    with pytest.raises(QuantityTooLargeError):
        check_cart_quantity(_packet(), 100)


def test_loose_limit_per_line_is_50() -> None:
    assert Decimal("50") == MAX_LOOSE_PER_LINE
    assert check_cart_quantity(_loose(), Decimal("50")) == Decimal("50.000")
    with pytest.raises(QuantityTooLargeError):
        check_cart_quantity(_loose(), Decimal("50.001"))


def test_an_integer_is_accepted_and_returned_as_a_decimal() -> None:
    result = check_cart_quantity(_packet(), 3)

    assert isinstance(result, Decimal)
    assert result == Decimal("3.000")
