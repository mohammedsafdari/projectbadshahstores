"""Tests for the send log (D88).

Every row is checked for what it must never contain: a message text, a code, a
full phone number or a full email address.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import IntegrityError, models, transaction
from django.test import Client
from django.urls import reverse

from apps.notifications.delivery import deliver, error_summary, mask_email
from apps.notifications.email_codes import send_email_code
from apps.notifications.messages import EVENT_PLACED, EVENT_STAFF_NEW_ORDER, sms_text
from apps.notifications.models import NotificationLog
from apps.notifications.order_notices import (
    build_notice,
    notify_credit_reminder,
    notify_order_event,
)
from apps.notifications.tests.recording import RecordingSmsBackend
from apps.orders.tests.checkout_helpers import clear_settings, reset_settings, set_settings
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

RECORDING_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
FAILING_BACKEND = "apps.notifications.tests.recording.FailingSmsBackend"
PHONE = "+919876543210"
EMAIL = "asha@example.com"
CODE = "654321"
SHOP_EMAIL = "shop@example.com"


@pytest.fixture(autouse=True)
def _clean_environment():
    """A fresh settings row and an empty recorded list around every test."""
    reset_settings()
    RecordingSmsBackend.messages.clear()
    RecordingSmsBackend.codes.clear()
    yield
    RecordingSmsBackend.messages.clear()
    RecordingSmsBackend.codes.clear()
    clear_settings()


def _sms(send, purpose=NotificationLog.Purpose.ORDER, event=EVENT_PLACED) -> NotificationLog:
    """Run deliver for one SMS and return the row it wrote."""
    log = deliver(
        NotificationLog.Channel.SMS,
        purpose,
        event,
        None,
        PHONE,
        send,
    )
    assert log is not None
    return log


def _text_values(row: NotificationLog) -> list[str]:
    """Every text value of one row, for the never-stored checks."""
    values: list[str] = []
    # _meta.fields lists the model's own fields in declaration order.
    for field in NotificationLog._meta.fields:
        if isinstance(field, (models.CharField, models.TextField)):
            value = getattr(row, field.attname)
            if value:
                values.append(str(value))
    return values


def test_successful_sms_writes_sent_row() -> None:
    log = _sms(lambda: "req_1")

    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.channel == NotificationLog.Channel.SMS
    assert stored.purpose == NotificationLog.Purpose.ORDER
    assert stored.event == EVENT_PLACED
    assert stored.status == NotificationLog.Status.SENT
    assert stored.attempts == 1
    assert stored.provider_ref == "req_1"
    assert stored.error_summary == ""


def test_failed_sms_writes_failed_then_retry_pending_for_order() -> None:
    def boom() -> str:
        raise RuntimeError("provider down")

    log = _sms(boom)

    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.status == NotificationLog.Status.RETRY_PENDING
    assert stored.attempts == 1
    assert stored.error_summary.startswith("RuntimeError: provider down")


def test_retry_pending_save_includes_updated_at(monkeypatch) -> None:
    """The FAILED to RETRY_PENDING write must move ``updated_at`` too."""
    saved: list[list[str] | None] = []
    original = NotificationLog.save

    def spy(self, *args: object, **kwargs: object) -> None:
        # ``kwargs["update_fields"]`` is the list the caller passed.
        saved.append(kwargs.get("update_fields"))
        original(self, *args, **kwargs)

    monkeypatch.setattr(NotificationLog, "save", spy)

    def boom() -> str:
        raise RuntimeError("provider down")

    _sms(boom)

    assert ["status", "updated_at"] in saved


def test_otp_failure_is_logged_but_not_retried() -> None:
    def boom() -> None:
        raise RuntimeError("sms is down")

    log = _sms(boom, purpose=NotificationLog.Purpose.OTP, event="")

    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.purpose == NotificationLog.Purpose.OTP
    assert stored.status == NotificationLog.Status.FAILED


def test_credit_reminder_failure_is_logged_not_retried_and_returns_false(settings) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = FAILING_BACKEND
    user = UserFactory(phone=PHONE)

    sent = notify_credit_reminder(user, Decimal("450.50"))

    assert sent is False
    row = NotificationLog.objects.get()
    assert row.purpose == NotificationLog.Purpose.CREDIT_REMINDER
    assert row.status == NotificationLog.Status.FAILED


def test_email_code_failure_is_logged_not_retried(monkeypatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("smtp is down")

    monkeypatch.setattr("apps.notifications.email_codes.send_mail", boom)

    send_email_code(EMAIL, CODE)

    row = NotificationLog.objects.get()
    assert row.channel == NotificationLog.Channel.EMAIL
    assert row.purpose == NotificationLog.Purpose.EMAIL_CODE
    assert row.status == NotificationLog.Status.FAILED


def test_staff_email_failure_is_retried(settings) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    set_settings(email=SHOP_EMAIL)
    order = OrderFactory()

    with patch("apps.notifications.order_notices.send_mail", side_effect=RuntimeError):
        notify_order_event(order.pk, EVENT_STAFF_NEW_ORDER)

    row = NotificationLog.objects.get()
    assert row.purpose == NotificationLog.Purpose.STAFF
    assert row.status == NotificationLog.Status.RETRY_PENDING
    assert row.order_id == order.pk


def test_log_row_has_masked_phone_only() -> None:
    log = _sms(lambda: None)

    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.recipient_hint == "+91******3210"
    assert PHONE not in stored.recipient_hint
    assert "+919876543210" not in " ".join(_text_values(stored))


def test_log_row_has_masked_email_only() -> None:
    log = deliver(
        NotificationLog.Channel.EMAIL,
        NotificationLog.Purpose.ORDER,
        EVENT_PLACED,
        None,
        EMAIL,
        lambda: None,
    )
    assert log is not None

    stored = NotificationLog.objects.get(pk=log.pk)
    assert stored.recipient_hint == "a***@example.com"
    assert EMAIL not in " ".join(_text_values(stored))


def test_mask_email_keeps_the_first_letter_and_the_domain() -> None:
    assert mask_email(EMAIL) == "a***@example.com"
    assert mask_email("not-an-address") == "n***"


def test_error_summary_hides_long_digit_runs() -> None:
    summary = error_summary(RuntimeError("refused for 9876543210 at once"))

    assert "9876543210" not in summary
    assert "#" in summary
    # A five-digit run is not a phone or a code and stays readable.
    assert "int error 12345" in error_summary(RuntimeError("int error 12345"))


def test_log_never_stores_message_text_or_code(settings, mailoutbox) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.ORDER_EMAIL_ENABLED = False
    settings.SMS_BACKEND = RECORDING_BACKEND
    order = OrderFactory()
    text = sms_text(EVENT_PLACED, build_notice(order))

    notify_order_event(order.pk, EVENT_PLACED)
    send_email_code(EMAIL, CODE)

    assert NotificationLog.objects.count() == 2
    stored_values = " ".join(
        value for row in NotificationLog.objects.all() for value in _text_values(row)
    )
    assert text not in stored_values
    assert CODE not in stored_values
    assert "verification code is" not in stored_values
    # The one code email went to the address through the test outbox.
    assert len(mailoutbox) == 1


def test_deliver_never_raises_even_if_logging_fails(monkeypatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the database is down")

    monkeypatch.setattr(NotificationLog.objects, "create", boom)

    result = deliver(
        NotificationLog.Channel.SMS,
        NotificationLog.Purpose.ORDER,
        EVENT_PLACED,
        None,
        PHONE,
        lambda: "req_1",
    )

    # The send happened; only the record of it failed.
    assert result is None


def test_sms_switch_off_writes_no_row(settings) -> None:
    settings.ORDER_SMS_ENABLED = False
    settings.ORDER_EMAIL_ENABLED = False
    order = OrderFactory()

    notify_order_event(order.pk, EVENT_PLACED)

    assert NotificationLog.objects.count() == 0


def test_attempts_constraint() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationLog.objects.create(
            channel=NotificationLog.Channel.SMS,
            purpose=NotificationLog.Purpose.ORDER,
            status=NotificationLog.Status.SENT,
            attempts=0,
        )


def test_admin_is_read_only() -> None:
    log = _sms(lambda: None)
    # The shop's user model logs in by phone, so pytest-django's admin_client
    # cannot be used; a superuser of the project's own model is enough.
    staff = UserFactory(is_staff=True, is_superuser=True)
    client = Client()
    client.force_login(staff)
    changelist = reverse("admin:notifications_notificationlog_changelist")
    add_url = reverse("admin:notifications_notificationlog_add")
    change_url = reverse("admin:notifications_notificationlog_change", args=[log.pk])

    assert client.get(changelist).status_code == 200
    assert client.get(add_url).status_code == 403
    # A detail page opens read-only, but a save is refused.
    assert client.get(change_url).status_code == 200
    assert client.post(change_url, {}).status_code == 403
