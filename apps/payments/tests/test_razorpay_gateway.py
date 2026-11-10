"""Tests for the one Razorpay module (D76, D77).

No test opens a connection: ``_post_json`` and ``_get_json`` are replaced, or
``urlopen`` is patched, and the webhook tests build their own signed body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import pytest

from apps.payments import razorpay_gateway
from apps.payments.gateway import (
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    GatewayNotConfigured,
    InvalidWebhookSignature,
    PaymentGatewayError,
)
from apps.payments.razorpay_gateway import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    RazorpayGateway,
)
from apps.payments.services import handle_gateway_event
from apps.payments.tests.helpers import (
    NOW,
    clear_settings,
    factory_online_order,
    online_order,
    reset_settings,
    start_payment,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """Create the settings row and empty the cache before every test."""
    reset_settings()
    yield
    clear_settings()


class _JsonReply:
    """A tiny stand-in for urlopen's context-managed reply."""

    def __enter__(self) -> _JsonReply:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return b"not-json"


def _body(**values: object) -> bytes:
    return json.dumps(values).encode("utf-8")


def _sign(body: bytes, secret: str) -> str:
    # hmac.new takes the key, the body and the hash; hexdigest is the header value.
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _headers(body: bytes, secret: str, *, event_id: str = "") -> dict[str, str]:
    return {
        SIGNATURE_HEADER: _sign(body, secret),
        EVENT_ID_HEADER: event_id,
    }


def _entity(**values: object) -> dict[str, object]:
    return {"payment": {"entity": values}}


def _payment_item(**values: object) -> dict[str, object]:
    return values


def test_missing_credentials_are_a_clear_refusal(settings) -> None:
    settings.RAZORPAY_KEY_ID = ""
    settings.RAZORPAY_KEY_SECRET = ""

    with pytest.raises(GatewayNotConfigured):
        RazorpayGateway().public_key_id()


def test_public_key_id_returns_only_the_key(settings) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"

    assert RazorpayGateway().public_key_id() == "rzp_test_key"


