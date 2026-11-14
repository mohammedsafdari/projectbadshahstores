"""Record every send attempt and queue a retry for a failed order message (D88).

One function, ``deliver``, wraps a real send: it writes exactly one
``NotificationLog`` row and never raises, so no page or task can fail because a
message did. A failed order or staff message becomes RETRY_PENDING and is
queued through ``transaction.on_commit``; a code or a manual reminder is only
logged, because a late code or a repeated reminder is worse than none.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from django.db import transaction

from apps.notifications.models import NotificationLog
from apps.notifications.sms import _mask_phone

logger = logging.getLogger(__name__)

# Only an order message may be sent again. A code must arrive at once and a
# manual reminder is a person's decision, so neither is ever repeated (D88).
RETRYABLE_PURPOSES = (
    NotificationLog.Purpose.ORDER,
    NotificationLog.Purpose.STAFF,
)

# The total sends allowed, the first one included.
MAX_ATTEMPTS = 3

# Seconds to wait after each failure: one minute, five minutes, thirty minutes.
RETRY_DELAYS = (60, 300, 1800)

# Six digits in a row is a phone number or a code, never a useful error detail.
_LONG_DIGIT_RUN = re.compile(r"\d{6,}")

# The error summary column is 200 characters wide.
ERROR_SUMMARY_LIMIT = 200

# How much of the exception message is kept before the digit rule applies.
ERROR_MESSAGE_LIMIT = 120


def mask_email(email: str) -> str:
    """Keep the first letter and the domain: asha@example.com -> a***@example.com."""
    # partition splits once at the first "@" and always returns three parts.
    local, _separator, domain = email.strip().partition("@")
    first_letter = local[:1]
    return f"{first_letter}***@{domain}" if domain else f"{first_letter}***"


def mask_recipient(channel: str, recipient: str) -> str:
    """The masked contact a log row may keep."""
    if channel == NotificationLog.Channel.EMAIL:
        return mask_email(recipient)
    # The same mask the console SMS backend prints.
    return _mask_phone(recipient)


def error_summary(error: BaseException) -> str:
    """The exception class and a short, digit-free message (D88).

    Every run of six or more digits is replaced by "#", so a phone number or a
    login code can never reach the database through an error message.
    """
    message = str(error)[:ERROR_MESSAGE_LIMIT]
    cleaned = _LONG_DIGIT_RUN.sub("#", message)
    return f"{type(error).__name__}: {cleaned}"[:ERROR_SUMMARY_LIMIT]


def retry_delay(attempts: int) -> int:
    """Seconds to wait after a failure that leaves ``attempts`` sends made."""
    # min() keeps the index inside the tuple if the count ever grows.
    index = min(attempts, len(RETRY_DELAYS)) - 1
    return RETRY_DELAYS[index]


def _queue_retry(log: NotificationLog) -> None:
    """Mark the row RETRY_PENDING and queue the task after the commit (D88)."""
    log.status = NotificationLog.Status.RETRY_PENDING
    log.save(update_fields=["status", "updated_at"])
    # Imported here, not at the top: tasks.py imports this module, so a
    # top-level import would be a circular import between the two files.
    from apps.notifications.tasks import retry_notification

    delay = retry_delay(log.attempts)
    # on_commit runs the queue call only after the surrounding transaction
    # commits, so a rolled-back order never queues a retry.
    transaction.on_commit(lambda: retry_notification.apply_async(args=[log.pk], countdown=delay))


def deliver(
    channel: str,
    purpose: str,
    event: str,
    order_id: int | None,
    recipient: str,
    send: Callable[[], str | None],
) -> NotificationLog | None:
    """Send once, write one log row and never raise (D88).

    ``send`` does the real work and returns the provider's request id when it
    has one. The row is written after the send, so a database problem can
    never stop a message from going out.
    """
    reference = ""
    summary = ""
    status = NotificationLog.Status.SENT
    try:
        reference = send() or ""
    except Exception as error:
        status = NotificationLog.Status.FAILED
        summary = error_summary(error)
        # The traceback goes to the log file; this line carries no phone, no
        # address and no message text, and the row keeps the cleaned summary.
        logger.exception("Notification send failed: purpose=%s event=%s", purpose, event)

    try:
        log = NotificationLog.objects.create(
            channel=channel,
            purpose=purpose,
            event=event,
            order_id=order_id,
            recipient_hint=mask_recipient(channel, recipient),
            status=status,
            provider_ref=reference[:100],
            error_summary=summary,
        )
    except Exception:
        # The send already happened; only the record of it failed.
        logger.exception(
            "Notification log could not be written: channel=%s event=%s", channel, event
        )
        return None
    if log.status == NotificationLog.Status.FAILED and purpose in RETRYABLE_PURPOSES:
        try:
            _queue_retry(log)
        except Exception:
            # The row stays FAILED, so a person can see it in the admin even
            # when the queue itself is down.
            logger.exception("Notification retry could not be queued: log_id=%s", log.pk)
    return log
