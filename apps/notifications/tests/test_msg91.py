"""Tests for the MSG91 Flow backend, the DLT variables and the interface (D88).

No test opens a connection: ``urlopen`` is patched in the MSG91 module, the
same way the payment-gateway tests patch theirs.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.notifications import msg91
from apps.notifications.messages import (
    CUSTOMER_EVENTS,
    EVENT_CANCELLED,
    EVENT_CREDIT_REMINDER,
    EVENT_DELIVERED,
    EVENT_OUT_FOR_DELIVERY,
    EVENT_PAYMENT_FAILED,
    EVENT_PLACED,
    EVENT_REFUND_PROCESSED,
    SMS_VARIABLE_MAX_LENGTH,
    SMS_VARIABLE_ORDER,
    OrderNotice,
    credit_reminder_notice,
    notice_variables,
)
from apps.notifications.msg91 import Msg91Error, Msg91SmsBackend
from apps.notifications.order_notices import notify_order_event
from apps.notifications.sms import ConsoleSmsBackend, SmsBackend
from apps.orders.tests.factories import OrderFactory

PHONE = "+919876543210"
CODE = "123456"
AUTH_KEY = "msg91-secret-key"
TEXT = "Badshah Stores: Order BS/26-27/000001 is placed."

# Every event this project can send by SMS, including the manual reminder.
SMS_EVENTS = (*CUSTOMER_EVENTS, EVENT_CREDIT_REMINDER)


class _Reply:
    """A tiny stand-in for urlopen's context-managed reply."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict[str, object] | None = None,
    error: BaseException | None = None,
) -> dict[str, object]:
    """Patch urlopen and return the dict the fake call fills in."""
    calls: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):  # stands in for urlopen
        calls["url"] = request.full_url
        # header_items() keeps urllib's capitalisation, so lower-case the names.
        calls["headers"] = {name.lower(): value for name, value in request.header_items()}
        calls["body"] = json.loads(request.data.decode("utf-8"))
        calls["timeout"] = timeout
        if error is not None:
            raise error
        return _Reply(payload or {})

    monkeypatch.setattr(msg91, "urlopen", fake_urlopen)
    return calls


def _configure(settings, **overrides: object) -> None:
    """A configured backend: one auth key and one template id per event."""
    settings.MSG91_AUTH_KEY = AUTH_KEY
    settings.MSG91_TEMPLATE_IDS = {
        "OTP": "tpl_otp",
        EVENT_PLACED: "tpl_placed",
        EVENT_REFUND_PROCESSED: "tpl_refund",
    }
    settings.MSG91_TIMEOUT_SECONDS = 10
    for name, value in overrides.items():
        setattr(settings, name, value)


def _notice(**overrides: object) -> OrderNotice:
    """One plain notice with every field a variable might read."""
    values: dict[str, object] = {
        "order_number": "BS/26-27/000001",
        "customer_name": "Asha Sharma",
        "payment_method": "COD",
        "grand_total": Decimal("230.00"),
        "amount_due_on_delivery": Decimal("230.00"),
        "promised_delivery_date": date(2026, 11, 7),
        "ship_city": "Indore",
        "ship_pincode": "452012",
        "ship_phone": PHONE,
        "refund_amount": Decimal("100.00"),
    }
    values.update(overrides)
    return OrderNotice(**values)


# ---------------------------------------------------------------------------
# The Flow request
# ---------------------------------------------------------------------------
def test_otp_posts_to_flow_url_with_authkey_header(settings, monkeypatch) -> None:
    _configure(settings)
    calls = _capture(monkeypatch, payload={"type": "success", "request_id": "req_1"})

    Msg91SmsBackend().send_otp(PHONE, CODE)

    assert calls["url"] == "https://control.msg91.com/api/v5/flow/"
    headers = calls["headers"]
    assert isinstance(headers, dict)
    assert headers["authkey"] == AUTH_KEY
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "application/json"


def test_phone_is_sent_without_plus(settings, monkeypatch) -> None:
    _configure(settings)
    calls = _capture(monkeypatch, payload={"type": "success"})

    Msg91SmsBackend().send_otp(PHONE, CODE)

    body = calls["body"]
    assert isinstance(body, dict)
    assert body["recipients"][0]["mobiles"] == "919876543210"


