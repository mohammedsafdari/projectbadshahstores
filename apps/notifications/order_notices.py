"""Turn an order into the messages the customer and the shop receive (D69).

Every send goes through ``delivery.deliver``, which writes one
``NotificationLog`` row per attempt and queues a retry for a failed order or
staff message (D88). ``notify_order_event`` is meant to be called after the
order transaction has committed (Slice 38b).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from apps.accounts.models import User
from apps.core.services import get_shop_settings
from apps.notifications.delivery import deliver
from apps.notifications.messages import (
    CUSTOMER_EVENTS,
    EVENT_CREDIT_REMINDER,
    EVENT_REFUND_PROCESSED,
    EVENT_STAFF_NEW_ORDER,
    EVENT_STAFF_REFUND_FAILED,
    OrderNotice,
    credit_reminder_notice,
    credit_reminder_text,
    email_body,
    email_subject,
    notice_variables,
    sms_text,
)
from apps.notifications.models import NotificationLog
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


def _send_mail_to(recipient: str, subject: str, body: str) -> None:
    """Send one plain email; the send itself is all this call is for."""
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient])


def _send_customer_sms(order: Order, event: str, notice: OrderNotice) -> None:
    """Send the customer's SMS when the switch is on, swallowing any failure."""
    if not settings.ORDER_SMS_ENABLED:
        return
    # ``deliver`` runs the lambda, writes the log row and never raises. The
    # backend is built inside the lambda, so a bad SMS_BACKEND path is a
    # logged failure like any other channel problem (D88).
    log = deliver(
        channel=NotificationLog.Channel.SMS,
        purpose=NotificationLog.Purpose.ORDER,
        event=event,
        order_id=order.pk,
        recipient=order.user.phone,
        send=lambda: get_sms_backend().send_notice(
            order.user.phone,
            event,
            notice_variables(event, notice),
            sms_text(event, notice),
        ),
    )
    if log is None or log.status != NotificationLog.Status.SENT:
        # Only the event, the order and the channel: never a phone or a text.
        logger.error(
            "Order notice failed: event=%s order_id=%s channel=sms",
            event,
            order.pk,
        )


def _send_customer_email(order: Order, event: str, notice: OrderNotice) -> None:
    """Send the customer's email when the switch is on and there is an address."""
    recipient = order.user.email
    if not settings.ORDER_EMAIL_ENABLED or not recipient:
        return
    log = deliver(
        channel=NotificationLog.Channel.EMAIL,
        purpose=NotificationLog.Purpose.ORDER,
        event=event,
        order_id=order.pk,
        recipient=recipient,
        send=lambda: _send_mail_to(
            recipient,
            email_subject(event, notice),
            email_body(event, notice),
        ),
    )
    if log is None or log.status != NotificationLog.Status.SENT:
        logger.error(
            "Order notice failed: event=%s order_id=%s channel=customer_email",
            event,
            order.pk,
        )


def notify_credit_reminder(user: User, balance: Decimal) -> bool:
    """Send one manual credit reminder SMS; return False when it was skipped.

    A reminder is never automatic: a staff member presses the button, and a
    failure is logged and never raised. It is logged but never retried: a
    repeated reminder would nag the customer (D88).
    """
    if not settings.ORDER_SMS_ENABLED:
        return False
    sent = False

    def send() -> None:
        # ``nonlocal`` lets the inner function write the outer ``sent`` flag,
        # because ``deliver`` returns no success flag of its own.
        nonlocal sent
        variables = notice_variables(
            EVENT_CREDIT_REMINDER,
            credit_reminder_notice(balance),
        )
        get_sms_backend().send_notice(
            user.phone,
            EVENT_CREDIT_REMINDER,
            variables,
            credit_reminder_text(balance),
        )
        sent = True

    deliver(
        channel=NotificationLog.Channel.SMS,
        purpose=NotificationLog.Purpose.CREDIT_REMINDER,
        event=EVENT_CREDIT_REMINDER,
        order_id=None,
        recipient=user.phone,
        send=send,
    )
    if not sent:
        # The log line carries the event and the user id, never the phone or
        # the message text.
        logger.error(
            "Credit reminder failed: event=%s user_id=%s channel=sms",
            EVENT_CREDIT_REMINDER,
            user.pk,
        )
        return False
    return True


def _send_staff_email(notice: OrderNotice, order_id: int, event: str) -> None:
    """Send a shop email for a staff event when the switch is on."""
    if not settings.ORDER_EMAIL_ENABLED:
        return
    try:
        recipient = get_shop_settings().email
    except Exception:
        logger.exception(
            "Order notice failed: event=%s order_id=%s channel=staff_email",
            event,
            order_id,
        )
        return
    if not recipient:
        return
    log = deliver(
        channel=NotificationLog.Channel.EMAIL,
        purpose=NotificationLog.Purpose.STAFF,
        event=event,
        order_id=order_id,
        recipient=recipient,
        send=lambda: _send_mail_to(
            recipient,
            email_subject(event, notice),
            email_body(event, notice),
        ),
    )
    if log is None or log.status != NotificationLog.Status.SENT:
        logger.error(
            "Order notice failed: event=%s order_id=%s channel=staff_email",
            event,
            order_id,
        )
