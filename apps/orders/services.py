"""Orders business logic: placement, transitions and small read helpers (D64).

Every function that changes an order runs in one ``transaction.atomic()``,
locks the row it changes with ``select_for_update()`` and writes each row with
``save(update_fields=[..., "updated_at"])``. Queryset ``update()`` is never
used, so no write skips the model and ``updated_at`` always moves.

The lock order in ``place_order`` is fixed (D67): the user row, then the
variants in ascending ``pk`` order (inside ``reserve_stock``), then the order
rows, and the credit account row LAST (inside ``charge_order``). No other
function locks in a different order. Messages are registered with
``transaction.on_commit`` and never block an order (D69).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.accounts.models import Address, User
from apps.accounts.services import AddressNotFound, get_address
from apps.cart.services import STATUS_OK, CartSummary, build_cart, clear_cart
from apps.cart.stores import DbCartStore
from apps.catalog.models import ProductVariant
from apps.core.numbering import next_number
from apps.core.services import get_shop_settings
from apps.credit.models import CreditAccount
from apps.credit.services import (
    CreditLimitExceededError,
    CreditNotAvailableError,
    available_credit,
    charge_order,
    reverse_order_charge,
)
from apps.delivery.services import address_is_served, get_delivery_promise
from apps.inventory.reservations import ReservationLine, release_group, reserve_stock
from apps.notifications.messages import (
    EVENT_CANCELLED,
    EVENT_DELIVERED,
    EVENT_OUT_FOR_DELIVERY,
    EVENT_PLACED,
    EVENT_STAFF_NEW_ORDER,
)
from apps.notifications.order_notices import notify_order_event
from apps.orders.models import Order, OrderItem, OrderStatusLog
from apps.orders.state import (
    CANCELLED,
    COD_ONLY_BLOCKED,
    DELIVERED,
    OUT_FOR_DELIVERY,
    can_transition,
)

# The status moves that tell the customer something (D69). Every other move,
# including CONFIRMED and PACKING, sends nothing.
STATUS_EVENTS: dict[str, str] = {
    CANCELLED: EVENT_CANCELLED,
    OUT_FOR_DELIVERY: EVENT_OUT_FOR_DELIVERY,
    DELIVERED: EVENT_DELIVERED,
}

# A customer may cancel only before packing starts (D70, Slice 38d). The cancel
# page and the service both read this one rule, so they cannot disagree.
CUSTOMER_CANCELLABLE = (Order.Status.PLACED, Order.Status.CONFIRMED)


class OrderError(ValueError):
    """Base error for every order service refusal."""


class OrderNotFoundError(OrderError):
    """No order matches the id (or the id belongs to another customer)."""


class InvalidTransitionError(OrderError):
    """The requested status move is unknown, illegal or blocked for COD or credit."""


class CancelReasonRequiredError(OrderError):
    """A staff cancellation must say why."""


class CheckoutGroupInUseError(OrderError):
    """The checkout group already belongs to another customer's order."""


class OrderAddressNotFoundError(OrderError):
    """The address does not exist for this customer, or it is archived."""


class AddressNotServedError(OrderError):
    """The delivery pin code is not in an active delivery zone."""


class EmptyCartError(OrderError):
    """There is nothing to order."""


class CartNotReadyError(OrderError):
    """The cart has problem lines or is below the minimum order value."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class CodLimitExceededError(OrderError):
    """The order total is above the shop-wide COD cap."""


class InvalidPaymentMethodError(OrderError):
    """The payment method is not COD or CREDIT."""


class OrderCreditUnavailableError(OrderError):
    """The customer has no active credit account (D68)."""


class OrderCreditLimitExceededError(OrderError):
    """The order total is above the customer's available credit (D68)."""


class CustomerNotVerifiedError(OrderError):
    """The customer has not verified the phone or the email yet (D71)."""


@dataclass(frozen=True)
class PlacedOrder:
    """What ``place_order`` returns: the order and whether it was just created."""

    order: Order
    created: bool


def _register_notices(order_id: int, events: tuple[str, ...]) -> None:
    """Send these order events after the current transaction commits (D69)."""

    # Do not use a bare lambda inside a loop here: a lambda looks up its
    # variables when it runs, so every callback would send the loop's last
    # event. A nested function binds the whole tuple now, which avoids that.
    def send_notices() -> None:
        for event in events:
            notify_order_event(order_id, event)

    transaction.on_commit(send_notices)