def test_notice_uses_template_id_for_event(settings, monkeypatch) -> None:
    _configure(settings)
    calls = _capture(monkeypatch, payload={"type": "success"})

    Msg91SmsBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "BS/26-27/000001"}, TEXT)

    body = calls["body"]
    assert isinstance(body, dict)
    assert body["template_id"] == "tpl_placed"


def test_notice_variables_are_in_the_body(settings, monkeypatch) -> None:
    _configure(settings)
    calls = _capture(monkeypatch, payload={"type": "success"})

    Msg91SmsBackend().send_notice(
        PHONE,
        EVENT_PLACED,
        {"VAR1": "BS/26-27/000001", "VAR2": "230.00"},
        TEXT,
    )

    body = calls["body"]
    assert isinstance(body, dict)
    recipient = body["recipients"][0]
    assert recipient["VAR1"] == "BS/26-27/000001"
    assert recipient["VAR2"] == "230.00"


def test_missing_auth_key_raises_improperly_configured(settings, monkeypatch) -> None:
    _configure(settings, MSG91_AUTH_KEY="")
    _capture(monkeypatch, payload={"type": "success"})

    with pytest.raises(ImproperlyConfigured):
        Msg91SmsBackend().send_otp(PHONE, CODE)


def test_missing_template_id_raises_improperly_configured(settings, monkeypatch) -> None:
    _configure(settings, MSG91_TEMPLATE_IDS={"OTP": "tpl_otp"})
    _capture(monkeypatch, payload={"type": "success"})

    with pytest.raises(ImproperlyConfigured) as raised:
        Msg91SmsBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "x"}, TEXT)

    # The event is named; the id is never printed.
    assert EVENT_PLACED in str(raised.value)


def test_http_error_raises(settings, monkeypatch) -> None:
    _configure(settings)
    _capture(
        monkeypatch,
        error=HTTPError("https://control.msg91.com/api/v5/flow/", 401, "No", None, None),
    )

    with pytest.raises(Msg91Error) as raised:
        Msg91SmsBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "x"}, TEXT)

    assert "401" in str(raised.value)


def test_network_error_raises(settings, monkeypatch) -> None:
    _configure(settings)
    _capture(monkeypatch, error=URLError("connection refused"))

    with pytest.raises(Msg91Error):
        Msg91SmsBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "x"}, TEXT)


def test_provider_error_type_raises(settings, monkeypatch) -> None:
    _configure(settings)
    _capture(monkeypatch, payload={"type": "error", "message": "Invalid template"})

    with pytest.raises(Msg91Error) as raised:
        Msg91SmsBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "x"}, TEXT)

    assert EVENT_PLACED in str(raised.value)


def test_provider_request_id_is_returned(settings, monkeypatch) -> None:
    _configure(settings)
    _capture(monkeypatch, payload={"type": "success", "request_id": "req_42"})

    request_id = Msg91SmsBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "x"}, TEXT)

    assert request_id == "req_42"


def test_send_message_is_refused(settings) -> None:
    _configure(settings)

    with pytest.raises(NotImplementedError):
        Msg91SmsBackend().send_message(PHONE, TEXT)


def test_error_text_never_contains_auth_key_or_phone(settings, monkeypatch) -> None:
    _configure(settings)
    _capture(monkeypatch, error=HTTPError("https://control.msg91.com/", 500, "No", None, None))

    with pytest.raises(Msg91Error) as raised:
        Msg91SmsBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "x"}, TEXT)

    message = str(raised.value)
    assert AUTH_KEY not in message
    assert PHONE not in message
    assert "919876543210" not in message


def test_timeout_setting_is_passed(settings, monkeypatch) -> None:
    _configure(settings, MSG91_TIMEOUT_SECONDS=3)
    calls = _capture(monkeypatch, payload={"type": "success"})

    Msg91SmsBackend().send_otp(PHONE, CODE)

    assert calls["timeout"] == 3


# ---------------------------------------------------------------------------
# The DLT variables
# ---------------------------------------------------------------------------
def test_every_event_has_a_variable_order() -> None:
    for event in SMS_EVENTS:
        assert event in SMS_VARIABLE_ORDER

    variables = notice_variables(EVENT_OUT_FOR_DELIVERY, _notice())

    assert variables == {"VAR1": "BS/26-27/000001", "VAR2": "230.00"}


