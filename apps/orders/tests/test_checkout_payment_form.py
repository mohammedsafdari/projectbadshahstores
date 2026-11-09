"""Tests for the khata checkbox of the checkout form (D74)."""

from __future__ import annotations

from apps.orders.forms import CheckoutForm
from apps.orders.presentation import ADDRESS_INVALID


def _form(**fields: str) -> CheckoutForm:
    return CheckoutForm(fields)


def test_form_defaults_to_cash_on_delivery() -> None:
    form = _form(address="7")

    assert form.is_valid()
    assert form.cleaned_data["use_credit"] is False


def test_form_accepts_the_khata_checkbox() -> None:
    form = _form(address="7", use_credit="on")

    assert form.is_valid()
    assert form.cleaned_data["use_credit"] is True


def test_form_has_no_payment_method_field() -> None:
    form = _form(address="7")

    assert "payment_method" not in form.fields


def test_form_still_refuses_a_missing_address() -> None:
    form = _form()

    assert not form.is_valid()
    assert form.errors["address"] == [str(ADDRESS_INVALID)]