def _pack_label(variant: ProductVariant) -> str:
    """Copy the storefront's pack label rule without importing ``storefront``."""
    if variant.net_quantity_label:
        return variant.net_quantity_label
    size = variant.pack_size
    unit = variant.unit
    if size == 1:
        return f"1 {unit.lower()}"
    return f"{size:g} {unit.lower()}"


def _checkout_block_reason(summary: CartSummary) -> str:
    """Explain why a non-empty cart is not ready for checkout."""
    if summary.has_problem_lines:
        return "Some items in the cart are not available in the requested amount."
    if not summary.minimum_order_met:
        return "The cart is below the minimum order value."
    return "The cart is not ready for checkout."


def _cancel_pending_items(order: Order) -> None:
    """Mark every still-pending line cancelled after the order is cancelled."""
    for item in (
        OrderItem.objects.select_for_update()
        .filter(order=order, item_status=OrderItem.ItemStatus.PENDING)
        .order_by("pk")
    ):
        item.item_status = OrderItem.ItemStatus.CANCELLED
        item.save(update_fields=["item_status", "updated_at"])


def _apply_transition(
    order: Order,
    to_status: str,
    *,
    actor: User | None,
    note: str,
    reason: str,
    now: datetime,
    require_cancel_reason: bool,
) -> Order:
    """Apply one move to an already locked order and write its log row.

    The caller must hold the order row lock inside the same transaction. This
    private helper is shared by ``transition_order``, customer cancellation and
    ``place_order``'s own PLACED to CONFIRMED move.
    """
    valid_statuses = set(Order.Status.values)
    if to_status not in valid_statuses:
        raise InvalidTransitionError(f"Unknown order status {to_status!r}.")
    if to_status == order.status:
        raise InvalidTransitionError(f"The order is already {to_status}.")
    if not can_transition(order.status, to_status):
        raise InvalidTransitionError(f"Cannot move an order from {order.status} to {to_status}.")
    if (
        order.payment_method
        in (
            Order.PaymentMethod.COD,
            Order.PaymentMethod.CREDIT,
        )
        and to_status in COD_ONLY_BLOCKED
    ):
        raise InvalidTransitionError(f"A COD or credit order cannot move to {to_status}.")
    # The delivery gate (D71): nobody goes out until the phone, and the email
    # when there is one, are verified. It refuses before any write or notice.
    if to_status == OUT_FOR_DELIVERY and not order.user.is_ready_for_delivery:
        raise CustomerNotVerifiedError(
            "The customer must verify the phone and email before the first delivery."
        )
    if to_status == CANCELLED and require_cancel_reason and not reason.strip():
        raise CancelReasonRequiredError("A staff cancellation needs a reason.")

    from_status = order.status
    order.status = to_status
    update_fields = ["status", "updated_at"]
    if to_status == CANCELLED:
        order.cancel_reason = reason.strip()[:200]
        order.cancelled_at = now
        update_fields += ["cancel_reason", "cancelled_at"]
    elif to_status == DELIVERED:
        order.delivered_at = now
        update_fields.append("delivered_at")
    order.save(update_fields=update_fields)

    # The log already records who and when, so no AuditLog row is written.
    OrderStatusLog.objects.create(
        order=order,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        note=(note or reason).strip()[:200],
    )

    if to_status == CANCELLED:
        # Idempotent: a CONSUMED row stays consumed and an unknown group is 0.
        release_group(order.checkout_group, now=now)
        _cancel_pending_items(order)
        # Give the credit back for a cancelled credit order (D68). This does
        # nothing for a COD order that was never charged.
        reverse_order_charge(order, actor=actor, now=now)

    event = STATUS_EVENTS.get(to_status)
    if event is not None:
        _register_notices(order.pk, (event,))
    return order


