"""Tests for the forgot-password, reset and account forms (D73)."""

from __future__ import annotations

from apps.accounts.auth_presentation import PASSWORDS_DIFFER_MESSAGE
from apps.accounts.forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    ForgotPasswordForm,
    ResetPasswordForm,
)


def test_forgot_form_accepts_a_phone() -> None:
    form = ForgotPasswordForm({"identifier": "98765 43210"})

    assert form.is_valid() is True
    assert form.cleaned_data["identifier"] == "98765 43210"


def test_forgot_form_accepts_an_email() -> None:
    form = ForgotPasswordForm({"identifier": "asha@example.com"})

    assert form.is_valid() is True
    assert form.cleaned_data["identifier"] == "asha@example.com"


def test_forgot_form_requires_the_identifier() -> None:
    form = ForgotPasswordForm({"identifier": ""})

    assert form.is_valid() is False
    assert "identifier" in form.errors


def test_reset_form_accepts_a_six_digit_code_and_matching_passwords() -> None:
    form = ResetPasswordForm(
        {
            "code": "123456",
            "new_password1": "StrongPass1",
            "new_password2": "StrongPass1",
        }
    )

    assert form.is_valid() is True


def test_reset_form_refuses_a_code_with_letters() -> None:
    form = ResetPasswordForm(
        {
            "code": "abcdef",
            "new_password1": "StrongPass1",
            "new_password2": "StrongPass1",
        }
    )

    assert form.is_valid() is False
    assert "code" in form.errors


def test_reset_form_refuses_different_passwords() -> None:
    form = ResetPasswordForm(
        {
            "code": "123456",
            "new_password1": "StrongPass1",
            "new_password2": "OtherPass2",
        }
    )

    assert form.is_valid() is False
    assert str(PASSWORDS_DIFFER_MESSAGE) in form.errors["new_password2"]


def test_reset_form_refuses_a_weak_password() -> None:
    form = ResetPasswordForm(
        {
            "code": "123456",
            "new_password1": "12345678",
            "new_password2": "12345678",
        }
    )

    assert form.is_valid() is False
    assert "new_password1" in form.errors


def test_change_password_form_accepts_matching_passwords() -> None:
    form = ChangePasswordForm(
        {
            "new_password1": "StrongPass1",
            "new_password2": "StrongPass1",
        }
    )

    assert form.is_valid() is True


def test_change_password_form_refuses_different_passwords() -> None:
    form = ChangePasswordForm(
        {
            "new_password1": "StrongPass1",
            "new_password2": "OtherPass2",
        }
    )

    assert form.is_valid() is False
    assert str(PASSWORDS_DIFFER_MESSAGE) in form.errors["new_password2"]


def test_change_email_form_lowercases_and_requires_a_valid_email() -> None:
    good = ChangeEmailForm({"email": " Asha@Example.COM "})
    bad = ChangeEmailForm({"email": "not-an-email"})

    assert good.is_valid() is True
    assert good.cleaned_data["email"] == "asha@example.com"
    assert bad.is_valid() is False
    assert "email" in bad.errors
