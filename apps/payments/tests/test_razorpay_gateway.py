"""Tests for the one Razorpay module (D76, D77).

No test opens a connection: ``_post_json`` is replaced, and the webhook tests
build their own signed body.
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
from apps.payments.razorpay_gateway import RazorpayGateway
from apps.payments.tests.helpers import factory_online_order

pytestmark = pytest.mark.django_db


def _body(**values: object) -> bytes:
    return json.dumps(values).encode("utf-8")


def _sign(body: bytes, secret: str) -> str:
    # hmac.new takes the key, the body and the hash; hexdigest is the header value.
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _entity(**values: object) -> dict[str, object]:
    return {"payment": {"entity": values}}


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
        _sign(body, "webhook_secret"),
        event_id="evt_1",
    )

    assert event.event_id == "evt_1"
    assert event.kind == PAYMENT_CAPTURED
    assert event.gateway_order_id == "order_1"
    assert event.gateway_payment_id == "pay_1"
    assert event.amount == Decimal("230.00")
    assert event.method == "upi"


def test_paise_are_turned_into_rupees_with_two_places(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event=PAYMENT_CAPTURED,
        payload=_entity(id="pay_1", order_id="order_1", amount=1),
    )

    event = RazorpayGateway().parse_webhook(body, _sign(body, "webhook_secret"))

    assert event.amount == Decimal("0.01")


def test_a_missing_event_header_falls_back_to_the_payment_id(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event=PAYMENT_CAPTURED,
        payload=_entity(id="pay_9", order_id="order_1", amount=100),
    )

    event = RazorpayGateway().parse_webhook(body, _sign(body, "webhook_secret"))

    assert event.event_id == f"{PAYMENT_CAPTURED}:pay_9"


def test_a_wrong_signature_is_refused(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(event=PAYMENT_CAPTURED, payload=_entity(id="pay_1"))

    with pytest.raises(InvalidWebhookSignature):
        RazorpayGateway().parse_webhook(body, "not-the-signature")


def test_a_missing_webhook_secret_is_a_configuration_error(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = ""

    with pytest.raises(GatewayNotConfigured):
        RazorpayGateway().parse_webhook(b"{}", "signature")


def test_a_signed_body_that_is_not_json_is_refused(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = b"not-json"

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().parse_webhook(body, _sign(body, "webhook_secret"))


def test_an_unknown_event_is_refused(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(event="payment.refunded", payload=_entity(id="pay_1"))

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().parse_webhook(body, _sign(body, "webhook_secret"))


def test_a_webhook_without_a_payment_entity_is_refused(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(event=PAYMENT_CAPTURED, payload={})

    with pytest.raises(PaymentGatewayError):
        RazorpayGateway().parse_webhook(body, _sign(body, "webhook_secret"))


def test_the_failed_event_kind_is_accepted(settings) -> None:
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook_secret"
    body = _body(
        event=PAYMENT_FAILED,
        payload=_entity(id="pay_1", order_id="order_1", amount=100),
    )

    event = RazorpayGateway().parse_webhook(body, _sign(body, "webhook_secret"))

    assert event.kind == PAYMENT_FAILED
