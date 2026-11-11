"""Shared setup for the staff order tests (D79).

These helpers build the data the staff services work on: a staff account
with a role, a variant that is really in stock, a stock hold and an order
with one line. They test nothing by themselves.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.inventory.models import StockReservation
from apps.inventory.services import receive_stock
from apps.inventory.tests.factories import StockReservationFactory
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from tests.factories import UserFactory

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=IST)
DELIVERY_DAY = date(2026, 11, 7)
OTHER_DAY = date(2026, 11, 8)


def staff(role: str = User.ROLE_OWNER) -> User:
    """A staff account with this role."""
    return UserFactory(is_staff=True, role=role)


def stocked_variant(stock: str = "10.000") -> ProductVariant:
    """One packet variant that is really in stock."""
    row = ProductVariantFactory(
        is_sold_loose=False,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("1"),
        selling_price=Decimal("95.00"),
        mrp=Decimal("120.00"),
    )
    receive_stock(variant=row, quantity=Decimal(stock))
    return row


def hold_for(
    variant: ProductVariant,
    customer: User,
    *,
    group: UUID,
    quantity: str = "2",
    kind: str = StockReservation.Kind.HELD_FOR_FULFILMENT,
    expires_at: datetime | None = None,
) -> StockReservation:
    """One stock hold of a variant for a checkout group."""
    return StockReservationFactory(
        variant=variant,
        user=customer,
        quantity=Decimal(quantity),
        kind=kind,
        checkout_group=group,
        expires_at=expires_at,
    )


def order_with_hold(
    *,
    customer: User | None = None,
    status: str = Order.Status.PACKING,
    quantity: str = "2",
    delivery_day: date = DELIVERY_DAY,
    **order_fields: object,
) -> tuple[Order, ProductVariant, StockReservation]:
    """An order with one line, real stock and a matching hold."""
    user = customer if customer is not None else UserFactory()
    row = stocked_variant()
    group = uuid4()
    hold = hold_for(row, user, group=group, quantity=quantity)
    order = OrderFactory(
        user=user,
        status=status,
        checkout_group=group,
        promised_delivery_date=delivery_day,
        **order_fields,
    )
    OrderItemFactory(
        order=order,
        variant=row,
        quantity_ordered=Decimal(quantity),
    )
    return order, row, hold


def expired_hold_order() -> tuple[Order, ProductVariant, StockReservation]:
    """A packing order whose hold is an expired checkout hold."""
    customer = UserFactory()
    row = stocked_variant()
    group = uuid4()
    hold = hold_for(
        row,
        customer,
        group=group,
        quantity="2",
        kind=StockReservation.Kind.CHECKOUT_PENDING_PAYMENT,
        expires_at=NOW - timedelta(minutes=1),
    )
    order = OrderFactory(
        user=customer,
        status=Order.Status.PACKING,
        checkout_group=group,
    )
    OrderItemFactory(order=order, variant=row, quantity_ordered=Decimal("2"))
    return order, row, hold