def transition_order(
    order_id: int,
    to_status: str,
    *,
    actor: User | None,
    note: str = "",
    reason: str = "",
    now: datetime | None = None,
) -> Order:
    """Move one order to ``to_status``, or refuse and change nothing.

    No permission check here: views and staff roles come later (D64). A staff
    cancellation (``to_status == CANCELLED``) needs a non-blank ``reason``.
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise OrderNotFoundError("Order not found.")
        return _apply_transition(
            order,
            to_status,
            actor=actor,
            note=note,
            reason=reason,
            now=resolved_now,
            require_cancel_reason=True,
        )


def cancel_order_by_customer(
    user: User,
    order_id: int,
    *,
    reason: str = "",
    now: datetime | None = None,
) -> Order:
    """Cancel the customer's own order while it is PLACED or CONFIRMED.

    Another customer's order is reported as not found, so an id cannot confirm
    that somebody else's order exists. The reason is optional here.
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id, user=user).first()
        if order is None:
            raise OrderNotFoundError("Order not found.")
        if order.status not in CUSTOMER_CANCELLABLE:
            raise InvalidTransitionError("The customer can no longer cancel this order.")
        return _apply_transition(
            order,
            Order.Status.CANCELLED,
            actor=user,
            note="Cancelled by customer",
            reason=reason,
            now=resolved_now,
            require_cancel_reason=False,
        )


def place_order(
    *,
    user: User,
    address_id: int,
    payment_method: str,
    checkout_group: UUID,
    substitution_preference: str = Order.SubstitutionPreference.CALL_ME,
    customer_note: str = "",
    now: datetime | None = None,
) -> PlacedOrder:
    """Create a COD or credit order, reserve its stock and confirm it.

    Re-submitting the same ``checkout_group`` by the same customer returns the
    existing order with ``created=False``; another customer gets
    ``CheckoutGroupInUseError``. The user row is locked first, so two identical
    submissions queue instead of racing. A credit order is charged to the
    customer's khata in the same transaction (D68).
    """
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        # Same pattern as address create (D57): the user row is the one shared
        # lock that serialises two submissions of the same checkout.
        User.objects.select_for_update().get(pk=user.pk)

        existing = Order.objects.filter(checkout_group=checkout_group).first()
        if existing is not None:
            if existing.user_id == user.pk:
                return PlacedOrder(order=existing, created=False)
            raise CheckoutGroupInUseError("This checkout has already been used.")

        # Only COD and credit exist in Phase 3 (D68). Checked before any other
        # read or write, so an unknown value can never create a row.
        if payment_method not in (
            Order.PaymentMethod.COD,
            Order.PaymentMethod.CREDIT,
        ):
            raise InvalidPaymentMethodError("Choose cash on delivery or credit.")

        # A credit request needs an active khata with something left in it
        # (D74). The amount actually charged is decided from the cart total
        # further down, so the cash part can be split off.
        available = Decimal("0.00")
        if payment_method == Order.PaymentMethod.CREDIT:
            if not CreditAccount.objects.filter(user=user, is_active=True).exists():
                raise OrderCreditUnavailableError("Credit is not available for this account.")
            available = available_credit(user)
            if available <= Decimal("0.00"):
                raise OrderCreditLimitExceededError("There is no credit left on this account.")

        try:
            address: Address = get_address(user, address_id)
        except AddressNotFound:
            raise OrderAddressNotFoundError("Address not found.") from None
        if not address.is_active:
            raise OrderAddressNotFoundError("Address not found.")
        if not address_is_served(address):
            raise AddressNotServedError("This pin code is not served.")

        store = DbCartStore(user)
        summary = build_cart(store)
        if not summary.lines:
            raise EmptyCartError("The cart is empty.")
        if not summary.checkout_ready:
            raise CartNotReadyError(_checkout_block_reason(summary))

        settings = get_shop_settings()
        credit_applied = Decimal("0.00")
        if payment_method == Order.PaymentMethod.CREDIT:
            # Use as much of the available credit as the order needs; the rest
            # is paid in cash at the door (D74).
            credit_applied = min(available, summary.grand_total)
            cash_due = summary.grand_total - credit_applied
            if cash_due > settings.cod_cap:
                raise CodLimitExceededError(
                    "The cash part of this order is above the cash-on-delivery limit."
                )
        elif summary.grand_total > settings.cod_cap:
            raise CodLimitExceededError("The total is above the cash-on-delivery limit.")

        lines = [
            ReservationLine(variant_id=line.variant.pk, quantity=line.quantity)
            for line in summary.lines
            if line.status == STATUS_OK
        ]
        # A shortfall raises unchanged and rolls the whole transaction back:
        # no order, no reservation and no number is used.
        reservations = reserve_stock(
            user=user,
            lines=lines,
            kind="HELD_FOR_FULFILMENT",
            checkout_group=checkout_group,
            now=resolved_now,
        )
        # The order adopts the group the reservations actually carry (D63).
        group = reservations[0].checkout_group

        # A blank prefix means "not configured"; orders fall back to BS (D58).
        prefix = settings.order_number_prefix.strip() or "BS"
        order_number = next_number(prefix, when=resolved_now)
        promise = get_delivery_promise(resolved_now)

        order = Order.objects.create(
            order_number=order_number,
            user=user,
            checkout_group=group,
            status=Order.Status.PLACED,
            payment_method=payment_method,
            ship_name=address.recipient_name,
            ship_phone=address.recipient_phone,
            ship_line1=address.line1,
            ship_line2=address.line2,
            ship_landmark=address.landmark,
            ship_city=address.city,
            ship_state=address.state,
            ship_pincode=address.pincode,
            ship_label=address.label,
            item_total=summary.item_total,
            delivery_fee=summary.delivery_fee,
            grand_total=summary.grand_total,
            savings_total=summary.savings_total,
            credit_applied=credit_applied,
            # Whatever the credit part does not cover is collected at the door.
            amount_due_on_delivery=summary.grand_total - credit_applied,
            promised_delivery_date=promise.day,
            delivery_is_same_day=promise.is_same_day,
            substitution_preference=substitution_preference,
            customer_note=customer_note[:300],
        )

        for line in summary.lines:
            variant = line.variant
            OrderItem.objects.create(
                order=order,
                variant=variant,
                sku_code=variant.sku_code,
                product_name=variant.product.name_en,
                pack_label=_pack_label(variant),
                unit=variant.unit,
                hsn_code=variant.hsn_code,
                gst_rate_percent=variant.gst_rate_percent,
                mrp=variant.mrp,
                unit_price=line.unit_price,
                quantity_ordered=line.quantity,
                quantity_fulfilled=Decimal("0.000"),
                line_total=line.line_total,
            )

        # The khata charge comes after the items and before the first log row,
        # so a refused charge rolls everything back (D74).
        if credit_applied > Decimal("0.00"):
            try:
                charge_order(order, actor=user)
            except CreditNotAvailableError:
                raise OrderCreditUnavailableError(
                    "Credit is not available for this account."
                ) from None
            except CreditLimitExceededError:
                raise OrderCreditLimitExceededError(
                    "There is no credit left on this account."
                ) from None

        OrderStatusLog.objects.create(
            order=order,
            from_status="",
            to_status=Order.Status.PLACED,
            actor=user,
            note="Order placed",
        )
        _apply_transition(
            order,
            Order.Status.CONFIRMED,
            actor=user,
            note="Order confirmed",
            reason="",
            now=resolved_now,
            require_cancel_reason=False,
        )

        # Only after the order and its stock hold exist is the cart cleared.
        clear_cart(store)
        # Messages go out only after the transaction commits (D69).
        _register_notices(order.pk, (EVENT_PLACED, EVENT_STAFF_NEW_ORDER))
        return PlacedOrder(order=order, created=True)


