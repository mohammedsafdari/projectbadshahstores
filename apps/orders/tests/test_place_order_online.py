"""Tests for placing an order whose remainder is paid online (D77)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.cart.models import CartItem
from apps.credit.models import CreditAccount, CreditEntry
from apps.inventory.models import StockReservation
from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import (
    InvalidPaymentMethodError,
    OnlinePaymentUnavailableError,
    place_order,
)
from apps.payments.tests.helpers import (
    NOW,
    address,
    clear_settings,
    credit_account,
    fill_cart,
    place_online,
    ready_customer,
    reset_settings,
    serve,
    set_settings,
    variant,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _online_shop(settings):
    """A clean settings row and the fake provider for every test."""
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    yield
    clear_settings()


def test_an_online_remainder_creates_a_payment_pending_order() -> None:
    user, address_row, _row = ready_customer()

    order = place_online(user, address_row).order

    assert order.status == Order.Status.PAYMENT_PENDING
    assert order.payment_method == Order.PaymentMethod.ONLINE
    assert order.credit_applied == Decimal("0.00")
    assert order.online_amount == Decimal("230.00")
    assert order.amount_due_on_delivery == Decimal("0.00")


def test_the_khata_is_used_first_and_online_pays_only_the_rest() -> None:
    user, address_row, _row = ready_customer(credit_limit="100.00")

    order = place_online(user, address_row, payment_method=Order.PaymentMethod.CREDIT).order

    assert order.payment_method == Order.PaymentMethod.CREDIT
    assert order.credit_applied == Decimal("100.00")
    assert order.online_amount == Decimal("130.00")
    assert order.amount_due_on_delivery == Decimal("0.00")
    assert CreditEntry.objects.filter(order=order).count() == 1
    assert CreditAccount.objects.get(user=user).balance == Decimal("100.00")


def test_a_khata_that_covers_everything_leaves_no_online_part() -> None:
    user, address_row, _row = ready_customer(credit_limit="1000.00")

    order = place_online(user, address_row, payment_method=Order.PaymentMethod.CREDIT).order

    assert order.online_amount == Decimal("0.00")
    assert order.amount_due_on_delivery == Decimal("0.00")
    assert order.payment_method == Order.PaymentMethod.CREDIT
    assert order.status == Order.Status.CONFIRMED


def test_an_online_order_keeps_a_checkout_hold_that_expires() -> None:
    user, address_row, _row = ready_customer()

    order = place_online(user, address_row).order

    hold = StockReservation.objects.get(checkout_group=order.checkout_group)
    assert hold.status == StockReservation.Status.ACTIVE
    assert hold.kind == StockReservation.Kind.CHECKOUT_PENDING_PAYMENT
    assert hold.expires_at is not None


def test_an_online_order_still_holds_the_whole_quantity() -> None:
    user, address_row, _row = ready_customer()

    order = place_online(user, address_row).order

    hold = StockReservation.objects.get(checkout_group=order.checkout_group)
    assert hold.quantity == Decimal("2.000")


def test_an_online_order_clears_the_cart() -> None:
    user, address_row, _row = ready_customer()

    place_online(user, address_row)

    assert CartItem.objects.filter(cart__user=user).count() == 0


def test_no_order_notice_is_sent_before_the_payment_is_captured(
    django_capture_on_commit_callbacks,
) -> None:
    user, address_row, _row = ready_customer()

    with (
        patch("apps.orders.services.notify_order_event") as notifier,
        django_capture_on_commit_callbacks(execute=True),
    ):
        place_online(user, address_row)

    notifier.assert_not_called()


def test_no_gateway_refuses_the_online_choice_and_writes_nothing(settings) -> None:
    settings.PAYMENT_PROVIDER = ""
    user, address_row, row = ready_customer()

    with pytest.raises(OnlinePaymentUnavailableError):
        place_online(user, address_row)

    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0
    assert CartItem.objects.filter(cart__user=user).count() == 1
    assert CartItem.objects.get(cart__user=user).variant_id == row.pk


def test_an_unknown_remainder_method_is_refused() -> None:
    user, address_row, _row = ready_customer()

    with pytest.raises(InvalidPaymentMethodError):
        place_order(
            user=user,
            address_id=address_row.pk,
            payment_method=Order.PaymentMethod.COD,
            remainder_method=Order.PaymentMethod.CREDIT,
            checkout_group=uuid4(),
            now=NOW,
        )

    assert Order.objects.count() == 0


def test_online_payment_is_allowed_above_the_cash_cap() -> None:
    user, address_row, _row = ready_customer()
    set_settings(cod_cap=Decimal("30.00"))

    order = place_online(user, address_row).order

    assert order.online_amount == Decimal("230.00")
    assert order.amount_due_on_delivery == Decimal("0.00")


def test_the_status_log_records_the_wait_for_payment() -> None:
    user, address_row, _row = ready_customer()

    order = place_online(user, address_row).order

    logs = list(OrderStatusLog.objects.filter(order=order).order_by("pk"))
    assert [log.to_status for log in logs] == [
        Order.Status.PLACED,
        Order.Status.PAYMENT_PENDING,
    ]


def test_a_repeated_checkout_group_returns_the_same_online_order() -> None:
    user, address_row, _row = ready_customer()
    group = uuid4()

    first = place_online(user, address_row, group=group)
    second = place_online(user, address_row, group=group)

    assert second.created is False
    assert second.order.pk == first.order.pk
    assert StockReservation.objects.count() == 1


def test_a_blank_remainder_still_means_cash_on_delivery() -> None:
    user, address_row, _row = ready_customer()

    order = place_order(
        user=user,
        address_id=address_row.pk,
        payment_method=Order.PaymentMethod.COD,
        checkout_group=uuid4(),
        now=NOW,
    ).order

    assert order.online_amount == Decimal("0.00")
    assert order.payment_method == Order.PaymentMethod.COD
    assert order.status == Order.Status.CONFIRMED


def test_a_new_customer_without_credit_can_still_pay_online() -> None:
    user = UserFactory()
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    credit_account(user, limit="0.00", active=False)

    order = place_online(user, address_row).order

    assert order.credit_applied == Decimal("0.00")
    assert order.online_amount == Decimal("230.00")
