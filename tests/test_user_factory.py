"""Tests for the shared user factory (Slice 35c)."""

from __future__ import annotations

from tests.factories import UserFactory


def test_user_factory_emails_are_clean_and_unique() -> None:
    users = UserFactory.build_batch(50)

    assert users
    for user in users:
        assert "<" not in user.email
        assert ">" not in user.email
        assert " " not in user.email

    # A set drops duplicates, so 50 different emails leave 50 entries.
    emails = [user.email for user in users]
    assert len(set(emails)) == 50
