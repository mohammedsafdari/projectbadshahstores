"""The one Celery task that sends a failed order message again (D88).

The task is a thin wrapper: ``delivery`` owns the log row and the backoff, and
this file only re-builds the notice and re-sends the one channel it names.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

from apps.core.services import get_shop_settings
from apps.notifications.delivery import MAX_ATTEMPTS, error_summary, retry_delay
from apps.notifications.messages import (
    email_body,
    email_subject,
    notice_variables,
    sms_text,
)
from apps.notifications.models import NotificationLog
from apps.notifications.order_notices import _latest_refund, build_notice
from apps.notifications.sms import get_sms_backend
from apps.orders.models import Order

logger = logging.getLogger(__name__)

# What a row says when the channel has nowhere to send any more.
NO_RECIPIENT_TEXT = "NoRecipient: the address or phone is blank"
# What a row says when its order was deleted, because the link is SET_NULL.
MISSING_ORDER_TEXT = "OrderMissing: the order no longer exists"


def _save(log: NotificationLog, fields: list[str]) -> None:
    """Save the row and always move its ``updated_at``."""
    # ``[*fields, "updated_at"]`` unpacks the caller's list into one new list.
    log.save(update_fields=[*fields, "updated_at"])


def _retry_recipient(log: NotificationLog, order: Order) -> str:
    """Where this one channel sends on a retry, or a blank string."""
    if log.channel == NotificationLog.Channel.SMS:
        return order.user.phone
    if log.purpose == NotificationLog.Purpose.STAFF:
        return get_shop_settings().email
    return order.user.email or ""


def _send_again(log: NotificationLog, notice, recipient: str) -> str | None:
    """Send only the channel the row names, with a freshly built notice."""
    if log.channel == NotificationLog.Channel.SMS:
        # A notice event always has variables and a text; an unknown event
        # raises, and the caller turns that into a failed attempt.
        return get_sms_backend().send_notice(
            recipient,
            log.event,
            notice_variables(log.event, notice),
            sms_text(log.event, notice),
        )
    # send_mail returns the number of messages sent; the log keeps no such ref.
    send_mail(
        email_subject(log.event, notice),
        email_body(log.event, notice),
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
    )
    return None


def _fail_again(log: NotificationLog, error: BaseException) -> str:
    """Count one more failure and either queue the next try or give up."""
    log.attempts += 1
    log.error_summary = error_summary(error)
    if log.attempts >= MAX_ATTEMPTS:
        log.status = NotificationLog.Status.GAVE_UP
        _save(log, ["attempts", "error_summary", "status"])
        logger.warning("Notification retry gave up: log_id=%s attempts=%s", log.pk, log.attempts)
        return log.status
    log.status = NotificationLog.Status.RETRY_PENDING
    _save(log, ["attempts", "error_summary", "status"])
    delay = retry_delay(log.attempts)
    # Queued after this transaction commits, so a rolled-back change never
    # leaves a task behind that sends twice.
    transaction.on_commit(lambda: retry_notification.apply_async(args=[log.pk], countdown=delay))
    return log.status


@shared_task
def retry_notification(log_id: int) -> str:
    """Send one failed order message again and return the status stored (D88).

    The row is locked, so two tasks for the same row can never both send: the
    second one sees a status that is no longer RETRY_PENDING and stops.
    """
    with transaction.atomic():
        log = NotificationLog.objects.select_for_update().filter(pk=log_id).first()
        if log is None or log.status != NotificationLog.Status.RETRY_PENDING:
            # A repeated or concurrent run must never send a second time.
            return ""
        # A deleted order left the row with no order at all (SET_NULL), and a
        # missing row is the same case, so both give up at once.
        order = None
        if log.order_id is not None:
            order = Order.objects.select_related("user").filter(pk=log.order_id).first()
        if order is None:
            log.status = NotificationLog.Status.GAVE_UP
            log.error_summary = MISSING_ORDER_TEXT
            _save(log, ["status", "error_summary"])
            return log.status
        recipient = _retry_recipient(log, order)
        if not recipient:
            log.status = NotificationLog.Status.GAVE_UP
            log.error_summary = NO_RECIPIENT_TEXT
            _save(log, ["status", "error_summary"])
            return log.status
        # The refund fields are rebuilt from the newest refund, so a retried
        # refund message reads exactly like the first one would have.
        notice = build_notice(order, _latest_refund(order, log.event))
        try:
            reference = _send_again(log, notice, recipient)
        except Exception as error:
            return _fail_again(log, error)
        log.status = NotificationLog.Status.SENT
        log.provider_ref = (reference or "")[:100]
        log.error_summary = ""
        _save(log, ["status", "provider_ref", "error_summary"])
        return log.status
