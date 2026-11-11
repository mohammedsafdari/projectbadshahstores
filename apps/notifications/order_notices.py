"""Turn an order into the messages the customer and the shop receive (D69).

Nothing here writes to the database. ``notify_order_event`` is meant to be
called after the order transaction has committed (Slice 38b).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from apps.core.services import get_shop_settings
from apps.notifications.messages import (
    CUSTOMER_EVENTS,
    EVENT_REFUND_PROCESSED,
    EVENT_STAFF_NEW_ORDER,
    EVENT_STAFF_REFUND_FAILED,
    OrderNotice,
    email_body,
    email_subject,
    sms_text,
)
from apps.notifications.sms import get_sms_backend
from apps.orders.models import Order

logger = logging.getLogger(__name__)


def build_notice(order: Order, refund=None) -> OrderNotice:
    """Copy the order facts one message needs into a plain, read-only object.

    ``refund`` is the Refund row for a refund event; every other event passes
    nothing and keeps the two refund fields at zero (D78).
    """
    return OrderNotice(
        order_number=order.order_number,
        # The account name is preferred; a blank one falls back to the name on
        # the frozen shipping address.
        customer_name=order.user.full_name or order.ship_name,
        payment_method=order.payment_method,
        grand_total=order.grand_total,
        amount_due_on_delivery=order.amount_due_on_delivery,
        promised_delivery_date=order.promised_delivery_date,
        ship_city=order.ship_city,
        ship_pincode=order.ship_pincode,
        ship_phone=order.ship_phone,
        online_amount=order.online_amount,
        credit_applied=order.credit_applied,
        cart_url=_cart_url(),
        refund_amount=refund.amount if refund is not None else Decimal("0.00"),
        refund_fee=refund.fee_amount if refund is not None else Decimal("0.00"),
    )


def _cart_url() -> str:
    """The cart link for a message, absolute when the site address is set."""
    path = reverse("cart_detail")
    base = str(getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else path


def notify_order_event(order_id: int, event: str) -> None:
    """Send the messages for one order event, and never raise (D69).

    Every channel has its own try/except because an order must never fail
    because a message did, and one broken channel must not stop the next.
    Loading the order and building the notice share the same guard, so even a
    database error or a bad field cannot escape.
    Call it after the database commit (Slice 38b), so a rolled-back order
    never sends a message.
    """
    try:
        # select_related("user") reads the customer in the same query. There is
        # no lock and no write: sending a message changes nothing in the database.
        order = Order.objects.select_related("user").filter(pk=order_id).first()
        if order is None or (
            event not in CUSTOMER_EVENTS
            and event not in (EVENT_STAFF_NEW_ORDER, EVENT_STAFF_REFUND_FAILED)
        ):
            # Only the event and the id: never a phone number or a message text.
            logger.warning("Order notice skipped: event=%s order_id=%s", event, order_id)
            return

        notice = build_notice(order, _latest_refund(order, event))
    except Exception:
        logger.exception(
            "Order notice failed: event=%s order_id=%s channel=load",
            event,
            order_id,
        )
        return
    if event == EVENT_STAFF_NEW_ORDER:
        _send_staff_email(notice, order.pk, EVENT_STAFF_NEW_ORDER)
        return
    if event == EVENT_STAFF_REFUND_FAILED:
        _send_staff_email(notice, order.pk, EVENT_STAFF_REFUND_FAILED)
        return
    _send_customer_sms(order, event, notice)
    _send_customer_email(order, event, notice)


def _latest_refund(order: Order, event: str):
    """The newest refund for a refund event, or None for every other event.

    The import is inside the function so this module keeps working before the
    payments tables exist, and a normal order message adds no query (D78).
    """
    if event not in (EVENT_REFUND_PROCESSED, EVENT_STAFF_REFUND_FAILED):
        return None
    from apps.payments.models import Refund

    return Refund.objects.filter(payment__order=order).order_by("-created_at", "-pk").first()


def _send_customer_sms(order: Order, event: str, notice: OrderNotice) -> None:
    """Send the customer's SMS when the switch is on, swallowing any failure."""
    if not settings.ORDER_SMS_ENABLED:
        return
    try:
        # The backend is built inside the try, so a bad SMS_BACKEND path is
        # swallowed like any other channel failure.
        get_sms_backend().send_message(order.user.phone, sms_text(event, notice))
    except Exception:
        # logger.exception adds the traceback for the developer, but the
        # message itself carries only the event, the order and the channel.
        logger.exception(
            "Order notice failed: event=%s order_id=%s channel=sms",
            event,
            order.pk,
        )


def _send_customer_email(order: Order, event: str, notice: OrderNotice) -> None:
    """Send the customer's email when the switch is on and there is an address."""
    recipient = order.user.email
    if not settings.ORDER_EMAIL_ENABLED or not recipient:
        return
    try:
        send_mail(
            email_subject(event, notice),
            email_body(event, notice),
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
        )
    except Exception:
        logger.exception(
            "Order notice failed: event=%s order_id=%s channel=customer_email",
            event,
            order.pk,
        )


def _send_staff_email(notice: OrderNotice, order_id: int, event: str) -> None:
    """Send a shop email for a staff event when the switch is on."""
    if not settings.ORDER_EMAIL_ENABLED:
        return
    try:
        recipient = get_shop_settings().email
        if not recipient:
            return
        send_mail(
            email_subject(event, notice),
            email_body(event, notice),
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
        )
    except Exception:
        logger.exception(
            "Order notice failed: event=%s order_id=%s channel=staff_email",
            event,
            order_id,
        )
