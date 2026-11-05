"""Tests for the shared test factories (Slice 33)."""

from __future__ import annotations

from apps.core.phone import normalise_phone
from tests.factories import UserFactory


def test_user_factory_phones_stay_valid_past_one_hundred_users() -> None:
    users = UserFactory.build_batch(250)

    for user in users:
        # Raises ValueError if the number is not a valid Indian mobile number.
        assert normalise_phone(user.phone) == user.phone


def test_user_factory_phones_are_unique_across_two_hundred_users() -> None:
    users = UserFactory.build_batch(200)

    assert len({user.phone for user in users}) == 200
