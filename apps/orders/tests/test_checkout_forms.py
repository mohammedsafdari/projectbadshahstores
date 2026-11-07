"""Tests for the checkout form (D66)."""

from __future__ import annotations

from apps.orders.forms import CheckoutForm
from apps.orders.models import Order


def _form(**fields: str) -> CheckoutForm:
    return CheckoutForm(fields)


def test_form_accepts_an_address_and_defaults_to_call_me() -> None:
    form = _form(address="7")

    assert form.is_valid()
    assert form.cleaned_data["address"] == 7
    assert form.cleaned_data["substitution_preference"] == Order.SubstitutionPreference.CALL_ME
    assert form.cleaned_data["customer_note"] == ""


def test_form_refuses_an_unknown_substitution_preference() -> None:
    form = _form(address="7", substitution_preference="NOT_A_CHOICE")

    assert not form.is_valid()
    assert "substitution_preference" in form.errors


def test_form_refuses_a_note_over_300_characters() -> None:
    form = _form(address="7", customer_note="x" * 301)

    assert not form.is_valid()
    assert "customer_note" in form.errors


def test_form_strips_the_note() -> None:
    form = _form(address="7", customer_note="  Leave at the door.  ")

    assert form.is_valid()
    assert form.cleaned_data["customer_note"] == "Leave at the door."


def test_form_requires_an_address() -> None:
    form = _form()

    assert not form.is_valid()
    assert "address" in form.errors


def test_form_refuses_a_non_numeric_address() -> None:
    form = _form(address="not-a-number")

    assert not form.is_valid()
    assert "address" in form.errors


def test_form_accepts_a_blank_note() -> None:
    form = _form(address="7", customer_note="")

    assert form.is_valid()
    assert form.cleaned_data["customer_note"] == ""