def test_create_session_sends_paise_and_reads_the_order_id(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"
    order = factory_online_order()
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post(url: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((url, payload))
        return {"id": "order_razorpay_1"}

    monkeypatch.setattr(razorpay_gateway, "_post_json", fake_post)

    session = RazorpayGateway().create_session(order)

    assert session.gateway_order_id == "order_razorpay_1"
    assert session.amount == Decimal("230.00")
    assert session.key_id == "rzp_test_key"
    assert calls[0][0] == razorpay_gateway.ORDERS_URL
    assert calls[0][1]["amount"] == 23000
    assert calls[0][1]["currency"] == "INR"


def test_create_session_refuses_a_reply_without_an_order_id(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"
    monkeypatch.setattr(razorpay_gateway, "_post_json", lambda url, payload: {})

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().create_session(factory_online_order())


def test_a_webhook_with_a_valid_signature_is_read(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event=PAYMENT_CAPTURED,
        payload=_entity(id="pay_1", order_id="order_1", amount=23000, method="upi"),
    )

    event = RazorpayGateway().parse_webhook(
        body,
        _headers(body, "webhook_secret", event_id="evt_1"),
    )

    assert event is not None
    assert event.event_id == "evt_1"
    assert event.kind == PAYMENT_CAPTURED
    assert event.gateway_order_id == "order_1"
    assert event.gateway_payment_id == "pay_1"
    assert event.amount == Decimal("230.00")
    assert event.method == "upi"


def test_the_provider_reads_its_own_signature_header(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event=PAYMENT_CAPTURED,
        payload=_entity(id="pay_1", order_id="order_1", amount=100),
    )

    # The right value in any other header is not a signature.
    with pytest.raises(InvalidWebhookSignature):
        RazorpayGateway().parse_webhook(
            body,
            {"X-Other-Signature": _sign(body, "webhook_secret")},
        )


def test_paise_are_turned_into_rupees_with_two_places(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event=PAYMENT_CAPTURED,
        payload=_entity(id="pay_1", order_id="order_1", amount=1),
    )

    event = RazorpayGateway().parse_webhook(body, _headers(body, "webhook_secret"))

    assert event is not None
    assert event.amount == Decimal("0.01")


def test_a_missing_event_header_falls_back_to_the_kind_and_payment_id(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event=PAYMENT_CAPTURED,
        payload=_entity(id="pay_9", order_id="order_1", amount=100),
    )

    event = RazorpayGateway().parse_webhook(
        body,
        {SIGNATURE_HEADER: _sign(body, "webhook_secret")},
    )

    assert event is not None
    assert event.event_id == f"{PAYMENT_CAPTURED}:pay_9"


def test_order_paid_maps_to_a_captured_event(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event="order.paid",
        payload=_entity(
            id="pay_2",
            order_id="order_1",
            amount=23000,
            method="card",
            status="captured",
        ),
    )

    event = RazorpayGateway().parse_webhook(
        body,
        _headers(body, "webhook_secret", event_id="evt_paid"),
    )

    assert event is not None
    assert event.kind == PAYMENT_CAPTURED
    assert event.gateway_payment_id == "pay_2"
    assert event.amount == Decimal("230.00")


def test_order_paid_and_payment_captured_get_different_fallback_ids(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    paid = _body(
        event="order.paid",
        payload=_entity(id="pay_2", order_id="order_1", amount=100),
    )
    captured = _body(
        event=PAYMENT_CAPTURED,
        payload=_entity(id="pay_2", order_id="order_1", amount=100),
    )

    paid_event = RazorpayGateway().parse_webhook(paid, _headers(paid, "webhook_secret"))
    captured_event = RazorpayGateway().parse_webhook(
        captured,
        _headers(captured, "webhook_secret"),
    )

    assert paid_event is not None
    assert captured_event is not None
    assert paid_event.event_id == "order.paid:pay_2"
    assert captured_event.event_id == "payment.captured:pay_2"
    assert paid_event.event_id != captured_event.event_id


def test_a_wrong_signature_is_refused(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(event=PAYMENT_CAPTURED, payload=_entity(id="pay_1"))

    with pytest.raises(InvalidWebhookSignature):
        RazorpayGateway().parse_webhook(body, {SIGNATURE_HEADER: "not-the-signature"})


def test_a_missing_webhook_secret_is_a_configuration_error(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = ""

    with pytest.raises(GatewayNotConfigured):
        RazorpayGateway().parse_webhook(b"{}", {SIGNATURE_HEADER: "signature"})


def test_a_signed_body_that_is_not_json_is_refused(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = b"not-json"

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().parse_webhook(body, _headers(body, "webhook_secret"))


def test_an_unknown_event_is_ignored(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(event="payment.refunded", payload=_entity(id="pay_1"))

    event = RazorpayGateway().parse_webhook(body, _headers(body, "webhook_secret"))

    assert event is None


def test_a_webhook_without_a_payment_entity_is_refused(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(event=PAYMENT_CAPTURED, payload={})

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().parse_webhook(body, _headers(body, "webhook_secret"))


def test_the_failed_event_kind_is_accepted(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event=PAYMENT_FAILED,
        payload=_entity(id="pay_1", order_id="order_1", amount=100),
    )

    event = RazorpayGateway().parse_webhook(body, _headers(body, "webhook_secret"))

    assert event is not None
    assert event.kind == PAYMENT_FAILED


def test_the_stored_payload_keeps_only_the_safe_fields(settings) -> None:
    settings.PAYMENT_PROVIDER = "fake"
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    _user, order, _row = online_order()
    payment = start_payment(order).payment
    body = _body(
        event=PAYMENT_CAPTURED,
        payload=_entity(
            id="pay_safe",
            order_id=payment.gateway_order_id,
            amount=23000,
            status="captured",
            method="upi",
            currency="INR",
            phone="+919876543210",
            email="asha@example.com",
            vpa="asha@upi",
            card={"number": "4111111111111111", "bank": "HDFC"},
            notes={"address": "12 Test Road"},
        ),
    )
    event = RazorpayGateway().parse_webhook(
        body,
        _headers(body, "webhook_secret", event_id="evt_safe"),
    )
    assert event is not None

    webhook = handle_gateway_event(event, provider="razorpay", now=NOW)

    payment.refresh_from_db()
    stored = json.dumps([webhook.payload, payment.raw_response])
    for personal in (
        "9876543210",
        "asha@example.com",
        "asha@upi",
        "4111111111111111",
        "HDFC",
        "12 Test Road",
    ):
        assert personal not in stored
    assert set(webhook.payload) == {
        "event",
        "payment_id",
        "order_id",
        "status",
        "method",
        "amount",
        "currency",
        "error_code",
        "error_description",
        "error_reason",
    }


def test_fetch_reads_only_captured_and_failed_payments(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"
    urls: list[str] = []

    def fake_get(url: str) -> dict[str, object]:
        urls.append(url)
        return {
            "items": [
                _payment_item(
                    id="pay_1",
                    order_id="order_1",
                    status="captured",
                    amount=23000,
                    method="upi",
                    currency="INR",
                ),
                _payment_item(
                    id="pay_2",
                    order_id="order_1",
                    status="failed",
                    amount=23000,
                    method="card",
                    currency="INR",
                    error={
                        "code": "BAD_CARD",
                        "description": "declined",
                        "reason": "payment_failed",
                    },
                ),
                _payment_item(id="pay_3", status="authorized", amount=23000),
                _payment_item(id="pay_4", status="created", amount=23000),
            ]
        }

    monkeypatch.setattr(razorpay_gateway, "_get_json", fake_get)

    events = RazorpayGateway().fetch_order_payments("order_1")

    assert urls == [f"{razorpay_gateway.ORDERS_URL}/order_1/payments"]
    assert [event.kind for event in events] == [PAYMENT_CAPTURED, PAYMENT_FAILED]
    assert events[0].gateway_payment_id == "pay_1"
    assert events[0].amount == Decimal("230.00")
    assert events[1].safe["error_code"] == "BAD_CARD"
    assert events[1].safe["error_description"] == "declined"
    assert events[1].safe["error_reason"] == "payment_failed"


def test_fetch_returns_nothing_for_an_order_without_payments(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"
    monkeypatch.setattr(razorpay_gateway, "_get_json", lambda url: {"items": []})

    assert RazorpayGateway().fetch_order_payments("order_1") == []


def test_fetch_refuses_a_reply_without_a_list(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"
    monkeypatch.setattr(razorpay_gateway, "_get_json", lambda url: {"items": "nope"})

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().fetch_order_payments("order_1")


def test_fetch_turns_a_timeout_into_a_gateway_error(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"

    def slow(request: object, timeout: int) -> object:
        raise TimeoutError("slow")

    monkeypatch.setattr(razorpay_gateway, "urlopen", slow)

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().fetch_order_payments("order_1")


def test_fetch_turns_bad_json_into_a_gateway_error(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"
    monkeypatch.setattr(
        razorpay_gateway,
        "urlopen",
        lambda request, timeout: _JsonReply(),
    )

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().fetch_order_payments("order_1")