def get_user_order(user: User, order_id: int) -> Order:
    """One order of this customer, with items and logs prefetched.

    The query count is constant: one order query plus one prefetch each for
    items and status logs, whatever the order size.
    """
    order = (
        Order.objects.filter(user=user, pk=order_id)
        .prefetch_related("items", "status_logs")
        .first()
    )
    if order is None:
        raise OrderNotFoundError("Order not found.")
    return order


def get_user_order_by_group(user: User, group: UUID) -> Order:
    """One order of this customer by its checkout group (D66).

    The confirmation page uses this instead of the id or the order number: the
    number contains slashes, and the group is already unique on the model.
    """
    order = (
        Order.objects.filter(user=user, checkout_group=group)
        .prefetch_related("items", "status_logs")
        .first()
    )
    if order is None:
        raise OrderNotFoundError("Order not found.")
    return order


def list_user_orders(user: User):
    """This customer's orders, newest first (the model's default order)."""
    return Order.objects.filter(user=user)


# The "My orders" page shows ten orders at a time (D70).
ORDER_LIST_PAGE_SIZE = 10


def list_user_order_rows(user: User):
    """This customer's orders, newest first, each with its item count (D70).

    The count is annotated into the same query, so the page needs one order
    query whatever the number of rows (no per-row query).
    """
    # The aggregate annotation adds GROUP BY, and Django drops Meta.ordering
    # on such a query, so the model's newest-first order is applied again here.
    return list_user_orders(user).annotate(item_count=Count("items")).order_by("-created_at", "-pk")
