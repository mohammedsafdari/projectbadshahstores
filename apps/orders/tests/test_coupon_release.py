"""Tests for giving a cancelled order's coupon use back (D82)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from apps.orders.models import Order
from apps.orders.services import (
    cancel_order_by_customer,
    mark_order_payment_failed,
    transition_order,
)
from apps.orders.tests.checkout_helpers import NOW, clear_settings, fill_cart
from apps.orders.tests.coupon_helpers import place, ready
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.invoice_helpers import sent_out_order
from apps.orders.tests.staff_helpers import staff
from apps.promotions.models import CouponRedemption
from apps.promotions.services import release_redemption
from apps.promotions.tests.helpers import make_coupon, make_redemption

pytestmark = pytest.mark.django_db

LATER = NOW + timedelta(minutes=10)


@pytest.fixture(autouse=True)
def _empty_cache():
    """Leave no cached settings behind for the next test."""
    yield
    clear_settings()


def _placed_with_coupon():
    """One real cash order that used the default coupon."""
    user, address_row, row = ready()
    make_coupon()
    order = place(user, address_row, coupon_code="DIWALI10").order
    return user, address_row, row, order


def _pending_online_order() -> tuple[Order, CouponRedemption]:
    """An online order waiting for its payment, with a coupon use on it."""
    order = OrderFactory(
        status=Order.Status.PAYMENT_PENDING,
        payment_method=Order.PaymentMethod.ONLINE,
        online_amount=Decimal("230.00"),
        amount_due_on_delivery=Decimal("0.00"),
    )
    coupon = make_coupon()
    redemption = make_redemption(coupon, user=order.user, order=order)
    return order, redemption


def test_a_customer_cancel_releases_the_use() -> None:
    user, _address, _row, order = _placed_with_coupon()

    cancel_order_by_customer(user, order.pk, reason="Changed my mind", now=LATER)

    assert CouponRedemption.objects.get(order=order).released_at == LATER


def test_a_staff_cancel_before_send_out_releases_the_use() -> None:
    _user, _address, _row, order = _placed_with_coupon()

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff(),
        reason="Damaged in the shop",
        now=LATER,
    )

    assert CouponRedemption.objects.get(order=order).released_at == LATER


def test_a_cancel_after_send_out_keeps_the_use_counted() -> None:
    order, invoice = sent_out_order()
    coupon = make_coupon()
    make_redemption(coupon, user=order.user, order=order)

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=staff(),
        reason="Customer refused the delivery",
        now=LATER,
    )

    # The goods left the shop, so the customer already had the discount.
    assert invoice.pk is not None
    assert CouponRedemption.objects.get(order=order).released_at is None


def test_a_failed_payment_keeps_the_use_counted() -> None:
    order, _redemption = _pending_online_order()

    mark_order_payment_failed(order.pk, reason="Declined", now=LATER)

    order.refresh_from_db()
    assert order.status == Order.Status.PAYMENT_FAILED
    # D77 keeps the stock for a retry, so the coupon use stays with the order.
    assert CouponRedemption.objects.get(order=order).released_at is None


def test_a_failed_payment_order_that_is_cancelled_releases_the_use() -> None:
    order, _redemption = _pending_online_order()
    mark_order_payment_failed(order.pk, reason="Declined", now=LATER)

    transition_order(
        order.pk,
        Order.Status.CANCELLED,
        actor=None,
        reason="The customer gave up",
        now=LATER + timedelta(minutes=5),
    )

    assert CouponRedemption.objects.get(order=order).released_at == LATER + timedelta(minutes=5)


def test_the_same_customer_can_use_the_coupon_again_after_a_release() -> None:
    user, address_row, row, order = _placed_with_coupon()
    cancel_order_by_customer(user, order.pk, now=LATER)
    fill_cart(user, row)

    again = place(user, address_row, coupon_code="DIWALI10").order

    assert again.pk != order.pk
    assert again.discount_total == Decimal("20.00")
    assert CouponRedemption.objects.filter(released_at__isnull=True).count() == 1


def test_a_second_release_changes_nothing() -> None:
    user, _address, _row, order = _placed_with_coupon()
    cancel_order_by_customer(user, order.pk, now=LATER)

    release_redemption(order, now=LATER + timedelta(hours=1))

    assert CouponRedemption.objects.get(order=order).released_at == LATER


def test_cancelling_an_order_without_a_coupon_writes_nothing() -> None:
    user, address_row, _row = ready()

    order = place(user, address_row).order
    cancel_order_by_customer(user, order.pk, now=LATER)

    assert CouponRedemption.objects.count() == 0


def test_a_released_row_keeps_its_amounts() -> None:
    user, _address, _row, order = _placed_with_coupon()

    cancel_order_by_customer(user, order.pk, now=LATER)

    redemption = CouponRedemption.objects.get(order=order)
    assert redemption.released_at is not None
    assert redemption.discount_amount == Decimal("20.00")
    assert redemption.delivery_discount_amount == Decimal("0.00")


def test_a_released_row_no_longer_counts_against_the_total_limit() -> None:
    user, _address, _row, order = _placed_with_coupon()
    cancel_order_by_customer(user, order.pk, now=LATER)
    other_user, other_address, _other_row = ready()

    other = place(other_user, other_address, coupon_code="DIWALI10").order

    assert other.discount_total == Decimal("20.00")


def test_the_release_returns_the_row() -> None:
    _user, _address, _row, order = _placed_with_coupon()

    redemption = release_redemption(order, now=LATER)

    assert redemption is not None
    assert redemption.released_at == LATER


def test_the_release_returns_none_without_a_redemption() -> None:
    order = OrderFactory()

    assert release_redemption(order, now=LATER) is None


def test_a_cancel_at_packing_releases_the_use() -> None:
    coupon = make_coupon()
    order = OrderFactory(status=Order.Status.PACKING)
    make_redemption(coupon, user=order.user, order=order)

    transition_order(
        order.pk, Order.Status.CANCELLED, actor=staff(), reason="Out of stock", now=LATER
    )

    assert CouponRedemption.objects.get(order=order).released_at == LATER


def test_a_failing_cancel_releases_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _user, _address, _row, order = _placed_with_coupon()

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("The cancel broke after the release.")

    # The release runs before the credit reversal, so this proves that both
    # writes sit in the one transaction and a failed cancel keeps the use.
    monkeypatch.setattr("apps.orders.services.reverse_order_charge", _boom)

    with pytest.raises(RuntimeError):
        transition_order(order.pk, Order.Status.CANCELLED, actor=staff(), reason="Test", now=LATER)

    order.refresh_from_db()
    assert order.status == Order.Status.CONFIRMED
    assert CouponRedemption.objects.get(order=order).released_at is None
