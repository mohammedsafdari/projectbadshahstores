"""Staff order actions: packing, sending out, delivering and cancelling (D79).

Every function here is called by a staff member and checks the capability
first (D75), so a view only has to pass the actor along. Each write path
runs in one transaction, locks the rows it changes and writes each row with
``save(update_fields=[..., "updated_at"])``.

The rules of this module:

* the delivery gate runs before anything else, so a refused gate changes
  nothing at all (D71);
* sending out consumes the stock holds and writes one ``SALE`` movement per
  order line in the same transaction, and sending out twice never writes a
  second sale (D7, D63);
* an item that is not available means the whole order is cancelled with a
  reason: there is no partial fulfilment and no substitution (D79);
* the cash collected at the door must be exactly the amount the order owes,
  and the collection row and the status change are one transaction (D79).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import User
from apps.inventory.models import StockReservation
from apps.inventory.reservations import consume_reservation
from apps.inventory.services import StockMovementLine, record_returns, record_sales
from apps.orders.invoices import issue_invoice, mark_invoice_cancelled
from apps.orders.models import CodCollection, Order, OrderItem
from apps.orders.services import (
    CancelReasonRequiredError,
    CustomerNotVerifiedError,
    InvalidTransitionError,
    OrderError,
    OrderNotFoundError,
    transition_order,
)

# Two places, the paisa. Money is never a float (D3).
TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")

# The plain-words reasons of the delivery gate (D71). The staff member has to
# know what to ask the customer, so the reason names the phone or the email.
GATE_REASON_UNVERIFIED_PHONE = "The customer's phone number is not verified yet."
GATE_REASON_UNVERIFIED_EMAIL = "The customer's email address is not verified yet."

# The statuses staff may cancel from (D64, D79).
STAFF_CANCELLABLE = (
    Order.Status.CONFIRMED,
    Order.Status.PACKING,
    Order.Status.OUT_FOR_DELIVERY,
)


class StaffPermissionError(PermissionError):
    """The staff member does not have the capability for this action (D75)."""


class StaffOrderError(OrderError):
    """Base error for the staff order actions."""


class StockHoldUnavailableError(StaffOrderError):
    """The stock hold of the order is missing, expired or does not match."""


class CodAmountRequiredError(StaffOrderError):
    """The order is due cash at the door and no amount was entered."""


class CodAmountMismatchError(StaffOrderError):
    """The collected amount is not the amount the order is due."""


class CodCollectionExistsError(StaffOrderError):
    """This order already has a cash collection."""


def require_capability(actor: User, capability: str) -> None:
    """Refuse when the actor may not take this action (D75).

    The check runs before any other read or write, so a staff member without
    the right never changes anything.
    """
    if not has_capability(actor, capability):
        # The label of the capability reads as the action, for example
        # "Send out for delivery".
        label = Capability(capability).label
        raise StaffPermissionError(f"You do not have the right to {label.lower()}.")


def delivery_gate_reason(user: User) -> str:
    """Why this customer may not go out yet, in plain words (D71).

    An empty string means the customer may go out.
    """
    if user.phone_verified_at is None:
        return GATE_REASON_UNVERIFIED_PHONE
    # An OTP-only customer has no email address, so there is nothing to prove.
    if user.email and user.email_verified_at is None:
        return GATE_REASON_UNVERIFIED_EMAIL
    return ""


def start_packing(order_id: int, *, actor: User, now: datetime | None = None) -> Order:
    """Move a confirmed order to PACKING (D79).

    The move goes through the one transition path of the order service
    (D10, D64), so the status log row, the illegal moves and the notice rules
    are the same everywhere.
    """
    require_capability(actor, Capability.START_PACKING)
    return transition_order(
        order_id,
        Order.Status.PACKING,
        actor=actor,
        note="Packing started",
        now=now,
    )


def _check_hold(
    hold: StockReservation | None,
    item: OrderItem,
    now: datetime,
) -> None:
    """Refuse when the stock hold of one line is missing, expired or short.

    No partial fulfilment: one bad line refuses the whole send-out (D79).
    """
    if hold is None:
        raise StockHoldUnavailableError(
            f"The stock hold for {item.product_name} is missing.",
        )
    if hold.status != StockReservation.Status.ACTIVE:
        raise StockHoldUnavailableError(
            f"The stock hold for {item.product_name} is no longer active.",
        )
    # The expiry is checked before the kind, so an expired checkout hold says
    # that it has expired.
    if hold.expires_at is not None and hold.expires_at <= now:
        raise StockHoldUnavailableError(
            f"The stock hold for {item.product_name} has expired.",
        )
    if hold.kind != StockReservation.Kind.HELD_FOR_FULFILMENT:
        raise StockHoldUnavailableError(
            f"The stock hold for {item.product_name} is not held for this order.",
        )
    if hold.quantity != item.quantity_ordered:
        raise StockHoldUnavailableError(
            f"The stock hold for {item.product_name} does not match the order.",
        )


def pack_and_send_out(order_id: int, *, actor: User, now: datetime | None = None) -> Order:
    """Pack a picked order and send it out for delivery (D79).

    One transaction: the delivery gate is checked first, then every stock hold
    of the order is consumed, then one ``SALE`` movement is written for every
    order line, and only then does the order move to OUT_FOR_DELIVERY. A
    refused gate, a missing hold or a short hold changes nothing at all.
    """
    require_capability(actor, Capability.SEND_OUT)
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise OrderNotFoundError("Order not found.")
        if order.status != Order.Status.PACKING:
            raise InvalidTransitionError(
                f"This order is {order.status}; only a packing order can be sent out."
            )

        # The delivery gate runs first (D71). Nothing has changed yet, so a
        # refusal leaves the order, the holds and the stock exactly as they were.
        reason = delivery_gate_reason(order.user)
        if reason:
            raise CustomerNotVerifiedError(reason)

        items = list(OrderItem.objects.select_for_update().filter(order=order).order_by("pk"))
        if not items:
            raise StockHoldUnavailableError("This order has no items to pack.")

        # The holds of this order are the rows of its checkout group (D63).
        holds = {
            row.variant_id: row
            for row in StockReservation.objects.select_for_update()
            .filter(checkout_group=order.checkout_group)
            .order_by("pk")
        }
        for item in items:
            _check_hold(holds.get(item.variant_id), item, resolved_now)

        # The holds are consumed and the sale is written before the status
        # moves, so the stock and the order always agree.
        for item in items:
            consume_reservation(holds[item.variant_id].pk, now=resolved_now)
        record_sales(
            lines=[
                StockMovementLine(variant_id=item.variant_id, quantity=item.quantity_ordered)
                for item in items
            ],
            reason=f"Order {order.order_number}",
            created_by=actor,
        )
        # Nothing is weighed again: the ordered quantity is the fulfilled one.
        for item in items:
            item.quantity_fulfilled = item.quantity_ordered
            item.item_status = OrderItem.ItemStatus.FULFILLED
            item.save(update_fields=["quantity_fulfilled", "item_status", "updated_at"])

        # The GST tax invoice is issued inside this transaction, after the sale
        # is written and before the status moves (D13, D80). A refusal rolls
        # the whole send-out back, so no stock, hold or number is left behind.
        issue_invoice(order, actor=actor, now=resolved_now)

        # ``transition_order`` loads the order again, so the object it returns
        # is the one with the new status. The caller gets that one.
        updated = transition_order(
            order_id,
            Order.Status.OUT_FOR_DELIVERY,
            actor=actor,
            note="Packed and sent out",
            now=resolved_now,
        )
    return updated


def _clean_collected_amount(value: Decimal | int | None) -> Decimal | None:
    """The cash the staff entered, as money, or None when nothing was entered.

    A float is refused: money is always ``Decimal`` (D3).
    """
    if value is None:
        return None
    # bool is a subclass of int, so it is refused first.
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise CodAmountMismatchError("The collected amount must be a decimal number.")
    amount = Decimal(value)
    if not amount.is_finite():
        raise CodAmountMismatchError("The collected amount must be a finite number.")
    # ``quantize`` rounds to two places; a value that changes had more.
    if amount != amount.quantize(TWO_PLACES):
        raise CodAmountMismatchError(
            "The collected amount must have at most two decimal places.",
        )
    return amount.quantize(TWO_PLACES)


def mark_delivered(
    order_id: int,
    *,
    actor: User,
    collected_amount: Decimal | int | None = None,
    now: datetime | None = None,
) -> Order:
    """Deliver an order and record the cash taken at the door (D79).

    An order that owes cash at the door needs the collected amount, and it
    must be exactly the amount due: no short and no over. An order paid on
    the khata or online needs no cash and gets no collection row. The
    collection row and the status change are one transaction.
    """
    require_capability(actor, Capability.DELIVER_COD)
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise OrderNotFoundError("Order not found.")
        if order.status != Order.Status.OUT_FOR_DELIVERY:
            raise InvalidTransitionError(
                f"This order is {order.status}; only an order out for delivery can be delivered."
            )

        due = order.amount_due_on_delivery
        amount = _clean_collected_amount(collected_amount)
        if due > ZERO:
            if amount is None:
                raise CodAmountRequiredError(
                    f"This order must collect {due} in cash at the door.",
                )
            if amount != due:
                raise CodAmountMismatchError(
                    f"This order must collect exactly {due} in cash at the door."
                )
        else:
            if amount is not None and amount > ZERO:
                raise CodAmountMismatchError("This order has no cash to collect.")
            amount = None

        if amount is not None:
            if CodCollection.objects.filter(order=order).exists():
                raise CodCollectionExistsError("This order already has a cash collection.")
            CodCollection.objects.create(
                order=order,
                amount=amount,
                collected_by=actor,
                collected_at=resolved_now,
            )

        # ``transition_order`` loads the order again, so the object it
        # returns is the one with the new status.
        updated = transition_order(
            order_id,
            Order.Status.DELIVERED,
            actor=actor,
            note="Delivered",
            now=resolved_now,
        )
    return updated


def _cancel_fulfilled_items(order: Order) -> None:
    """Mark the lines of a sent-out order cancelled when the goods come back."""
    for item in (
        OrderItem.objects.select_for_update()
        .filter(order=order, item_status=OrderItem.ItemStatus.FULFILLED)
        .order_by("pk")
    ):
        item.item_status = OrderItem.ItemStatus.CANCELLED
        item.save(update_fields=["item_status", "updated_at"])


def staff_cancel_order(
    order_id: int,
    *,
    actor: User,
    reason: str,
    now: datetime | None = None,
) -> Order:
    """Cancel an order as staff, with a reason (D79).

    The order may be cancelled while it is confirmed, being packed or out for
    delivery. The existing cancel path does the rest: the holds are released,
    the khata charge is reversed, a captured online payment is refunded and
    the customer gets the cancel message (D69, D78).

    An order that was already sent out gives its goods back to the stock,
    with one return movement per line.
    """
    require_capability(actor, Capability.STAFF_CANCEL)
    cleaned_reason = (reason or "").strip()
    if not cleaned_reason:
        raise CancelReasonRequiredError("A staff cancellation needs a reason.")
    resolved_now = now if now is not None else timezone.now()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise OrderNotFoundError("Order not found.")
        if order.status not in STAFF_CANCELLABLE:
            raise InvalidTransitionError(
                f"This order is {order.status}; staff can only cancel a confirmed, "
                "packing or out-for-delivery order."
            )

        # A consumed hold is the proof that the order was sent out, so the
        # goods that left the shop come back to the stock.
        sent = list(
            StockReservation.objects.select_for_update()
            .filter(
                checkout_group=order.checkout_group,
                status=StockReservation.Status.CONSUMED,
            )
            .order_by("pk")
        )
        if sent:
            record_returns(
                lines=[
                    StockMovementLine(variant_id=row.variant_id, quantity=row.quantity)
                    for row in sent
                ],
                reason=f"Order {order.order_number} cancelled",
                created_by=actor,
            )
            _cancel_fulfilled_items(order)

        # A cancelled invoice keeps its number and is marked, never deleted or
        # renumbered (D14, D80). An order that never got one writes nothing.
        mark_invoice_cancelled(order, cleaned_reason, now=resolved_now)

        # ``transition_order`` loads the order again, so the object it
        # returns is the one with the new status.
        updated = transition_order(
            order_id,
            Order.Status.CANCELLED,
            actor=actor,
            note=f"Cancelled by staff: {cleaned_reason}",
            reason=cleaned_reason,
            now=resolved_now,
        )
    return updated
