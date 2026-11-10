"""Online payment services: start, webhook, retry and expiry (D77).

Every state change runs in one ``transaction.atomic()`` and locks its rows.
The order row is locked before the payment row, in the same order as the
expiry job, so the two cannot deadlock.

A webhook is the only thing that marks money as received. A redirect from the
browser proves nothing, so the return page only reads the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.cart.services import restore_cart_from_order
from apps.core.services import get_shop_settings
from apps.inventory.models import StockReservation
from apps.inventory.reservations import (
    ReservationError,
    ReservationExpiredError,
    ReservationLine,
    ReservationNotFoundError,
    ReservationStateError,
    hold_for_fulfilment,
    reserve_stock,
)
from apps.notifications.messages import EVENT_PAYMENT_FAILED
from apps.notifications.order_notices import notify_order_event
from apps.orders.models import Order, OrderItem
from apps.orders.services import (
    OrderNotFoundError,
    mark_order_paid,
    mark_order_payment_failed,
    transition_order,
)
from apps.payments.gateway import (
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    GatewayEvent,
    GatewayNotConfigured,
    PaymentSession,
    get_gateway,
)
from apps.payments.models import Payment, WebhookEvent


class OnlinePaymentError(ValueError):
    """Base error for every online payment refusal."""


class PaymentNotFoundError(OnlinePaymentError):
    """No payment matches the provider order id."""


class PaymentNotAllowedError(OnlinePaymentError):
    """The order cannot start or repeat an online payment now."""


class PaymentAlreadyCapturedError(OnlinePaymentError):
    """The online part is already paid; nothing more is due."""


@dataclass(frozen=True)
class StartedPayment:
    """The stored attempt and the values the browser needs to open it."""

    payment: Payment
    session: PaymentSession


def _now(now: datetime | None) -> datetime:
    return now if now is not None else timezone.now()


def _gateway():
    """The configured gateway, or a clear error instead of a crash."""
    gateway = get_gateway()
    if gateway is None:
        raise GatewayNotConfigured("Online payment is not configured.")
    return gateway


def _pending_session(gateway, payment: Payment, order: Order) -> PaymentSession:
    """Rebuild the browser values for a payment that already exists."""
    return PaymentSession(
        provider=payment.provider,
        key_id=gateway.public_key_id(),
        gateway_order_id=payment.gateway_order_id,
        amount=payment.amount,
        currency=payment.currency,
        description=f"Order {order.order_number}",
    )


def start_online_payment(
    user: User,
    order_number: str,
    *,
    gateway=None,
    now: datetime | None = None,
) -> StartedPayment:
    """Create (or reuse) the provider order for this customer's order.

    A retry after a failed attempt reuses the same order row, so a double click
    cannot create two payments. A captured payment is reported as already paid.
    """
    resolved_now = _now(now)
    provider = gateway if gateway is not None else _gateway()
    with transaction.atomic():
        order = (
            Order.objects.select_for_update().filter(user=user, order_number=order_number).first()
        )
        if order is None:
            raise OrderNotFoundError("Order not found.")
        if order.online_amount <= Decimal("0.00"):
            raise PaymentNotAllowedError("This order has no online payment.")
        if Payment.objects.filter(order=order, status=Payment.Status.CAPTURED).exists():
            raise PaymentAlreadyCapturedError("This order is already paid online.")
        if order.status not in (
            Order.Status.PLACED,
            Order.Status.PAYMENT_PENDING,
            Order.Status.PAYMENT_FAILED,
        ):
            raise PaymentNotAllowedError("This order cannot take an online payment now.")

        existing = (
            Payment.objects.select_for_update()
            .filter(
                order=order,
                status__in=(Payment.Status.CREATED, Payment.Status.FAILED),
            )
            .order_by("-pk")
            .first()
        )

        # The order waits for the gateway before it can become CONFIRMED
        # (D77). PLACED and PAYMENT_FAILED both move to PAYMENT_PENDING.
        if order.status in (Order.Status.PLACED, Order.Status.PAYMENT_FAILED):
            order = transition_order(
                order.pk,
                Order.Status.PAYMENT_PENDING,
                actor=user,
                note="Online payment started",
                now=resolved_now,
            )

        if existing is not None:
            if existing.status == Payment.Status.FAILED:
                # A retry reuses the one attempt row. That keeps the provider
                # order id unique (the fake gateway derives it from the order
                # number) and stops a double click creating two payments.
                existing.status = Payment.Status.CREATED
                existing.gateway_payment_id = ""
                existing.method = ""
                existing.failure_reason = ""
                existing.failed_at = None
                existing.raw_response = {}
                existing.save(
                    update_fields=[
                        "status",
                        "gateway_payment_id",
                        "method",
                        "failure_reason",
                        "failed_at",
                        "raw_response",
                        "updated_at",
                    ]
                )
            return StartedPayment(existing, _pending_session(provider, existing, order))

        session = provider.create_session(order)
        payment = Payment.objects.create(
            order=order,
            provider=session.provider,
            gateway_order_id=session.gateway_order_id,
            amount=session.amount,
            currency=session.currency,
            status=Payment.Status.CREATED,
        )
        return StartedPayment(payment, session)


def _restock_for_capture(order: Order, *, now: datetime) -> bool:
    """Make sure the order has a fulfilment hold before it is confirmed.

    An expired checkout hold is replaced by a fresh group. False means the
    stock cannot be reserved any more, so the payment must be refunded.
    """
    try:
        hold_for_fulfilment(order.checkout_group, now=now)
        return True
    except (ReservationExpiredError, ReservationNotFoundError, ReservationStateError):
        pass

    lines = [
        ReservationLine(variant_id=item.variant_id, quantity=item.quantity_ordered)
        for item in OrderItem.objects.filter(order=order).order_by("pk")
    ]
    try:
        rows = reserve_stock(
            user=order.user,
            lines=lines,
            kind=StockReservation.Kind.HELD_FOR_FULFILMENT,
            now=now,
        )
    except ReservationError:
        return False
    order.checkout_group = rows[0].checkout_group
    order.save(update_fields=["checkout_group", "updated_at"])
    return True


def _capture(
    payment: Payment,
    order: Order,
    event: GatewayEvent,
    *,
    now: datetime,
) -> None:
    """Apply one captured event to the payment and the order (D77)."""
    payment.status = Payment.Status.CAPTURED
    payment.gateway_payment_id = event.gateway_payment_id
    payment.method = event.method[:30]
    payment.captured_at = now
    payment.failure_reason = ""
    payment.raw_response = event.raw
    payment.save(
        update_fields=[
            "status",
            "gateway_payment_id",
            "method",
            "captured_at",
            "failure_reason",
            "raw_response",
            "updated_at",
        ]
    )

    payout_possible = order.status in (
        Order.Status.PLACED,
        Order.Status.PAYMENT_PENDING,
        Order.Status.PAYMENT_FAILED,
    )
    if payout_possible and not _restock_for_capture(order, now=now):
        payment.needs_refund = True
        payment.save(update_fields=["needs_refund", "updated_at"])
        transition_order(
            order.pk,
            Order.Status.CANCELLED,
            actor=None,
            reason="Online payment captured but the stock is no longer available.",
            now=now,
        )
        return

    confirmed = mark_order_paid(order.pk, now=now)
    # Money that arrived for an order which was not waiting for it (already
    # cancelled, already confirmed, delivered) is a refund, never a revival.
    if not payout_possible or confirmed.status != Order.Status.CONFIRMED:
        payment.needs_refund = True
        payment.save(update_fields=["needs_refund", "updated_at"])


def _fail(payment: Payment, event: GatewayEvent, *, now: datetime) -> None:
    """Apply one failed event; the checkout hold stays for a retry (D77)."""
    if payment.status != Payment.Status.CREATED:
        return
    payment.status = Payment.Status.FAILED
    payment.gateway_payment_id = event.gateway_payment_id
    payment.method = event.method[:30]
    payment.failed_at = now
    payment.failure_reason = str(event.raw.get("error", ""))[:200] or "Payment failed"
    payment.raw_response = event.raw
    payment.save(
        update_fields=[
            "status",
            "gateway_payment_id",
            "method",
            "failed_at",
            "failure_reason",
            "raw_response",
            "updated_at",
        ]
    )
    mark_order_payment_failed(payment.order_id, reason=payment.failure_reason, now=now)


def handle_gateway_event(
    event: GatewayEvent,
    *,
    provider: str,
    now: datetime | None = None,
) -> WebhookEvent:
    """Store and apply one verified webhook exactly once (D77).

    ``event_id`` is unique in the database, so a provider retry finds the row
    and returns without applying anything twice.
    """
    resolved_now = _now(now)
    event_id = event.event_id or str(uuid4())
    with transaction.atomic():
        webhook = WebhookEvent.objects.select_for_update().filter(event_id=event_id).first()
        if webhook is not None and webhook.processed_at is not None:
            return webhook
        if webhook is None:
            webhook = WebhookEvent.objects.create(
                provider=provider,
                event_id=event_id,
                event_type=event.kind,
                payload=event.raw,
            )

        # The order is locked before the payment, the same order the expiry
        # job uses. The payment row is found without a lock first, only to
        # learn its order id.
        looked_up = Payment.objects.filter(gateway_order_id=event.gateway_order_id).first()
        if looked_up is None:
            webhook.error = "Payment not found"
            webhook.processed_at = resolved_now
            webhook.save(update_fields=["error", "processed_at", "updated_at"])
            return webhook

        order = Order.objects.select_for_update().filter(pk=looked_up.order_id).first()
        payment = Payment.objects.select_for_update().filter(pk=looked_up.pk).first()
        if order is None or payment is None:
            webhook.error = "Payment not found"
            webhook.processed_at = resolved_now
            webhook.save(update_fields=["error", "processed_at", "updated_at"])
            return webhook
        webhook.payment = payment

        if event.amount != payment.amount:
            webhook.error = "Amount does not match the payment"
        elif event.kind == PAYMENT_CAPTURED:
            if payment.status != Payment.Status.CAPTURED:
                _capture(payment, order, event, now=resolved_now)
        elif event.kind == PAYMENT_FAILED:
            _fail(payment, event, now=resolved_now)

        webhook.processed_at = resolved_now
        webhook.save(update_fields=["payment", "error", "processed_at", "updated_at"])
        return webhook


def expire_unpaid_online_orders(now: datetime | None = None) -> int:
    """Cancel online orders whose payment never completed and restore carts.

    The cutoff adds the shop's reservation window and ``PAYMENT_GRACE_MINUTES``
    to the order time. Idempotent: a second run finds no matching order.
    """
    resolved_now = _now(now)
    shop = get_shop_settings()
    grace = int(getattr(settings, "PAYMENT_GRACE_MINUTES", 30))
    cutoff = resolved_now - timedelta(minutes=shop.reservation_minutes + grace)
    order_ids = list(
        Order.objects.filter(
            online_amount__gt=Decimal("0.00"),
            status__in=(
                Order.Status.PLACED,
                Order.Status.PAYMENT_PENDING,
                Order.Status.PAYMENT_FAILED,
            ),
            created_at__lte=cutoff,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    cancelled = 0
    for order_id in order_ids:
        if _expire_one(order_id, now=resolved_now, cutoff=cutoff):
            cancelled += 1
    return cancelled


def _expire_one(order_id: int, *, now: datetime, cutoff: datetime) -> bool:
    """Cancel one unpaid online order, inside its own transaction."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            return False
        if order.status not in (
            Order.Status.PLACED,
            Order.Status.PAYMENT_PENDING,
            Order.Status.PAYMENT_FAILED,
        ):
            return False
        if order.online_amount <= Decimal("0.00") or order.created_at > cutoff:
            return False
        # A capture may have landed between the list query and this lock.
        if Payment.objects.filter(order=order, status=Payment.Status.CAPTURED).exists():
            return False

        items = list(OrderItem.objects.filter(order=order).order_by("pk"))
        transition_order(
            order.pk,
            Order.Status.CANCELLED,
            actor=None,
            reason="Online payment was not completed in time.",
            now=now,
            notify=False,
        )
        restore_cart_from_order(
            order.user,
            [(item.variant, item.quantity_ordered) for item in items],
        )
        _register_payment_failed_notice(order.pk)
        return True


def _register_payment_failed_notice(order_id: int) -> None:
    """Send the customer's cart link after the cancellation commits."""

    # A nested function binds this id now; a lambda would read the loop's last
    # value when it runs.
    def send_notice() -> None:
        notify_order_event(order_id, EVENT_PAYMENT_FAILED)

    transaction.on_commit(send_notice)


def latest_payment(order: Order) -> Payment | None:
    """The most recent attempt, used by the return page and the detail page."""
    return Payment.objects.filter(order=order).order_by("-created_at", "-pk").first()
