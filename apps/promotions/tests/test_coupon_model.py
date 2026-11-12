"""Tests for the coupon and redemption models (D81).

REVIEW CAREFULLY: money. Every amount here is a ``Decimal``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.promotions.models import Coupon
from apps.promotions.tests.helpers import make_coupon, make_redemption

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# The code
# ---------------------------------------------------------------------------
def test_the_code_is_stored_in_capitals_and_trimmed() -> None:
    coupon = make_coupon(code="  diwali10  ")

    assert coupon.code == "DIWALI10"


def test_a_lower_case_code_is_refused_by_the_database_check() -> None:
    coupon = make_coupon(code="DIWALI10")

    # ``update()`` writes SQL directly, so the model's save() cannot fix the
    # case and the check constraint must refuse it.
    with pytest.raises(IntegrityError), transaction.atomic():
        Coupon.objects.filter(pk=coupon.pk).update(code="diwali10")

    coupon.refresh_from_db()
    assert coupon.code == "DIWALI10"


def test_the_code_is_unique() -> None:
    make_coupon(code="DIWALI10")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(code="DIWALI10")

    assert Coupon.objects.filter(code="DIWALI10").count() == 1


# ---------------------------------------------------------------------------
# One test per database rule
# ---------------------------------------------------------------------------
def test_a_percent_of_zero_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(percent_off=ZERO)


def test_a_percent_above_one_hundred_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(percent_off=Decimal("100.01"))


def test_a_percent_coupon_needs_a_percent() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(percent_off=None)


def test_a_fixed_coupon_needs_an_amount() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(kind=Coupon.Kind.FIXED, amount_off=None)


def test_a_fixed_coupon_with_a_percent_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(kind=Coupon.Kind.FIXED, percent_off=Decimal("5.00"))


def test_a_free_delivery_coupon_with_an_amount_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(kind=Coupon.Kind.FREE_DELIVERY, amount_off=Decimal("10.00"))


def test_an_amount_of_zero_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(kind=Coupon.Kind.FIXED, amount_off=ZERO)


def test_a_negative_minimum_order_value_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(min_order_value=Decimal("-1.00"))


def test_a_total_limit_of_zero_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(total_limit=0)


def test_a_per_customer_limit_of_zero_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(per_customer_limit=0)


def test_an_end_equal_to_the_start_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(starts_at=NOW, ends_at=NOW)


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_coupon(kind="BOGUS")


# ---------------------------------------------------------------------------
# clean() shows a friendly error, field by field
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"kind": Coupon.Kind.PERCENT, "percent_off": None}, "percent_off"),
        (
            {"kind": Coupon.Kind.PERCENT, "percent_off": Decimal("10.00"), "amount_off": ZERO},
            "amount_off",
        ),
        ({"kind": Coupon.Kind.FIXED, "amount_off": None}, "amount_off"),
        (
            {"kind": Coupon.Kind.FIXED, "amount_off": Decimal("5.00"), "percent_off": ZERO},
            "percent_off",
        ),
        (
            {"kind": Coupon.Kind.FIXED, "amount_off": Decimal("5.00"), "max_discount": ZERO},
            "max_discount",
        ),
        ({"kind": Coupon.Kind.FREE_DELIVERY, "percent_off": Decimal("5.00")}, "percent_off"),
        ({"kind": Coupon.Kind.FREE_DELIVERY, "amount_off": Decimal("5.00")}, "amount_off"),
        ({"kind": Coupon.Kind.FREE_DELIVERY, "max_discount": Decimal("5.00")}, "max_discount"),
    ],
)
def test_clean_shows_a_friendly_error_for_each_wrong_combination(
    values: dict[str, object],
    field: str,
) -> None:
    coupon = Coupon(code="DIWALI10", min_order_value=ZERO, **values)

    with pytest.raises(ValidationError) as error:
        coupon.full_clean()

    assert field in error.value.message_dict


@pytest.mark.parametrize("code", ["ab", "BAD CODE", "ABC!", "TOOLONGCODE1234567890X"])
def test_clean_refuses_a_bad_code_format(code: str) -> None:
    coupon = Coupon(code=code, kind=Coupon.Kind.PERCENT, percent_off=Decimal("10.00"))

    with pytest.raises(ValidationError) as error:
        coupon.full_clean()

    assert "code" in error.value.message_dict


def test_clean_refuses_an_end_equal_to_the_start() -> None:
    coupon = Coupon(
        code="DIWALI10",
        kind=Coupon.Kind.PERCENT,
        percent_off=Decimal("10.00"),
        starts_at=NOW,
        ends_at=NOW,
    )

    with pytest.raises(ValidationError) as error:
        coupon.full_clean()

    assert "ends_at" in error.value.message_dict


def test_clean_accepts_a_good_percent_coupon() -> None:
    coupon = Coupon(
        code="DIWALI10",
        kind=Coupon.Kind.PERCENT,
        percent_off=Decimal("10.00"),
        starts_at=NOW,
        ends_at=NOW + timedelta(days=1),
    )

    # A valid coupon must pass without raising.
    coupon.full_clean()


# ---------------------------------------------------------------------------
# Rows, text and the delete guard
# ---------------------------------------------------------------------------
def test_str_shows_the_code_and_the_kind() -> None:
    coupon = make_coupon(code="DIWALI10")

    assert str(coupon) == "DIWALI10 (Percent off)"


def test_a_coupon_with_a_redemption_cannot_be_deleted() -> None:
    coupon = make_coupon()
    make_redemption(coupon)

    with pytest.raises(ProtectedError):
        coupon.delete()

    assert Coupon.objects.filter(pk=coupon.pk).exists()


def test_the_timestamps_are_set() -> None:
    coupon = make_coupon()

    assert coupon.created_at is not None
    assert coupon.updated_at is not None
