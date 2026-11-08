"""The sign-up form checks text and passwords without a database (D72)."""

from __future__ import annotations

from apps.accounts.address_presentation import PHONE_ERROR
from apps.accounts.auth_presentation import (
    EMAIL_INVALID_MESSAGE,
    EMAIL_REQUIRED_MESSAGE,
    NAME_REQUIRED_MESSAGE,
    PASSWORDS_DIFFER_MESSAGE,
)
from apps.accounts.forms import SignupForm


def _data(**overrides) -> dict[str, str]:
    # ``**overrides`` collects extra keyword arguments into a dict, so one test
    # can change a single field and leave the rest of the form data alone.
    data = {
        "phone": "98765 43210",
        "full_name": "Asha Rao",
        "email": "asha@example.com",
        "password1": "StrongPass1",
        "password2": "StrongPass1",
    }
    data.update(overrides)
    return data


def test_valid_signup_form_cleans_phone_email_and_name() -> None:
    form = SignupForm(_data(full_name="  Asha Rao  ", email="  ASHA@Example.COM  "))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["phone"] == "+919876543210"
    assert form.cleaned_data["email"] == "asha@example.com"
    assert form.cleaned_data["full_name"] == "Asha Rao"


def test_phone_is_normalised() -> None:
    form = SignupForm(_data(phone="98765 43210"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["phone"] == "+919876543210"


def test_email_is_lowercased() -> None:
    form = SignupForm(_data(email="  ASHA@Example.COM  "))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["email"] == "asha@example.com"


def test_email_is_required() -> None:
    form = SignupForm(_data(email=""))

    assert not form.is_valid()
    assert str(EMAIL_REQUIRED_MESSAGE) in form.errors["email"]


def test_full_name_is_required() -> None:
    form = SignupForm(_data(full_name="   "))

    assert not form.is_valid()
    assert str(NAME_REQUIRED_MESSAGE) in form.errors["full_name"]


def test_invalid_phone_is_refused() -> None:
    form = SignupForm(_data(phone="12345"))

    assert not form.is_valid()
    assert str(PHONE_ERROR) in form.errors["phone"]


def test_invalid_email_is_refused() -> None:
    form = SignupForm(_data(email="not-an-email"))

    assert not form.is_valid()
    assert str(EMAIL_INVALID_MESSAGE) in form.errors["email"]


def test_passwords_must_match() -> None:
    form = SignupForm(_data(password2="AnotherPass1"))

    assert not form.is_valid()
    assert str(PASSWORDS_DIFFER_MESSAGE) in form.errors["password2"]


def test_short_password_is_refused() -> None:
    form = SignupForm(_data(password1="Ab1!", password2="Ab1!"))

    assert not form.is_valid()
    assert "too short" in form.errors["password1"][0]


def test_common_password_is_refused() -> None:
    form = SignupForm(_data(password1="password", password2="password"))

    assert not form.is_valid()
    assert "too common" in form.errors["password1"][0]


def test_numeric_password_is_refused() -> None:
    form = SignupForm(_data(password1="12345678", password2="12345678"))

    assert not form.is_valid()
    assert any("entirely numeric" in message for message in form.errors["password1"])


def test_password_similar_to_the_phone_or_email_is_refused() -> None:
    email = "asha.rao@example.com"
    form = SignupForm(_data(email=email, password1=email, password2=email))

    assert not form.is_valid()
    assert "too similar" in form.errors["password1"][0]
