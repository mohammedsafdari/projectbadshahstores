"""Tests for the cart forms (D61)."""

from __future__ import annotations

from decimal import Decimal

from apps.cart.forms import CartQuantityForm, CartRemoveForm


def _quantity_form(text: str) -> CartQuantityForm:
    return CartQuantityForm({"variant_id": "1", "quantity": text})


def test_plain_integer_is_parsed() -> None:
    form = _quantity_form("2")

    assert form.is_valid()
    assert form.cleaned_data["quantity"] == Decimal("2")


def test_one_to_three_decimals_are_parsed() -> None:
    for text in ("1.2", "1.23", "1.234"):
        form = _quantity_form(text)
        assert form.is_valid()
        assert form.cleaned_data["quantity"] == Decimal(text)


def test_four_decimals_are_refused() -> None:
    form = _quantity_form("1.2345")

    assert not form.is_valid()
    assert "quantity" in form.errors


def test_comma_is_refused() -> None:
    form = _quantity_form("1,000")

    assert not form.is_valid()
    assert "quantity" in form.errors


def test_negative_is_refused() -> None:
    form = _quantity_form("-1")

    assert not form.is_valid()
    assert "quantity" in form.errors


def test_empty_is_refused() -> None:
    form = _quantity_form("")

    assert not form.is_valid()
    assert "quantity" in form.errors


def test_spaces_around_are_stripped() -> None:
    form = _quantity_form(" 1.250 ")

    assert form.is_valid()
    assert form.cleaned_data["quantity"] == Decimal("1.250")


def test_letters_are_refused() -> None:
    form = _quantity_form("abc")

    assert not form.is_valid()
    assert "quantity" in form.errors


def test_exponent_notation_is_refused() -> None:
    form = _quantity_form("1e3")

    assert not form.is_valid()
    assert "quantity" in form.errors


def test_very_long_digits_are_refused() -> None:
    form = _quantity_form("1234567")

    assert not form.is_valid()
    assert "quantity" in form.errors


def test_variant_id_must_be_a_positive_integer() -> None:
    assert not CartQuantityForm({"variant_id": "0", "quantity": "1"}).is_valid()
    assert not CartQuantityForm({"variant_id": "-2", "quantity": "1"}).is_valid()
    assert not CartRemoveForm({"variant_id": "not-a-number"}).is_valid()
