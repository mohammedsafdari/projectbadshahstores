"""Shared setup for the online-payment tests (D77).

The order and cart setup helpers come from ``apps.orders.tests.checkout_helpers``
so a payment test builds a real order through ``place_order`` exactly the way
the checkout page does. This module adds only the online-payment pieces.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from apps.orders.models import Order
from apps.orders.services import PlacedOrder, place_order
from apps.orders.tests.checkout_helpers import (  # noqa: F401 - re-exported for tests
    NOW,
    address,
    clear_settings,
    credit_account,
    credit_user,
    fill_cart,
    reset_settings,
    serve,
    set_settings,
    variant,
)
from apps.orders.tests.factories import OrderFactory
from apps.payments.gateway import PAYMENT_CAPTURED, GatewayEvent
from apps.payments.models import Payment
from apps.payments.services import StartedPayment, start_online_payment
from tests.factories import UserFactory


def ready_customer(*, credit_limit: str | None = None, stock: str = "10.000"):
    """A customer with a served address, stock and two packets in the cart."""
    user = UserFactory()
    if credit_limit is not None:
        credit_account(user, limit=credit_limit)
    serve()
    address_row = address(user)
    row = variant(stock=stock)
    fill_cart(user, row)
    return user, address_row, row


def place_online(
    user,
    address_row,
    *,
    payment_method: str = Order.PaymentMethod.COD,
    group=None,
    now=NOW,
) -> PlacedOrder:
    """Place an order whose remainder is paid online, through the real service."""
    return place_order(
        user=user,
        address_id=address_row.pk,
        payment_method=payment_method,
        remainder_method=Order.PaymentMethod.ONLINE,
        checkout_group=group if group is not None else uuid4(),
        now=now,
    )


def online_order(*, credit_limit: str | None = None):
    """A real 230.00 order with an online part, its customer and its variant."""
    user, address_row, row = ready_customer(credit_limit=credit_limit)
    method = Order.PaymentMethod.CREDIT if credit_limit is not None else Order.PaymentMethod.COD
    placed = place_online(user, address_row, payment_method=method)
    return user, placed.order, row


def factory_online_order(*, status: str = Order.Status.PAYMENT_PENDING, **overrides) -> Order:
    """A bare order row with an online part, for tests that only start a payment."""
    values = {
        "payment_method": Order.PaymentMethod.ONLINE,
        "status": status,
        "online_amount": Decimal("230.00"),
        "amount_due_on_delivery": Decimal("0.00"),
    }
    values.update(overrides)
    return OrderFactory(**values)


def start_payment(order: Order) -> StartedPayment:
    """Start the configured gateway's payment for this order at NOW."""
    return start_online_payment(order.user, order.order_number, now=NOW)


def event_for(
    payment: Payment,
    *,
    kind: str = PAYMENT_CAPTURED,
    amount: Decimal | None = None,
    event_id: str = "evt_1",
    payment_id: str = "pay_1",
    method: str = "upi",
) -> GatewayEvent:
    """A neutral gateway event for this stored payment."""
    return GatewayEvent(
        event_id=event_id,
        kind=kind,
        gateway_order_id=payment.gateway_order_id,
        gateway_payment_id=payment_id,
        amount=payment.amount if amount is None else amount,
        method=method,
        safe={"event": kind},
    )
