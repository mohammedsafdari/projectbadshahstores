"""Tests for the address form (D65)."""

from __future__ import annotations

from typing import Any

from apps.accounts.forms import AddressForm
from apps.accounts.models import Address


def _data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "recipient_name": "Asha Sharma",
        "recipient_phone": "9876543210",
        "line1": "12, Saifee Nagar",
        "line2": "Near the mosque",
        "landmark": "Opposite the school",
        "city": "Indore",
        "state": "Madhya Pradesh",
        "pincode": "452012",
        "label": Address.Label.HOME,
    }
    data.update(changes)
    return data


def test_form_accepts_a_valid_address() -> None:
    form = AddressForm(_data())

    assert form.is_valid(), form.errors
    assert form.cleaned_data["recipient_name"] == "Asha Sharma"
    assert form.cleaned_data["recipient_phone"] == "+919876543210"


def test_form_normalises_the_phone() -> None:
    form = AddressForm(_data(recipient_phone="98765 43210"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["recipient_phone"] == "+919876543210"


def test_form_refuses_a_five_digit_pin_code() -> None:
    form = AddressForm(_data(pincode="45201"))

    assert not form.is_valid()
    assert "pincode" in form.errors


def test_form_refuses_letters_in_the_pin_code() -> None:
    form = AddressForm(_data(pincode="45A012"))

    assert not form.is_valid()
    assert "pincode" in form.errors


def test_form_strips_spaces_from_text_fields() -> None:
    form = AddressForm(
        _data(
            recipient_name="  Asha Sharma  ",
            line1="  12, Saifee Nagar  ",
            line2="  Near the mosque  ",
            landmark="  Opposite the school  ",
            city="  Indore  ",
            state="  Madhya Pradesh  ",
        )
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["recipient_name"] == "Asha Sharma"
    assert form.cleaned_data["line1"] == "12, Saifee Nagar"
    assert form.cleaned_data["line2"] == "Near the mosque"
    assert form.cleaned_data["landmark"] == "Opposite the school"
    assert form.cleaned_data["city"] == "Indore"
    assert form.cleaned_data["state"] == "Madhya Pradesh"


def test_form_requires_name_line1_and_pin() -> None:
    data = _data()
    del data["recipient_name"]
    del data["line1"]
    del data["pincode"]

    form = AddressForm(data)

    assert not form.is_valid()
    assert "recipient_name" in form.errors
    assert "line1" in form.errors
    assert "pincode" in form.errors
