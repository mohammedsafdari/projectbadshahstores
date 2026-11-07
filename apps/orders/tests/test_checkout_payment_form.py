"""Tests for the payment method field of the checkout form (D68)."""

from __future__ import annotations

from apps.orders.forms import CheckoutForm
from apps.orders.models import Order
from apps.orders.presentation import PAYMENT_METHOD_INVALID


def _form(**fields: str) -> CheckoutForm:
    return CheckoutForm(fields)


def test_form_accepts_cod() -> None:
    form = _form(address="7", payment_method=Order.PaymentMethod.COD)

    assert form.is_valid()
    assert form.cleaned_data["payment_method"] == Order.PaymentMethod.COD


def test_form_accepts_credit() -> None:
    form = _form(address="7", payment_method=Order.PaymentMethod.CREDIT)

    assert form.is_valid()
    assert form.cleaned_data["payment_method"] == Order.PaymentMethod.CREDIT


def test_form_defaults_to_cod() -> None:
    form = _form(address="7")

    assert form.is_valid()
    assert form.cleaned_data["payment_method"] == Order.PaymentMethod.COD


def test_form_refuses_an_unknown_method() -> None:
    form = _form(address="7", payment_method="ONLINE")

    assert not form.is_valid()
    assert form.errors["payment_method"] == [str(PAYMENT_METHOD_INVALID)]
