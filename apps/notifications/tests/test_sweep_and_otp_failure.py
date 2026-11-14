"""The retry sweep and the "no order, no retry" rule (D89).

The sweep is called directly, so no broker is needed; ``apply_async`` is
patched wherever the test only wants to see that a retry was queued.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.notifications.delivery import deliver
from apps.notifications.messages import EVENT_PLACED, EVENT_STAFF_NEW_ORDER
from apps.notifications.models import NotificationLog
from apps.notifications.tasks import (
    STUCK_AFTER_MINUTES,
    retry_notification,
    sweep_stuck_retries,
)
from apps.notifications.tests.recording import RecordingSmsBackend
from apps.orders.tests.factories import OrderFactory

pytestmark = pytest.mark.django_db

APPLY = "apps.notifications.tasks.retry_notification.apply_async"


@pytest.fixture(autouse=True)
def _clean_backend():
    """Empty the recorded list around every test."""
    RecordingSmsBackend.messages.clear()
    RecordingSmsBackend.codes.clear()
    yield
    RecordingSmsBackend.messages.clear()
    RecordingSmsBackend.codes.clear()


def _log(
    *,
    status: str = NotificationLog.Status.RETRY_PENDING,
    order=None,
    purpose: str = NotificationLog.Purpose.ORDER,
) -> NotificationLog:
    """One saved log row, ready for the sweep."""
    return NotificationLog.objects.create(
        channel=NotificationLog.Channel.SMS,
        purpose=purpose,
        event=EVENT_PLACED,
        order=order,
        recipient_hint="+91******3210",
        status=status,
    )


def _age(log: NotificationLog, minutes: int) -> None:
    """Move a row's updated_at into the past; auto_now forbids a save."""
    NotificationLog.objects.filter(pk=log.pk).update(
        updated_at=timezone.now() - timedelta(minutes=minutes)
    )


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------
def test_sweep_queues_row_pending_over_45_minutes() -> None:
    log = _log()
    _age(log, STUCK_AFTER_MINUTES + 5)

    with patch(APPLY) as apply_async:
        queued = sweep_stuck_retries()

    assert queued == 1
    apply_async.assert_called_once_with(args=[log.pk])


def test_sweep_ignores_recent_pending_row() -> None:
    # 44 minutes is one minute short of the age the sweep waits for, so this
    # row is left alone until it is really stuck.
    _age(_log(), 44)

    with patch(APPLY) as apply_async:
        queued = sweep_stuck_retries()

    assert queued == 0
    apply_async.assert_not_called()


def test_sweep_ignores_sent_and_gave_up_rows() -> None:
    sent = _log(status=NotificationLog.Status.SENT, order=OrderFactory())
    gave_up = _log(status=NotificationLog.Status.GAVE_UP, order=OrderFactory())
    _age(sent, 60)
    _age(gave_up, 60)

    with patch(APPLY) as apply_async:
        queued = sweep_stuck_retries()

    assert queued == 0
    apply_async.assert_not_called()


def test_sweep_caps_at_200() -> None:
    NotificationLog.objects.bulk_create(
        [
            NotificationLog(
                channel=NotificationLog.Channel.SMS,
                purpose=NotificationLog.Purpose.ORDER,
                event=EVENT_PLACED,
                status=NotificationLog.Status.RETRY_PENDING,
                recipient_hint="+91******3210",
            )
            for _index in range(201)
        ]
    )
    NotificationLog.objects.all().update(updated_at=timezone.now() - timedelta(hours=2))

    with patch(APPLY) as apply_async:
        queued = sweep_stuck_retries()

    assert queued == 200
    assert apply_async.call_count == 200


def test_sweep_survives_a_failing_queue_call() -> None:
    first = _log()
    second = _log()
    _age(first, 60)
    _age(second, 60)
    broken = first.pk

    def queue(args, **_kwargs) -> None:
        """Fail for one row only, the way a broken broker would."""
        if args == [broken]:
            raise RuntimeError("broker down")

    with patch(APPLY, side_effect=queue):
        queued = sweep_stuck_retries()

    assert queued == 1


def test_sweep_then_retry_sends_once(settings) -> None:
    settings.SMS_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
    order = OrderFactory()
    log = _log(order=order)
    _age(log, 60)

    with patch(APPLY) as apply_async:
        queued = sweep_stuck_retries()

    assert queued == 1
    apply_async.assert_called_once_with(args=[log.pk])

    # The queued task, run twice, sends once: the row lock sees SENT.
    assert retry_notification(log.pk) == NotificationLog.Status.SENT
    assert retry_notification(log.pk) == ""
    assert len(RecordingSmsBackend.messages) == 1


def test_failed_order_message_without_order_stays_failed(
    django_capture_on_commit_callbacks,
) -> None:
    def boom() -> str:
        raise RuntimeError("provider down")

    with (
        patch(APPLY) as apply_async,
        django_capture_on_commit_callbacks(execute=True),
    ):
        log = deliver(
            NotificationLog.Channel.EMAIL,
            NotificationLog.Purpose.STAFF,
            EVENT_STAFF_NEW_ORDER,
            None,
            "shop@example.com",
            boom,
        )

    assert log is not None
    # A retry rebuilds its message from the order, so this one waits for a
    # person instead of a task that could only fail.
    assert log.status == NotificationLog.Status.FAILED
    apply_async.assert_not_called()


def test_beat_entry_for_sweep_every_600_seconds(settings) -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["sweep-stuck-notification-retries"]

    assert entry["task"] == "apps.notifications.tasks.sweep_stuck_retries"
    assert entry["schedule"] == 600.0
