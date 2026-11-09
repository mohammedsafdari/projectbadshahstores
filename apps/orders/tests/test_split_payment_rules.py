"""The split-payment rules of D74, tested through the real placement service.

The cart is two packets of 100.00 plus 30.00 delivery, so the order total is
230.00. A credit limit below that leaves a cash part at the door.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from apps.cart.models import CartItem
from apps.credit.models import CreditAccount, CreditEntry
from apps.inventory.models import StockReservation
from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import OrderCreditUnavailableError
from apps.orders.tests.checkout_helpers import (
    address,
    clear_settings,
    credit_user,
    fill_cart,
    place_cod,
    place_credit,
    reset_settings,
    serve,
    set_settings,
    variant,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _ready(limit: str = "1000.00", balance: str = "0.00", active: bool = True):
    """One credit customer with a served address, stock and a ready cart."""
    user = credit_user(limit=limit, balance=balance, active=active)
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    return user, address_row


def test_cod_order_has_credit_applied_zero_and_amount_due_equal_to_the_total() -> None:
    user, address_row = _ready()

    order = place_cod(user, address_row).order

    assert order.payment_method == Order.PaymentMethod.COD
    assert order.credit_applied == Decimal("0.00")
    assert order.amount_due_on_delivery == order.grand_total == Decimal("230.00")
    assert CreditEntry.objects.count() == 0
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")


def test_a_split_order_still_reserves_stock_for_the_whole_quantity() -> None:
    # The named "fully covered" test duplicated an existing full-credit test;
    # this edge proves the cash part does not reduce the stock hold.
    user, address_row = _ready(limit="100.00")

    order = place_credit(user, address_row).order

    holds = list(StockReservation.objects.filter(checkout_group=order.checkout_group))
    assert len(holds) == 1
    assert holds[0].quantity == Decimal("2.000")
    assert holds[0].kind == StockReservation.Kind.HELD_FOR_FULFILMENT
    assert holds[0].status == StockReservation.Status.ACTIVE


def test_partly_covered_order_leaves_the_balance_equal_to_the_credit_part() -> None:
    user, address_row = _ready(limit="100.00")

    order = place_credit(user, address_row).order

    assert order.credit_applied == Decimal("100.00")
    assert order.amount_due_on_delivery == Decimal("130.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("100.00")


def test_suspended_account_is_refused_and_writes_nothing() -> None:
    user, address_row = _ready(limit="1000.00", active=False)

    with pytest.raises(OrderCreditUnavailableError):
        place_credit(user, address_row)

    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0
    assert CreditEntry.objects.count() == 0
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")
    assert CartItem.objects.filter(cart__user=user).count() == 1


def test_customer_without_an_account_is_refused_and_writes_nothing() -> None:
    # This customer never had a khata, unlike the named test that repeated the
    # existing "no account" refusal; the stronger check is that nothing at all
    # was written and the cart survives.
    user = UserFactory()
    serve()
    address_row = address(user)
    fill_cart(user, variant())

    with pytest.raises(OrderCreditUnavailableError):
        place_credit(user, address_row)

    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0
    assert CreditEntry.objects.count() == 0
    assert CreditAccount.objects.count() == 0
    assert CartItem.objects.filter(cart__user=user).count() == 1


def test_credit_applied_follows_the_available_credit_at_placing_time() -> None:
    # The limit is 300.00 but 200.00 is already owed, so only 100.00 is free.
    user, address_row = _ready(limit="300.00", balance="200.00")

    order = place_credit(user, address_row).order

    assert order.credit_applied == Decimal("100.00")
    assert order.amount_due_on_delivery == Decimal("130.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("300.00")


def test_repeated_checkout_group_returns_the_same_split_order_without_a_second_charge() -> None:
    user, address_row = _ready(limit="100.00")
    group = uuid4()

    first = place_credit(user, address_row, group=group)
    second = place_credit(user, address_row, group=group)

    assert second.created is False
    assert second.order.pk == first.order.pk
    assert CreditEntry.objects.filter(order=first.order).count() == 1
    assert CreditAccount.objects.get(user=user).balance == Decimal("100.00")


def test_split_order_status_logs_record_the_customer_as_actor() -> None:
    # The named logging test duplicated the existing full-credit log test; this
    # edge checks the actor on both rows, which a split order must preserve.
    user, address_row = _ready(limit="100.00")

    order = place_credit(user, address_row).order

    logs = list(OrderStatusLog.objects.filter(order=order).order_by("pk"))
    assert [log.to_status for log in logs] == [Order.Status.PLACED, Order.Status.CONFIRMED]
    assert [log.actor_id for log in logs] == [user.pk, user.pk]


def test_cod_cap_is_not_applied_to_the_credit_part_of_a_split_order() -> None:
    # The named test duplicated an existing "cap ignored" test; this edge has a
    # credit part above the cap and a cash part exactly at it, which is allowed.
    user, address_row = _ready(limit="200.00")
    set_settings(cod_cap=Decimal("30.00"))

    order = place_credit(user, address_row).order

    assert order.payment_method == Order.PaymentMethod.CREDIT
    assert order.credit_applied == Decimal("200.00")
    assert order.amount_due_on_delivery == Decimal("30.00")
    assert Order.objects.count() == 1
