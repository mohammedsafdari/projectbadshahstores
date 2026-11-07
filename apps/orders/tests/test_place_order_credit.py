"""Tests for placing an order on credit (D68)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from apps.cart.models import CartItem
from apps.core.models import NumberSeriesCounter
from apps.credit.models import CreditAccount, CreditEntry
from apps.inventory.models import StockReservation
from apps.orders.models import Order, OrderStatusLog
from apps.orders.services import (
    CodLimitExceededError,
    InvalidPaymentMethodError,
    InvalidTransitionError,
    OrderCreditLimitExceededError,
    OrderCreditUnavailableError,
    place_order,
    transition_order,
)
from apps.orders.state import CONFIRMED, PAYMENT_PENDING, PLACED
from apps.orders.tests.checkout_helpers import (
    NOW,
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
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


def _ready(limit: str = "1000.00", **account: object):
    """One credit customer with a served address, stock and a ready cart."""
    user = credit_user(limit=limit, **account)
    serve()
    address_row = address(user)
    fill_cart(user, variant())
    return user, address_row


def test_credit_order_is_created_and_confirmed() -> None:
    user, address_row = _ready()

    result = place_credit(user, address_row)

    assert result.created is True
    assert result.order.status == CONFIRMED
    assert result.order.payment_method == Order.PaymentMethod.CREDIT
    assert Order.objects.count() == 1


def test_credit_order_writes_one_order_entry_for_the_grand_total() -> None:
    user, address_row = _ready()

    order = place_credit(user, address_row).order

    entries = list(CreditEntry.objects.filter(order=order))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.entry_type == CreditEntry.EntryType.ORDER
    assert entry.amount == Decimal("230.00")
    assert entry.balance_after == Decimal("230.00")
    assert entry.account.user_id == user.pk


def test_credit_order_raises_the_balance_by_the_grand_total() -> None:
    user, address_row = _ready()

    place_credit(user, address_row)

    account = CreditAccount.objects.get(user=user)
    assert account.balance == Decimal("230.00")


def test_credit_order_has_nothing_due_on_delivery() -> None:
    user, address_row = _ready()

    order = place_credit(user, address_row).order

    assert order.grand_total == Decimal("230.00")
    assert order.amount_due_on_delivery == Decimal("0.00")


def test_cod_order_has_the_grand_total_due_on_delivery() -> None:
    user, address_row = _ready()

    order = place_cod(user, address_row).order

    assert order.amount_due_on_delivery == Decimal("230.00")


def test_credit_order_ignores_the_cod_cap() -> None:
    user, address_row = _ready()
    set_settings(cod_cap=Decimal("100.00"))

    order = place_credit(user, address_row).order

    assert order.grand_total == Decimal("230.00")
    assert order.payment_method == Order.PaymentMethod.CREDIT


def test_cod_order_above_the_cap_is_still_refused() -> None:
    user, address_row = _ready()
    set_settings(cod_cap=Decimal("100.00"))

    with pytest.raises(CodLimitExceededError):
        place_cod(user, address_row)

    assert Order.objects.count() == 0


def test_credit_order_exactly_at_the_available_credit_is_allowed() -> None:
    user, address_row = _ready(limit="230.00")

    order = place_credit(user, address_row).order

    assert order.grand_total == Decimal("230.00")
    assert CreditAccount.objects.get(user=user).balance == Decimal("230.00")


def test_credit_order_one_paisa_above_the_available_credit_is_refused() -> None:
    user, address_row = _ready(limit="229.99")

    with pytest.raises(OrderCreditLimitExceededError):
        place_credit(user, address_row)

    assert Order.objects.count() == 0


def test_refused_credit_order_writes_no_order_no_reservation_no_entry_and_keeps_the_cart() -> None:
    user, address_row = _ready(limit="100.00")

    with pytest.raises(OrderCreditLimitExceededError):
        place_credit(user, address_row)

    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0
    assert CreditEntry.objects.count() == 0
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")
    assert CartItem.objects.filter(cart__user=user).count() == 1


def test_refused_credit_order_does_not_use_an_order_number() -> None:
    user, address_row = _ready(limit="100.00")

    with pytest.raises(OrderCreditLimitExceededError):
        place_credit(user, address_row)

    assert NumberSeriesCounter.objects.count() == 0


def test_user_without_an_account_cannot_order_on_credit() -> None:
    user = UserFactory()
    serve()
    address_row = address(user)
    fill_cart(user, variant())

    with pytest.raises(OrderCreditUnavailableError):
        place_credit(user, address_row)

    assert Order.objects.count() == 0
    assert CreditEntry.objects.count() == 0


def test_suspended_account_cannot_order_on_credit() -> None:
    user, address_row = _ready(limit="1000.00", active=False)

    with pytest.raises(OrderCreditUnavailableError):
        place_credit(user, address_row)

    assert Order.objects.count() == 0


def test_unknown_payment_method_is_refused() -> None:
    user = credit_user()
    serve()
    address(user)
    fill_cart(user, variant())

    # A missing address id proves the method is checked before any read.
    with pytest.raises(InvalidPaymentMethodError):
        place_order(
            user=user,
            address_id=999999,
            payment_method="ONLINE",
            checkout_group=uuid4(),
            now=NOW,
        )

    assert Order.objects.count() == 0
    assert StockReservation.objects.count() == 0


def test_credit_order_still_holds_stock_for_fulfilment() -> None:
    user, address_row = _ready()

    order = place_credit(user, address_row).order

    holds = list(StockReservation.objects.filter(checkout_group=order.checkout_group))
    assert len(holds) == 1
    assert holds[0].quantity == Decimal("2.000")
    assert holds[0].kind == StockReservation.Kind.HELD_FOR_FULFILMENT
    assert holds[0].status == StockReservation.Status.ACTIVE


def test_second_call_with_the_same_group_charges_once() -> None:
    user, address_row = _ready()
    group = uuid4()

    first = place_credit(user, address_row, group=group)
    second = place_credit(user, address_row, group=group)

    assert second.created is False
    assert second.order.pk == first.order.pk
    assert CreditEntry.objects.filter(order=first.order).count() == 1
    assert CreditAccount.objects.get(user=user).balance == Decimal("230.00")


def test_credit_order_status_log_is_placed_then_confirmed() -> None:
    user, address_row = _ready()

    order = place_credit(user, address_row).order

    logs = list(OrderStatusLog.objects.filter(order=order).order_by("pk"))
    assert [log.from_status for log in logs] == ["", PLACED]
    assert [log.to_status for log in logs] == [PLACED, CONFIRMED]


def test_credit_order_cannot_move_to_a_payment_status() -> None:
    user = credit_user()
    order = OrderFactory(
        user=user,
        status=PLACED,
        payment_method=Order.PaymentMethod.CREDIT,
    )

    with pytest.raises(InvalidTransitionError, match="COD or credit"):
        transition_order(order.pk, PAYMENT_PENDING, actor=user, now=NOW)

    order.refresh_from_db()
    assert order.status == PLACED


def test_cod_order_writes_no_credit_entry_even_for_a_credit_customer() -> None:
    user, address_row = _ready()

    order = place_cod(user, address_row).order

    assert order.payment_method == Order.PaymentMethod.COD
    assert CreditEntry.objects.count() == 0
    assert CreditAccount.objects.get(user=user).balance == Decimal("0.00")
