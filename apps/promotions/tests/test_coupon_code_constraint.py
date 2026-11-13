"""Tests for the coupon code's database format constraint (D82)."""

from __future__ import annotations

import pytest
from django.db import DataError, IntegrityError, transaction

from apps.promotions.models import Coupon
from apps.promotions.tests.helpers import make_coupon

pytestmark = pytest.mark.django_db


def _store(code: str) -> None:
    """Write a code straight to the column, so ``save()`` cannot tidy it first."""
    coupon = make_coupon()
    with pytest.raises(IntegrityError), transaction.atomic():
        Coupon.objects.filter(pk=coupon.pk).update(code=code)


def test_the_database_refuses_a_lower_case_code() -> None:
    _store("diwali10")


def test_the_database_refuses_a_code_of_three_characters() -> None:
    _store("DIW")


def test_the_database_refuses_a_code_of_twenty_one_characters() -> None:
    coupon = make_coupon()
    # The column holds 20 characters, so the database refuses the value itself
    # (DataError) before the format check is even reached.
    with pytest.raises((DataError, IntegrityError)), transaction.atomic():
        Coupon.objects.filter(pk=coupon.pk).update(code="D" * 21)


def test_the_database_refuses_a_code_with_a_space() -> None:
    _store("DIWALI 10")


def test_the_database_refuses_an_empty_code() -> None:
    _store("")


def test_the_database_accepts_a_code_with_a_hyphen() -> None:
    coupon = make_coupon()
    Coupon.objects.filter(pk=coupon.pk).update(code="DIWALI-10")

    assert Coupon.objects.get(pk=coupon.pk).code == "DIWALI-10"