def test_variables_are_cut_to_30_characters() -> None:
    long_number = "BS/" + "9" * 40

    variables = notice_variables(EVENT_PLACED, _notice(order_number=long_number))

    assert len(variables["VAR1"]) == SMS_VARIABLE_MAX_LENGTH
    assert variables["VAR1"] == long_number[:SMS_VARIABLE_MAX_LENGTH]


def test_variable_values_match_notice_fields() -> None:
    placed = notice_variables(EVENT_PLACED, _notice())
    refunded = notice_variables(EVENT_REFUND_PROCESSED, _notice())
    reminder = notice_variables(EVENT_CREDIT_REMINDER, credit_reminder_notice(Decimal("450.50")))

    assert placed == {
        "VAR1": "BS/26-27/000001",
        "VAR2": "230.00",
        "VAR3": "07 Nov 2026",
    }
    assert refunded == {"VAR1": "BS/26-27/000001", "VAR2": "100.00"}
    assert reminder == {"VAR1": "450.50"}


def test_dlt_doc_lists_every_event() -> None:
    # parents[3] walks tests -> notifications -> apps -> the repository root.
    root = Path(__file__).resolve().parents[3]
    text = (root / "docs" / "dlt_templates.md").read_text(encoding="utf-8")

    for event in SMS_EVENTS:
        assert event in text
    assert "OTP" in text
    assert "MSG91_TEMPLATE_IDS" in text


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------
class _TextRecordingBackend(SmsBackend):
    """A backend that records the free text its default send_notice sends."""

    texts: list[tuple[str, str]] = []

    def send_otp(self, phone: str, code: str) -> None:
        """Do nothing: this backend only exists to record a text."""
        return

    def send_message(self, phone: str, text: str) -> None:
        _TextRecordingBackend.texts.append((phone, text))


class NoticeRecordingBackend(SmsBackend):
    """A backend that records the event and the variables of one notice."""

    notices: list[tuple[str, str, dict[str, str], str]] = []

    def send_otp(self, phone: str, code: str) -> None:
        """Do nothing: this backend only records notices."""
        return

    def send_notice(
        self,
        phone: str,
        event: str,
        variables: dict[str, str],
        text: str,
    ) -> str:
        NoticeRecordingBackend.notices.append((phone, event, variables, text))
        return "req_interface"


def test_default_send_notice_calls_send_message() -> None:
    _TextRecordingBackend.texts.clear()

    result = _TextRecordingBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "x"}, TEXT)

    assert _TextRecordingBackend.texts == [(PHONE, TEXT)]
    assert result is None


def test_console_backend_still_prints_text(settings, capsys) -> None:
    settings.DEBUG = True

    ConsoleSmsBackend().send_notice(PHONE, EVENT_PLACED, {"VAR1": "x"}, TEXT)

    out = capsys.readouterr().out
    assert "[DEV SMS]" in out
    assert TEXT in out


@pytest.mark.django_db
def test_order_sms_uses_send_notice_with_event_and_variables(settings) -> None:
    settings.ORDER_SMS_ENABLED = True
    settings.SMS_BACKEND = f"{__name__}.NoticeRecordingBackend"
    NoticeRecordingBackend.notices.clear()
    order = OrderFactory()

    notify_order_event(order.pk, EVENT_PLACED)

    assert len(NoticeRecordingBackend.notices) == 1
    phone, event, variables, text = NoticeRecordingBackend.notices[0]
    assert phone == order.user.phone
    assert event == EVENT_PLACED
    assert variables["VAR1"] == order.order_number
    assert order.order_number in text


def test_cancelled_event_has_no_amount_variable() -> None:
    # The cancelled text names the order and nothing else.
    assert notice_variables(EVENT_CANCELLED, _notice()) == {"VAR1": "BS/26-27/000001"}
    assert notice_variables(EVENT_DELIVERED, _notice()) == {"VAR1": "BS/26-27/000001"}
    assert notice_variables(EVENT_PAYMENT_FAILED, _notice()) == {"VAR1": "BS/26-27/000001"}
