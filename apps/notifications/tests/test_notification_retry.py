"""Tests for the retry of a failed order message (D88).

The task is called directly, so no broker is needed; ``apply_async`` is
patched wherever a second attempt is queued.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.notifications.delivery import deliver
from apps.notifications.messages import (
    EVENT_PLACED,
    EVENT_REFUND_PROCESSED,
    EVENT_STAFF_NEW_ORDER,
)
from apps.notifications.models import NotificationLog
from apps.notifications.tasks import retry_notification
from apps.notifications.tests.recording import RecordingSmsBackend
from apps.orders.tests.checkout_helpers import set_settings
from apps.orders.tests.factories import OrderFactory
from apps.payments.models import Payment, Refund

RECORDING_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
FAILING_BACKEND = "apps.notifications.tests.recording.FailingSmsBackend"
APPLY = "apps.notifications.tasks.retry_notification.apply_async"


def _log(
    order,
    *,
    event: str = EVENT_PLACED,
    channel: str = NotificationLog.Channel.SMS,
    purpose: str = NotificationLog.Purpose.ORDER,
    status: str = NotificationLog.Status.RETRY_PENDING,
    attempts: int = 1,
) -> NotificationLog:
    """One saved log row, ready to be retried."""
    return NotificationLog.objects.create(
        channel=channel,
        purpose=purpose,
        event=event,
        order=order,
        recipient_hint="+91******3210",
        status=status,
        attempts=attempts,
    )


@pytest.fixture(autouse=True)
def _clean_backend():
    """Empty the recorded list around every test."""
    RecordingSmsBackend.messages.clear()
    RecordingSmsBackend.codes.clear()
    yield
    RecordingSmsBackend.messages.clear()
    RecordingSmsBackend.codes.clear()


@pytest.mark.django_db
def test_retry_sends_and_marks_sent(settings) -> None:
    settings.SMS_BACKEND = RECORDING_BACKEND
    order = OrderFactory()
    log = _log(order)

    result = retry_notification(log.pk)

    assert result == NotificationLog.Status.SENT
    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.status == NotificationLog.Status.SENT
    assert stored.attempts == 1
    assert len(RecordingSmsBackend.messages) == 1
    assert order.order_number in RecordingSmsBackend.messages[0][1]


@pytest.mark.django_db
def test_retry_failure_schedules_next_attempt_with_backoff(
    settings, django_capture_on_commit_callbacks
) -> None:
    settings.SMS_BACKEND = FAILING_BACKEND
    log = _log(OrderFactory())

    with (
        patch(APPLY) as apply_async,
        django_capture_on_commit_callbacks(execute=True),
    ):
        result = retry_notification(log.pk)

    assert result == NotificationLog.Status.RETRY_PENDING
    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.attempts == 2
    apply_async.assert_called_once_with(args=[log.pk], countdown=300)


@pytest.mark.django_db
def test_third_failure_marks_gave_up(settings, django_capture_on_commit_callbacks) -> None:
    settings.SMS_BACKEND = FAILING_BACKEND
    log = _log(OrderFactory(), attempts=2)

    with (
        patch(APPLY) as apply_async,
        django_capture_on_commit_callbacks(execute=True),
    ):
        result = retry_notification(log.pk)

    assert result == NotificationLog.Status.GAVE_UP
    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.attempts == 3
    apply_async.assert_not_called()


@pytest.mark.django_db
def test_retry_ignores_a_row_that_is_not_retry_pending(settings) -> None:
    settings.SMS_BACKEND = RECORDING_BACKEND
    log = _log(OrderFactory(), status=NotificationLog.Status.SENT)

    result = retry_notification(log.pk)

    assert result == ""
    assert RecordingSmsBackend.messages == []
    assert NotificationLog.objects.get(pk=log.pk).status == NotificationLog.Status.SENT


@pytest.mark.django_db
def test_retry_twice_sends_once(settings) -> None:
    settings.SMS_BACKEND = RECORDING_BACKEND
    log = _log(OrderFactory())

    first = retry_notification(log.pk)
    second = retry_notification(log.pk)

    assert first == NotificationLog.Status.SENT
    assert second == ""
    assert len(RecordingSmsBackend.messages) == 1


@pytest.mark.django_db
def test_retry_with_deleted_order_marks_gave_up(settings) -> None:
    settings.SMS_BACKEND = RECORDING_BACKEND
    # The log's order is SET_NULL, so a deleted order leaves the row with no
    # order at all, which is exactly the state this test builds.
    log = _log(None)

    result = retry_notification(log.pk)

    assert result == NotificationLog.Status.GAVE_UP
    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.status == NotificationLog.Status.GAVE_UP
    assert stored.error_summary.startswith("OrderMissing")
    assert RecordingSmsBackend.messages == []


@pytest.mark.django_db
def test_retry_rebuilds_refund_notice_with_latest_refund(settings) -> None:
    settings.SMS_BACKEND = RECORDING_BACKEND
    order = OrderFactory()
    payment = Payment.objects.create(
        order=order,
        provider="fake",
        gateway_order_id="pay_retry_1",
        amount=Decimal("230.00"),
        status=Payment.Status.CAPTURED,
        captured_at=timezone.now(),
    )
    # A PROCESSED refund must carry its processed time, or the row is refused.
    Refund.objects.create(
        payment=payment,
        kind=Refund.Kind.SHOP_CANCEL,
        status=Refund.Status.PROCESSED,
        amount=Decimal("230.00"),
        idempotency_key="retry-key-1",
        processed_at=timezone.now(),
    )
    Refund.objects.create(
        payment=payment,
        kind=Refund.Kind.CUSTOMER_CANCEL,
        status=Refund.Status.PROCESSED,
        amount=Decimal("200.00"),
        fee_amount=Decimal("30.00"),
        idempotency_key="retry-key-2",
        processed_at=timezone.now(),
    )
    log = _log(order, event=EVENT_REFUND_PROCESSED)

    retry_notification(log.pk)

    text = RecordingSmsBackend.messages[0][1]
    assert "Rs 200.00 has been refunded" in text
    assert "Rs 30.00 cancellation fee was kept" in text


@pytest.mark.django_db
def test_staff_email_retry_goes_to_the_shop(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    news = NotificationLog.objects.create(
        channel=NotificationLog.Channel.EMAIL,
        purpose=NotificationLog.Purpose.STAFF,
        event=EVENT_STAFF_NEW_ORDER,
        order=OrderFactory(),
        recipient_hint="s***@example.com",
        status=NotificationLog.Status.RETRY_PENDING,
    )
    set_settings(email="shop@example.com")

    retry_notification(news.pk)

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["shop@example.com"]


@pytest.mark.django_db
def test_retry_updates_updated_at(settings) -> None:
    settings.SMS_BACKEND = RECORDING_BACKEND
    log = _log(OrderFactory())
    long_ago = timezone.now() - timedelta(minutes=10)
    # updated_at is auto_now, so only a queryset update can move it backwards.
    NotificationLog.objects.filter(pk=log.pk).update(updated_at=long_ago)

    retry_notification(log.pk)

    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.updated_at > long_ago


@pytest.mark.django_db(transaction=True)
def test_retry_lock_blocks_a_second_task(settings) -> None:
    settings.SMS_BACKEND = RECORDING_BACKEND
    log = _log(OrderFactory())
    log_pk = log.pk

    locked = threading.Event()
    errors: list[BaseException] = []

    def hold_the_row() -> None:
        """Lock the row, mark it SENT, then commit after a short wait."""
        try:
            with transaction.atomic():
                row = NotificationLog.objects.select_for_update().get(pk=log_pk)
                row.status = NotificationLog.Status.SENT
                row.save(update_fields=["status", "updated_at"])
                locked.set()
                time.sleep(0.5)
            # Leaving the block commits, which releases the lock.
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    def retry_while_locked() -> None:
        """The task must wait, then see SENT and send nothing."""
        try:
            assert locked.wait(timeout=10)
            retry_notification(log_pk)
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    first = threading.Thread(target=hold_the_row)
    second = threading.Thread(target=retry_while_locked)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    assert not errors, errors
    # Without the row lock the task would have read RETRY_PENDING and sent.
    assert RecordingSmsBackend.messages == []
    assert NotificationLog.objects.get(pk=log_pk).status == NotificationLog.Status.SENT


@pytest.mark.django_db
def test_retry_is_queued_only_after_commit(
    django_capture_on_commit_callbacks,
) -> None:
    def boom() -> str:
        raise RuntimeError("provider down")

    # execute=True runs every callback when the block that captured it exits,
    # which is what a real commit does.
    with (
        patch(APPLY) as apply_async,
        django_capture_on_commit_callbacks(execute=True),
    ):
        log = deliver(
            NotificationLog.Channel.SMS,
            NotificationLog.Purpose.ORDER,
            EVENT_PLACED,
            None,
            "+919876543210",
            boom,
        )
        # Nothing is queued while the transaction is still open.
        apply_async.assert_not_called()

    assert log is not None
    apply_async.assert_called_once_with(args=[log.pk], countdown=60)
