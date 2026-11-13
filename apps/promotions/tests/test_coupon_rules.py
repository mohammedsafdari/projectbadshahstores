"""Tests for the coupon rules (D81).

REVIEW CAREFULLY: money. Every expected value is a ``Decimal``.

A fixed ``NOW`` and an explicit ``now=`` argument keep every validity test off
the wall clock.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection, transaction

from apps.orders.models import Order
from apps.promotions.models import Coupon
from apps.promotions.rules import (
    NOT_COUNTING_STATUSES,
    CouponRefusal,
    evaluate_coupon,
)
from apps.promotions.tests.helpers import make_coupon, make_order, make_redemption
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
ZERO = Decimal("0.00")
FEE = Decimal("30.00")
ITEMS = Decimal("100.00")


def _evaluate(user, **overrides):
    """Evaluate our standard cart unless the test changes a part of it."""
    values: dict[str, object] = {
        "user": user,
        "item_total": ITEMS,
        "delivery_fee": FEE,
        "now": NOW,
    }
    values.update(overrides)
    return evaluate_coupon("DIWALI10", **values)


# ---------------------------------------------------------------------------
# One test per refusal reason, with its exact sentence
# ---------------------------------------------------------------------------
def test_an_anonymous_visitor_is_asked_to_sign_in() -> None:
    result = _evaluate(AnonymousUser())

    assert result.ok is False
    assert result.reason == CouponRefusal.LOGIN_REQUIRED
    assert result.message == "Please sign in to use a coupon."
    assert result.item_discount == ZERO
    assert result.delivery_discount == ZERO


def test_an_unknown_code_is_not_found() -> None:
    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.NOT_FOUND
    assert result.message == "This coupon code is not valid."


def test_a_switched_off_coupon_is_not_available() -> None:
    make_coupon(is_active=False)

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.INACTIVE
    assert result.message == "This coupon is not available right now."


def test_a_coupon_that_has_not_started_is_refused() -> None:
    make_coupon(starts_at=NOW + timedelta(minutes=1))

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.NOT_STARTED
    assert result.message == "This coupon is not active yet."


def test_an_expired_coupon_is_refused() -> None:
    make_coupon(ends_at=NOW - timedelta(seconds=1))

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.EXPIRED
    assert result.message == "This coupon has expired."


def test_an_order_below_the_minimum_names_the_amount_short() -> None:
    make_coupon(min_order_value=Decimal("200.00"))

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.BELOW_MINIMUM
    assert result.message == "Add items worth ₹100.00 more to use this coupon."


def test_a_first_order_coupon_is_refused_to_an_existing_customer() -> None:
    make_coupon(first_order_only=True)
    user = UserFactory()
    make_order(user, Order.Status.PLACED)

    result = _evaluate(user)

    assert result.reason == CouponRefusal.NOT_FIRST_ORDER
    assert result.message == "This coupon is only for your first order."


def test_a_total_limit_reached_coupon_is_fully_used() -> None:
    coupon = make_coupon(total_limit=1)
    make_redemption(coupon)

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.TOTAL_LIMIT_REACHED
    assert result.message == "This coupon has been fully used."


def test_a_customer_limit_reached_is_already_used() -> None:
    coupon = make_coupon(per_customer_limit=1)
    user = UserFactory()
    make_redemption(coupon, user=user)

    result = _evaluate(user)

    assert result.reason == CouponRefusal.PER_CUSTOMER_LIMIT_REACHED
    assert result.message == "You have already used this coupon."


def test_a_percent_that_rounds_to_zero_gives_no_benefit() -> None:
    # One paisa at 0.01% is 0.000001, which rounds to 0.00.
    make_coupon(percent_off=Decimal("0.01"))

    result = _evaluate(UserFactory(), item_total=Decimal("1.00"))

    assert result.reason == CouponRefusal.NO_BENEFIT
    assert result.message == "This coupon gives no discount on this order."


# ---------------------------------------------------------------------------
# The order of the checks: the first failure wins
# ---------------------------------------------------------------------------
def test_an_inactive_coupon_reports_the_inactive_reason_before_the_minimum() -> None:
    make_coupon(is_active=False, min_order_value=Decimal("500.00"))

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.INACTIVE


def test_a_coupon_that_has_not_started_reports_the_start_before_the_limit() -> None:
    coupon = make_coupon(starts_at=NOW + timedelta(minutes=1), total_limit=1)
    make_redemption(coupon)

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.NOT_STARTED


def test_the_first_order_check_comes_before_the_total_limit() -> None:
    coupon = make_coupon(first_order_only=True, total_limit=1)
    make_redemption(coupon)
    user = UserFactory()
    make_order(user, Order.Status.DELIVERED)

    result = _evaluate(user)

    assert result.reason == CouponRefusal.NOT_FIRST_ORDER


def test_the_total_limit_is_checked_before_the_customer_limit() -> None:
    coupon = make_coupon(total_limit=1, per_customer_limit=1)
    user = UserFactory()
    make_redemption(coupon, user=user)

    result = _evaluate(user)

    assert result.reason == CouponRefusal.TOTAL_LIMIT_REACHED


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------
def test_the_coupon_is_valid_at_its_start_time() -> None:
    make_coupon(starts_at=NOW)

    result = _evaluate(UserFactory())

    assert result.ok is True


def test_the_coupon_is_expired_at_its_end_time() -> None:
    make_coupon(ends_at=NOW)

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.EXPIRED


def test_the_minimum_is_met_exactly() -> None:
    make_coupon(min_order_value=ITEMS)

    result = _evaluate(UserFactory())

    assert result.ok is True
    assert result.item_discount == Decimal("10.00")


def test_one_paisa_below_the_minimum_names_the_amount_short() -> None:
    make_coupon(min_order_value=Decimal("100.01"))

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.BELOW_MINIMUM
    assert result.message == "Add items worth ₹0.01 more to use this coupon."


# ---------------------------------------------------------------------------
# The money
# ---------------------------------------------------------------------------
def test_a_percentage_is_rounded_half_up() -> None:
    # 15% of 99.50 is 14.925, which rounds up to 14.93.
    make_coupon(percent_off=Decimal("15.00"))

    result = _evaluate(UserFactory(), item_total=Decimal("99.50"))

    assert result.ok is True
    assert result.item_discount == Decimal("14.93")
    assert result.delivery_discount == ZERO


def test_the_maximum_discount_caps_a_percentage() -> None:
    make_coupon(percent_off=Decimal("20.00"), max_discount=Decimal("50.00"))

    result = _evaluate(UserFactory(), item_total=Decimal("1000.00"))

    assert result.item_discount == Decimal("50.00")


def test_a_fixed_amount_above_the_item_total_is_cut_to_the_total() -> None:
    make_coupon(kind=Coupon.Kind.FIXED, amount_off=Decimal("150.00"))

    result = _evaluate(UserFactory())

    assert result.ok is True
    assert result.item_discount == ITEMS
    assert result.delivery_discount == ZERO


def test_a_free_delivery_coupon_gives_the_whole_fee() -> None:
    make_coupon(kind=Coupon.Kind.FREE_DELIVERY)

    result = _evaluate(UserFactory())

    assert result.ok is True
    assert result.item_discount == ZERO
    assert result.delivery_discount == FEE


def test_a_free_delivery_coupon_with_no_fee_gives_no_benefit() -> None:
    make_coupon(kind=Coupon.Kind.FREE_DELIVERY)

    result = _evaluate(UserFactory(), delivery_fee=ZERO)

    assert result.reason == CouponRefusal.NO_BENEFIT


def test_every_amount_in_a_result_is_decimal() -> None:
    make_coupon(kind=Coupon.Kind.FREE_DELIVERY)

    result = _evaluate(UserFactory())

    assert isinstance(result.item_discount, Decimal)
    assert isinstance(result.delivery_discount, Decimal)


# ---------------------------------------------------------------------------
# First order
# ---------------------------------------------------------------------------
# The four statuses are written out here on purpose: a test list built from
# ``NOT_COUNTING_STATUSES`` would quietly shrink when the product code loses one.
NOT_COUNTING = [
    Order.Status.CANCELLED,
    Order.Status.PAYMENT_FAILED,
    Order.Status.REFUND_PENDING,
    Order.Status.REFUNDED,
]


def test_the_statuses_that_do_not_count_are_exactly_the_four_agreed() -> None:
    assert set(NOT_COUNTING_STATUSES) == set(NOT_COUNTING)


@pytest.mark.parametrize("status", NOT_COUNTING)
def test_an_order_that_does_not_count_leaves_the_customer_new(status: str) -> None:
    make_coupon(first_order_only=True)
    user = UserFactory()
    make_order(user, status)

    result = _evaluate(user)

    assert result.ok is True


@pytest.mark.parametrize(
    "status",
    [
        Order.Status.PLACED,
        Order.Status.PAYMENT_PENDING,
        Order.Status.PAID,
        Order.Status.CONFIRMED,
        Order.Status.PACKING,
        Order.Status.OUT_FOR_DELIVERY,
        Order.Status.DELIVERED,
        Order.Status.PARTIALLY_REFUNDED,
    ],
)
def test_any_order_that_counts_makes_the_customer_not_new(status: str) -> None:
    make_coupon(first_order_only=True)
    user = UserFactory()
    make_order(user, status)

    result = _evaluate(user)

    assert result.reason == CouponRefusal.NOT_FIRST_ORDER


def test_another_customers_order_does_not_make_this_customer_not_new() -> None:
    make_coupon(first_order_only=True)
    # An existing customer's delivered order must not count for a newcomer.
    make_order(UserFactory(), Order.Status.DELIVERED)
    newcomer = UserFactory()

    result = _evaluate(newcomer)

    assert result.ok is True


def test_a_first_order_coupon_below_the_minimum_names_the_amount_short() -> None:
    make_coupon(first_order_only=True, min_order_value=Decimal("200.00"))
    user = UserFactory()
    make_order(user, Order.Status.PLACED)

    result = _evaluate(user)

    # The documented order puts the minimum check before the first-order check.
    assert result.reason == CouponRefusal.BELOW_MINIMUM
    assert result.message == "Add items worth ₹100.00 more to use this coupon."


# ---------------------------------------------------------------------------
# Limits and released rows
# ---------------------------------------------------------------------------
def test_a_released_redemption_does_not_count_for_either_limit() -> None:
    coupon = make_coupon(total_limit=1, per_customer_limit=1)
    user = UserFactory()
    make_redemption(coupon, user=user, released_at=NOW)

    result = _evaluate(user)

    assert result.ok is True


def test_another_customers_redemption_does_not_count_for_the_customer_limit() -> None:
    coupon = make_coupon(per_customer_limit=1)
    make_redemption(coupon)
    user = UserFactory()

    result = _evaluate(user)

    assert result.ok is True


def test_an_empty_per_customer_limit_means_unlimited() -> None:
    coupon = make_coupon(per_customer_limit=None)
    user = UserFactory()
    make_redemption(coupon, user=user)
    make_redemption(coupon, user=user)

    result = _evaluate(user)

    assert result.ok is True


# ---------------------------------------------------------------------------
# The code as the customer types it
# ---------------------------------------------------------------------------
def test_another_coupon_with_a_different_code_is_not_found() -> None:
    # A coupon exists, but not with the code the customer typed (D81).
    make_coupon(code="WINTER20")

    result = _evaluate(UserFactory())

    assert result.reason == CouponRefusal.NOT_FOUND
    assert result.message == "This coupon code is not valid."


def test_a_code_with_spaces_and_lower_case_works() -> None:
    make_coupon(code="DIWALI10")

    result = evaluate_coupon(
        "  diwali10  ",
        user=UserFactory(),
        item_total=ITEMS,
        delivery_fee=FEE,
        now=NOW,
    )

    assert result.ok is True
    assert result.code == "DIWALI10"


@pytest.mark.parametrize("code", ["", "AB", "BAD CODE", "ABC!"])
def test_a_code_with_a_bad_format_is_not_found(code: str) -> None:
    make_coupon()

    result = evaluate_coupon(
        code,
        user=UserFactory(),
        item_total=ITEMS,
        delivery_fee=FEE,
        now=NOW,
    )

    assert result.reason == CouponRefusal.NOT_FOUND


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------
def test_for_update_reads_the_coupon_inside_a_transaction() -> None:
    make_coupon()

    # ``select_for_update()`` needs a transaction, so the call is wrapped in one.
    with transaction.atomic():
        result = _evaluate(UserFactory(), for_update=True)

    assert result.ok is True


@pytest.mark.django_db(transaction=True)
def test_a_locked_coupon_is_read_after_the_other_transaction_commits() -> None:
    coupon = make_coupon()
    user = UserFactory()
    coupon_pk = coupon.pk

    locked = threading.Event()
    reasons: list[str] = []
    errors: list[BaseException] = []

    def switch_it_off() -> None:
        """Switch the coupon off inside a transaction that holds its row lock."""
        try:
            with transaction.atomic():
                row = Coupon.objects.select_for_update().get(pk=coupon_pk)
                locked.set()  # tell the other thread the lock is held
                time.sleep(0.5)  # keep it for a moment, so the other thread must wait
                row.is_active = False
                row.save(update_fields=["is_active", "updated_at"])
            # Leaving the ``with`` block commits, which releases the lock.
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def evaluate_while_locked() -> None:
        """Evaluate: it must wait, then see the coupon switched off."""
        try:
            assert locked.wait(timeout=10)
            with transaction.atomic():
                result = _evaluate(user, for_update=True)
            reasons.append(result.reason)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    first = threading.Thread(target=switch_it_off)
    second = threading.Thread(target=evaluate_while_locked)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    assert not errors, errors
    # Without the lock the second read would have seen the old active row.
    assert reasons == [CouponRefusal.INACTIVE]
