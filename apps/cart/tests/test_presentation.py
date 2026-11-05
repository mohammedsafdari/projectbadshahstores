"""Tests for the cart error and quantity wording (D61)."""

from __future__ import annotations

from decimal import Decimal

from apps.cart.presentation import describe_cart_error, format_quantity
from apps.cart.rules import (
    CartError,
    InsufficientAvailabilityError,
    InvalidCartQuantityError,
    NotPurchasableError,
    QuantityTooLargeError,
)
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory


def _loose() -> ProductVariant:
    return ProductVariantFactory.build(
        is_sold_loose=True,
        quantity_step=Decimal("0.001"),
        unit=ProductVariant.Unit.KG,
        pack_size=Decimal("1.000"),
    )


def _packet() -> ProductVariant:
    return ProductVariantFactory.build(
        is_sold_loose=False,
        quantity_step=Decimal("1"),
        unit=ProductVariant.Unit.PACKET,
    )


def test_invalid_quantity_message_names_the_step() -> None:
    message = str(describe_cart_error(InvalidCartQuantityError("bad"), _loose()))

    assert "0.001" in message
    assert "kg" in message


def test_too_large_message_names_the_cap() -> None:
    message = str(describe_cart_error(QuantityTooLargeError("too big"), _loose()))

    assert "50.000 kg" in message


def test_not_purchasable_message_is_generic_and_does_not_name_the_reason() -> None:
    message = str(describe_cart_error(NotPurchasableError("archived"), _loose()))

    assert message == "This item is not available right now."
    for reason in ("archived", "brand", "category", "not for sale"):
        assert reason not in message


def test_insufficient_message_shows_the_available_amount() -> None:
    message = str(describe_cart_error(InsufficientAvailabilityError(Decimal("2.000")), _loose()))

    assert "2.000 kg" in message


def test_available_zero_message_says_out_of_stock() -> None:
    message = str(describe_cart_error(InsufficientAvailabilityError(Decimal("0.000")), _loose()))

    assert "out of stock" in message.lower()


def test_loose_amounts_are_shown_with_three_decimals_and_the_unit() -> None:
    assert format_quantity(_loose(), Decimal("0.25")) == "0.250 kg"


def test_packet_amounts_are_shown_as_whole_numbers() -> None:
    assert format_quantity(_packet(), Decimal("1.000")) == "1 pack"
    assert format_quantity(_packet(), Decimal("2.000")) == "2 packs"


def test_every_cart_error_class_has_a_message() -> None:
    classes = CartError.__subclasses__()
    # Without this the loop would pass on an empty list.
    assert classes
    for error_class in classes:
        if error_class is InsufficientAvailabilityError:
            error = error_class(Decimal("0"))
        else:
            error = error_class("test")
        assert str(describe_cart_error(error, _packet()))
