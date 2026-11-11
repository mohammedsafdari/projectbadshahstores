"""Read-only staff queries: the order list, the pick list and the cash
reconciliation (D79).

Nothing in this module writes anything. Every function checks the
``VIEW_ORDERS`` capability first (D75) and then reads the database with a
constant number of queries, whatever the number of rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import User
from apps.orders.models import CodCollection, Order
from apps.orders.services import OrderNotFoundError
from apps.orders.staff_services import (
    StaffPermissionError,
    delivery_gate_reason,
    require_capability,
)

# The statuses of an order that is finished: it will never go out again, so
# the delivery gate has nothing to say about it.
FINISHED_STATUSES = (
    Order.Status.DELIVERED,
    Order.Status.CANCELLED,
    Order.Status.REFUNDED,
    Order.Status.PARTIALLY_REFUNDED,
)

# The statuses the pick list shows: an order that is confirmed or being
# packed still has to be picked (D79).
PICKABLE_STATUSES = (
    Order.Status.CONFIRMED,
    Order.Status.PACKING,
)


@dataclass(frozen=True)
class StaffOrderRow:
    """One row of the staff order list."""

    order: Order
    order_number: str
    status: str
    customer_name: str
    customer_phone: str
    grand_total: Decimal
    amount_due_on_delivery: Decimal
    payment_method: str
    delivery_day: date
    phone_verified: bool
    email_verified: bool
    # Why this order may not go out yet; empty when it may.
    gate_reason: str


@dataclass(frozen=True)
class PickLine:
    """One thing to pick for one order."""

    variant_id: int
    sku_code: str
    product_name: str
    pack_label: str
    quantity: Decimal


@dataclass(frozen=True)
class PickOrder:
    """One order on the pick list, with its lines."""

    order: Order
    order_number: str
    status: str
    lines: list[PickLine]


@dataclass(frozen=True)
class PickTotal:
    """How much of one variant the whole delivery day needs."""

    variant_id: int
    sku_code: str
    product_name: str
    pack_label: str
    quantity: Decimal
    order_count: int


@dataclass(frozen=True)
class PickList:
    """The pick list of one delivery day."""

    day: date
    orders: list[PickOrder]
    totals: list[PickTotal]


@dataclass(frozen=True)
class CollectorTotal:
    """What one staff member collected on one day."""

    collected_by: User
    total: Decimal
    collection_count: int


@dataclass(frozen=True)
class ReconciliationResult:
    """The cash of one day, and the orders that still owe cash."""

    day: date
    collectors: list[CollectorTotal]
    grand_total: Decimal
    unpaid_orders: list[Order]


def _row(order: Order) -> StaffOrderRow:
    """Build one list row from an order and its customer."""
    user = order.user
    return StaffOrderRow(
        order=order,
        order_number=order.order_number,
        status=order.status,
        # The frozen name of the order is the recipient, so it is what the
        # staff member reads on the delivery bag.
        customer_name=order.ship_name or user.full_name,
        customer_phone=order.ship_phone,
        grand_total=order.grand_total,
        amount_due_on_delivery=order.amount_due_on_delivery,
        payment_method=order.payment_method,
        delivery_day=order.promised_delivery_date,
        phone_verified=user.phone_verified_at is not None,
        # A customer without an email address has nothing to verify.
        email_verified=user.email_verified_at is not None or not user.email,
        gate_reason="" if order.status in FINISHED_STATUSES else delivery_gate_reason(user),
    )


def list_staff_orders(
    actor: User,
    *,
    status: str | None = None,
    delivery_day: date | None = None,
    payment_method: str | None = None,
    search: str = "",
) -> list[StaffOrderRow]:
    """The orders for the staff list, with the filters the screen offers.

    The search box takes an order number or a phone number: the customer's
    phone and the phone frozen on the order's address.
    """
    require_capability(actor, Capability.VIEW_ORDERS)
    queryset = Order.objects.select_related("user")
    if status is not None:
        if status not in Order.Status.values:
            raise ValueError(f"Unknown order status {status!r}.")
        queryset = queryset.filter(status=status)
    if payment_method is not None:
        if payment_method not in Order.PaymentMethod.values:
            raise ValueError(f"Unknown payment method {payment_method!r}.")
        queryset = queryset.filter(payment_method=payment_method)
    if delivery_day is not None:
        queryset = queryset.filter(promised_delivery_date=delivery_day)
    term = (search or "").strip()
    if term:
        queryset = queryset.filter(
            Q(order_number__icontains=term)
            | Q(user__phone__icontains=term)
            | Q(ship_phone__icontains=term)
        )
    queryset = queryset.order_by("promised_delivery_date", "order_number", "pk")
    return [_row(order) for order in queryset]


def pick_list_for_day(actor: User, day: date) -> PickList:
    """The orders to pick for one delivery day, grouped by order.

    Only confirmed and packing orders are on the list; a cancelled order is
    not picked. The orders keep the order of their order numbers, so the list
    is the same every time it is printed.
    """
    require_capability(actor, Capability.VIEW_ORDERS)
    orders = (
        Order.objects.filter(promised_delivery_date=day, status__in=PICKABLE_STATUSES)
        .prefetch_related("items")
        .order_by("order_number", "pk")
    )
    picked: list[PickOrder] = []
    totals: dict[int, PickTotal] = {}
    for order in orders:
        lines: list[PickLine] = []
        for item in order.items.all():
            lines.append(
                PickLine(
                    variant_id=item.variant_id,
                    sku_code=item.sku_code,
                    product_name=item.product_name,
                    pack_label=item.pack_label,
                    quantity=item.quantity_ordered,
                )
            )
            current = totals.get(item.variant_id)
            if current is None:
                totals[item.variant_id] = PickTotal(
                    variant_id=item.variant_id,
                    sku_code=item.sku_code,
                    product_name=item.product_name,
                    pack_label=item.pack_label,
                    quantity=item.quantity_ordered,
                    order_count=1,
                )
            else:
                totals[item.variant_id] = PickTotal(
                    variant_id=current.variant_id,
                    sku_code=current.sku_code,
                    product_name=current.product_name,
                    pack_label=current.pack_label,
                    quantity=current.quantity + item.quantity_ordered,
                    order_count=current.order_count + 1,
                )
        picked.append(
            PickOrder(
                order=order,
                order_number=order.order_number,
                status=order.status,
                lines=lines,
            )
        )
    return PickList(
        day=day,
        orders=picked,
        # Sorted by SKU so the totals block always reads the same way.
        totals=sorted(totals.values(), key=lambda total: total.sku_code),
    )


def cash_reconciliation(actor: User, day: date) -> ReconciliationResult:
    """The cash collected on one day, and the orders that still owe cash.

    The collections of the day are grouped by the staff member who took the
    money, with a subtotal each and a grand total. The unpaid list is not
    limited to the day: an order that still owes cash is listed until it is
    collected or cancelled.
    """
    # The delivery role may read the cash page by itself (D79 amendment), so
    # this one query accepts either the order right or the delivery right.
    if not (
        has_capability(actor, Capability.VIEW_ORDERS)
        or has_capability(actor, Capability.DELIVER_COD)
    ):
        label = Capability(Capability.DELIVER_COD).label
        raise StaffPermissionError(f"You do not have the right to {label.lower()}.")
    # The day is the shop's day, not the server's, so the two ends of the
    # day are worked out in the shop time zone.
    shop_zone = timezone.get_current_timezone()
    start = datetime.combine(day, time.min, tzinfo=shop_zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=shop_zone)
    rows = (
        CodCollection.objects.filter(collected_at__gte=start, collected_at__lt=end)
        .select_related("collected_by")
        .order_by("collected_by__pk", "pk")
    )
    grouped: dict[int, list[CodCollection]] = {}
    for row in rows:
        grouped.setdefault(row.collected_by_id, []).append(row)

    collectors: list[CollectorTotal] = []
    grand_total = Decimal("0.00")
    for collector_id in sorted(grouped):
        collected = grouped[collector_id]
        # sum() needs a start value, or an empty list would raise.
        total = sum((row.amount for row in collected), Decimal("0.00"))
        collectors.append(
            CollectorTotal(
                collected_by=collected[0].collected_by,
                total=total,
                collection_count=len(collected),
            )
        )
        grand_total += total

    unpaid = list(
        Order.objects.filter(amount_due_on_delivery__gt=0)
        .exclude(status__in=FINISHED_STATUSES)
        .exclude(cod_collection__isnull=False)
        .select_related("user")
        .order_by("promised_delivery_date", "order_number", "pk")
    )
    return ReconciliationResult(
        day=day,
        collectors=collectors,
        grand_total=grand_total,
        unpaid_orders=unpaid,
    )


def get_staff_order(
    actor: User,
    order_number: str,
    *,
    capability: str = Capability.VIEW_ORDERS,
) -> Order:
    """One order by its printed number, behind the caller's capability.

    The number contains slashes, so the caller uses a ``path`` route and this
    lookup by the exact stored value. The order arrives with its lines, its
    status log and its payments already fetched.
    """
    require_capability(actor, capability)
    order = (
        Order.objects.filter(order_number=order_number)
        .select_related("user")
        .prefetch_related("items", "status_logs", "payments")
        .first()
    )
    if order is None:
        raise OrderNotFoundError("Order not found.")
    return order


def delivery_orders_for_day(actor: User, day: date) -> list[Order]:
    """Every order out for delivery today or earlier, oldest promise first.

    A future delivery day is never shown, so an overdue order stays on the
    list until it is delivered (Slice 42c).
    """
    require_capability(actor, Capability.DELIVER_COD)
    return list(
        Order.objects.filter(
            status=Order.Status.OUT_FOR_DELIVERY,
            promised_delivery_date__lte=day,
        )
        .select_related("user")
        .order_by("promised_delivery_date", "order_number", "pk")
    )
