"""Tests for sending order notices (Slice 38a, D69)."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings

from apps.notifications.messages import (
    CUSTOMER_EVENTS,
    EVENT_PLACED,
    EVENT_STAFF_NEW_ORDER,
    sms_text,
)
from apps.notifications.order_notices import build_notice, notify_order_event
from apps.notifications.tests.recording import RecordingSmsBackend
from apps.orders.tests.checkout_helpers import clear_settings, reset_settings, set_settings
from apps.orders.tests.factories import OrderFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

RECORDING_BACKEND = "apps.notifications.tests.recording.RecordingSmsBackend"
FAILING_BACKEND = "apps.notifications.tests.recording.FailingSmsBackend"
SHOP_EMAIL = "shop@example.com"
CUSTOMER_EMAIL = "asha@example.com"


@pytest.fixture(autouse=True)
def notice_environment():
    """Give every test a clean settings row, cache and recorded SMS list."""
    # Everything before ``yield`` runs before the test; everything after runs
    # at the end, even when the test fails.
    reset_settings()
    RecordingSmsBackend.messages.clear()
    RecordingSmsBackend.codes.clear()
    yield
    RecordingSmsBackend.messages.clear()
    RecordingSmsBackend.codes.clear()
    clear_settings()


def _order_with_user(**user_fields: object):
    """One saved order whose customer is a fresh user."""
    return OrderFactory(user=UserFactory(**user_fields))


def test_placed_sms_goes_to_the_account_phone_when_enabled(settings) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    order = _order_with_user()

    notify_order_event(order.pk, EVENT_PLACED)

    assert len(RecordingSmsBackend.messages) == 1
    phone, text = RecordingSmsBackend.messages[0]
    assert phone == order.user.phone
    assert order.order_number in text
    assert "Rs 230.00" in text


def test_no_sms_when_order_sms_is_disabled(settings) -> None:
    settings.ORDER_SMS_ENABLED = False
    settings.SMS_BACKEND = RECORDING_BACKEND
    order = _order_with_user()

    notify_order_event(order.pk, EVENT_PLACED)

    assert RecordingSmsBackend.messages == []


def test_placed_email_goes_to_the_customer_when_enabled(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=CUSTOMER_EMAIL)

    notify_order_event(order.pk, EVENT_PLACED)

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [CUSTOMER_EMAIL]
    assert order.order_number in mailoutbox[0].subject


def test_no_customer_email_when_order_email_is_disabled(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = False
    order = _order_with_user(email=CUSTOMER_EMAIL)

    notify_order_event(order.pk, EVENT_PLACED)

    assert mailoutbox == []


def test_no_customer_email_when_the_customer_has_no_email(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=None)

    notify_order_event(order.pk, EVENT_PLACED)

    assert mailoutbox == []


def test_staff_new_order_email_goes_to_the_shop_email(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    set_settings(email=SHOP_EMAIL)
    order = _order_with_user(full_name="Asha Sharma")

    notify_order_event(order.pk, EVENT_STAFF_NEW_ORDER)

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [SHOP_EMAIL]
    assert mailoutbox[0].subject.startswith(f"New order {order.order_number}")


def test_staff_new_order_sends_no_email_when_shop_email_is_blank(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user()

    notify_order_event(order.pk, EVENT_STAFF_NEW_ORDER)

    assert mailoutbox == []


def test_staff_new_order_sends_no_sms(settings) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    order = _order_with_user()

    notify_order_event(order.pk, EVENT_STAFF_NEW_ORDER)

    assert RecordingSmsBackend.messages == []


def test_sms_failure_is_swallowed_and_email_still_sent(settings, mailoutbox) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = FAILING_BACKEND
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=CUSTOMER_EMAIL)

    notify_order_event(order.pk, EVENT_PLACED)

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [CUSTOMER_EMAIL]


def test_email_failure_is_swallowed_and_sms_still_sent(settings, monkeypatch) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=CUSTOMER_EMAIL)

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("boom")

    # Replace the name the module imported, which is what it calls at run time.
    monkeypatch.setattr("apps.notifications.order_notices.send_mail", boom)

    notify_order_event(order.pk, EVENT_PLACED)

    assert len(RecordingSmsBackend.messages) == 1


def test_missing_order_is_ignored(settings, mailoutbox, caplog) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    settings.ORDER_EMAIL_ENABLED = True

    with caplog.at_level(logging.WARNING):
        notify_order_event(999999, EVENT_PLACED)

    assert RecordingSmsBackend.messages == []
    assert mailoutbox == []
    assert "event=ORDER_PLACED" in caplog.text
    assert "order_id=999999" in caplog.text


def test_unknown_event_is_ignored(settings, mailoutbox, caplog) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=CUSTOMER_EMAIL)

    with caplog.at_level(logging.WARNING):
        notify_order_event(order.pk, "ORDER_TELEPORTED")

    assert RecordingSmsBackend.messages == []
    assert mailoutbox == []
    assert "ORDER_TELEPORTED" in caplog.text


def test_failure_is_logged_without_phone_or_message_text(settings, caplog) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = FAILING_BACKEND
    order = _order_with_user(email=CUSTOMER_EMAIL)
    text = sms_text(EVENT_PLACED, build_notice(order))

    with caplog.at_level(logging.ERROR):
        notify_order_event(order.pk, EVENT_PLACED)

    assert "event=ORDER_PLACED" in caplog.text
    assert f"order_id={order.pk}" in caplog.text
    assert "channel=sms" in caplog.text
    # The log line must never carry personal data or the message itself.
    assert order.user.phone not in caplog.text
    assert CUSTOMER_EMAIL not in caplog.text
    assert text not in caplog.text


def test_each_customer_event_sends_its_own_text(settings) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    order = _order_with_user()
    notice = build_notice(order)
    texts: set[str] = set()

    for event in CUSTOMER_EVENTS:
        RecordingSmsBackend.messages.clear()
        RecordingSmsBackend.codes.clear()
        notify_order_event(order.pk, event)

        assert RecordingSmsBackend.messages == [
            (order.user.phone, sms_text(event, notice)),
        ]
        texts.add(sms_text(event, notice))

    # Four events must give four different texts, not one reused wording.
    assert len(texts) == len(CUSTOMER_EVENTS)


def test_order_channels_are_off_by_default_in_test_settings() -> None:
    # Read Django's settings directly, so the ``settings`` fixture cannot
    # switch anything on for this test.
    assert django_settings.ORDER_SMS_ENABLED is False
    assert django_settings.ORDER_EMAIL_ENABLED is False


def test_staff_new_order_sends_no_email_when_order_email_is_disabled(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = False
    set_settings(email=SHOP_EMAIL)
    order = _order_with_user()

    notify_order_event(order.pk, EVENT_STAFF_NEW_ORDER)

    assert mailoutbox == []


def test_customer_without_an_email_address_causes_no_error_log(
    settings, mailoutbox, caplog
) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=None)

    with caplog.at_level(logging.ERROR):
        notify_order_event(order.pk, EVENT_PLACED)

    assert RecordingSmsBackend.messages == []
    assert mailoutbox == []
    # A list comprehension keeps only ERROR records; a blank address is not one.
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []


def test_unknown_event_sends_nothing_and_logs_a_warning(settings, mailoutbox, caplog) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = RECORDING_BACKEND
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=CUSTOMER_EMAIL)

    with caplog.at_level(logging.WARNING):
        notify_order_event(order.pk, "ORDER_TELEPORTED")

    assert RecordingSmsBackend.messages == []
    assert mailoutbox == []
    assert "event=ORDER_TELEPORTED" in caplog.text
    assert f"order_id={order.pk}" in caplog.text


def test_blank_account_name_falls_back_to_the_ship_name(settings, mailoutbox) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=CUSTOMER_EMAIL, full_name="")

    notify_order_event(order.pk, EVENT_PLACED)

    assert len(mailoutbox) == 1
    assert mailoutbox[0].body.startswith(f"Hello {order.ship_name},")


def test_staff_email_failure_is_swallowed_and_logged(settings, caplog) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    set_settings(email=SHOP_EMAIL)
    order = _order_with_user()

    # Both context managers stay active for the call and the log capture.
    with (
        patch("apps.notifications.order_notices.send_mail", side_effect=RuntimeError),
        caplog.at_level(logging.ERROR),
    ):
        notify_order_event(order.pk, EVENT_STAFF_NEW_ORDER)

    assert "channel=staff_email" in caplog.text


def test_staff_new_order_does_not_call_send_mail_when_shop_email_is_blank(settings) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user()

    with patch("apps.notifications.order_notices.send_mail") as send_mail_mock:
        notify_order_event(order.pk, EVENT_STAFF_NEW_ORDER)

    send_mail_mock.assert_not_called()


def test_failure_while_loading_the_order_is_swallowed_and_logged(settings, caplog) -> None:
    settings.ORDER_EMAIL_ENABLED = True
    order = _order_with_user(email=CUSTOMER_EMAIL)

    # Both context managers stay active for the call and the log capture.
    with (
        patch("apps.notifications.order_notices.build_notice", side_effect=RuntimeError),
        caplog.at_level(logging.ERROR),
    ):
        notify_order_event(order.pk, EVENT_PLACED)

    assert "channel=load" in caplog.text
