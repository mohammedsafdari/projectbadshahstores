"""The 1000-character password cap on every password field (Slice 38h)."""

from __future__ import annotations

from apps.accounts.auth_presentation import PASSWORD_TOO_LONG_MESSAGE, auth_text
from apps.accounts.forms import (
    ChangePasswordForm,
    PasswordLoginForm,
    ResetPasswordForm,
    SignupForm,
)

LONG = "a" * 1001
MAX = "a" * 1000


def _login(password: str) -> PasswordLoginForm:
    return PasswordLoginForm({"identifier": "98765 43210", "password": password})


def _signup(password: str) -> SignupForm:
    return SignupForm(
        {
            "phone": "98765 43210",
            "full_name": "Asha Rao",
            "email": "asha@example.com",
            "password1": password,
            "password2": password,
        }
    )


def _reset(password: str) -> ResetPasswordForm:
    return ResetPasswordForm(
        {
            "code": "123456",
            "new_password1": password,
            "new_password2": password,
        }
    )


def _change(password: str, *, current: str = "StrongPass1") -> ChangePasswordForm:
    return ChangePasswordForm(
        {
            "current_password": current,
            "new_password1": password,
            "new_password2": password,
        },
        require_current=True,
    )


def _errors(form, name: str) -> list[str]:
    # ``str()`` forces each lazy message now, so plain strings can be compared.
    return [str(error) for error in form.errors[name]]


def test_login_form_refuses_a_password_over_1000_characters() -> None:
    form = _login(LONG)

    assert not form.is_valid()
    assert _errors(form, "password") == [str(PASSWORD_TOO_LONG_MESSAGE)]


def test_login_form_accepts_a_password_of_exactly_1000_characters() -> None:
    form = _login(MAX)

    assert form.is_valid(), form.errors


def test_signup_form_refuses_a_password_over_1000_characters() -> None:
    form = _signup(LONG)

    assert not form.is_valid()
    assert _errors(form, "password1") == [str(PASSWORD_TOO_LONG_MESSAGE)]
    assert _errors(form, "password2") == [str(PASSWORD_TOO_LONG_MESSAGE)]


def test_reset_form_refuses_a_new_password_over_1000_characters() -> None:
    form = _reset(LONG)

    assert not form.is_valid()
    assert _errors(form, "new_password1") == [str(PASSWORD_TOO_LONG_MESSAGE)]
    assert _errors(form, "new_password2") == [str(PASSWORD_TOO_LONG_MESSAGE)]


def test_change_password_form_refuses_a_current_password_over_1000_characters() -> None:
    form = _change("StrongPass1", current=LONG)

    assert not form.is_valid()
    assert _errors(form, "current_password") == [str(PASSWORD_TOO_LONG_MESSAGE)]


def test_change_password_form_refuses_a_new_password_over_1000_characters() -> None:
    form = _change(LONG)

    assert not form.is_valid()
    assert _errors(form, "new_password1") == [str(PASSWORD_TOO_LONG_MESSAGE)]
    assert _errors(form, "new_password2") == [str(PASSWORD_TOO_LONG_MESSAGE)]


def test_password_too_long_message_is_in_the_auth_text() -> None:
    assert auth_text()["password_too_long_message"] == "The password is too long."
