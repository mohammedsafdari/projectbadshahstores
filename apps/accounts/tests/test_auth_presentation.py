"""The login and sign-up wording file, read on its own (Slice 38f, D72).

No database and no request: these tests only guard the sentences the customer
reads, so a careless edit cannot start telling the page which field was wrong.
"""

from __future__ import annotations

from apps.accounts.auth_presentation import (
    LOGIN_ERROR_MESSAGE,
    LOGIN_LOCKED_MESSAGE,
    SIGNUP_EXISTS_MESSAGE,
    auth_text,
)


def test_auth_text_has_the_login_and_signup_sentences() -> None:
    text = auth_text()

    assert text["password_login_heading"] == "Log in with your password"
    assert text["otp_login_heading"] == "Or log in with a code"
    assert text["identifier_label"] == "Mobile number or email"
    assert text["password_login_button"] == "Log in"
    assert text["signup_button"] == "Create account"
    assert text["login_link"] == "Already have an account? Log in"


def test_lock_message_says_fifteen_minutes() -> None:
    assert "15 minutes" in str(LOGIN_LOCKED_MESSAGE)


def test_login_error_does_not_name_a_field() -> None:
    # The sentence lists all three possible parts, so it cannot say which one
    # was wrong. Naming a single field would confirm that the others were right.
    message = str(LOGIN_ERROR_MESSAGE).lower()
    named = [word for word in ("phone", "email", "password") if word in message]

    assert len(named) == 3


def test_signup_exists_message_names_neither_phone_nor_email() -> None:
    message = str(SIGNUP_EXISTS_MESSAGE).lower()

    assert "phone" not in message
    assert "email" not in message
