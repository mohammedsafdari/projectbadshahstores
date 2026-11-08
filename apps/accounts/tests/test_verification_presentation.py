"""Tests for the verification wording and masking helpers (D71)."""

from __future__ import annotations

from types import SimpleNamespace

from django.urls import reverse
from django.utils import timezone

from apps.accounts.verification_presentation import (
    BANNER_HEADING,
    EMAIL_TITLE,
    NO_EMAIL_MESSAGE,
    PHONE_TITLE,
    VERIFIED_MESSAGE,
    mask_email,
    mask_phone,
    pending_verifications,
    verification_text,
)


def test_mask_phone_keeps_the_last_four_digits() -> None:
    assert mask_phone("+919876543210") == "+91******3210"


def test_mask_email_keeps_the_first_letter_and_the_domain() -> None:
    assert mask_email("asha@example.com") == "a***@example.com"


def test_mask_email_for_a_one_letter_name() -> None:
    assert mask_email("a@example.com") == "a***@example.com"


def test_pending_verifications_lists_phone_and_email_when_both_are_missing() -> None:
    # SimpleNamespace is a tiny stand-in object: a bag of named attributes,
    # so this test needs no database row.
    user = SimpleNamespace(
        phone_verified_at=None,
        email="asha@example.com",
        email_verified_at=None,
    )

    items = pending_verifications(user)

    assert [item["kind"] for item in items] == ["phone", "email"]
    assert [item["url"] for item in items] == [
        reverse("account_verify_phone"),
        reverse("account_verify_email"),
    ]


def test_pending_verifications_skips_a_verified_phone() -> None:
    user = SimpleNamespace(
        phone_verified_at=timezone.now(),
        email="asha@example.com",
        email_verified_at=None,
    )

    items = pending_verifications(user)

    assert [item["kind"] for item in items] == ["email"]


def test_pending_verifications_is_empty_when_the_customer_is_ready() -> None:
    user = SimpleNamespace(
        phone_verified_at=timezone.now(),
        email="asha@example.com",
        email_verified_at=timezone.now(),
    )

    assert pending_verifications(user) == []


def test_pending_verifications_skips_the_email_when_the_account_has_none() -> None:
    user = SimpleNamespace(
        phone_verified_at=None,
        email=None,
        email_verified_at=None,
    )

    items = pending_verifications(user)

    assert [item["kind"] for item in items] == ["phone"]


def test_verification_text_has_the_new_sentences() -> None:
    text = verification_text()

    assert text["banner_heading"] == str(BANNER_HEADING)
    assert text["verified_message"] == str(VERIFIED_MESSAGE)
    assert text["no_email_message"] == str(NO_EMAIL_MESSAGE)
    assert text["phone_title"] == str(PHONE_TITLE)
    assert text["email_title"] == str(EMAIL_TITLE)
    assert text["send_code_button"] == "Send me a code"
    assert text["confirm_button"] == "Verify"
    assert text["code_label"] == "Code"
