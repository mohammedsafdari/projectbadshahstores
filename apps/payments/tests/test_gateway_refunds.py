"""Tests for the refund side of the provider boundary (D78)."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from urllib.error import HTTPError

import pytest

from apps.payments import razorpay_gateway
from apps.payments.fake_gateway import SIGNATURE_HEADER as FAKE_SIGNATURE_HEADER
from apps.payments.fake_gateway import (
    FakeGateway,
    fake_refund_id,
)
from apps.payments.gateway import (
    REFUND_FAILED,
    REFUND_PROCESSED,
    REFUND_UNKNOWN,
    GatewayRefund,
    PaymentGatewayError,
    RefundRejectedError,
)
from apps.payments.razorpay_gateway import (
    IDEMPOTENCY_HEADER,
    PAYMENTS_URL,
    SIGNATURE_HEADER,
    RazorpayGateway,
)

pytestmark = pytest.mark.django_db


def _settings(settings) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"
    settings.RAZORPAY_WEBHOOK_SECRET = "webhook-secret"


def test_the_fake_refund_id_is_stable_for_one_key() -> None:
    first = fake_refund_id("refund-7")
    second = fake_refund_id("refund-7")

    assert first == second
    assert first.startswith("fake_refund_")


def test_the_fake_gateway_returns_and_stores_a_refund() -> None:
    FakeGateway.reset()
    gateway = FakeGateway()

    refund = gateway.create_refund("pay_1", Decimal("224.25"), "refund-7")
    stored = gateway.fetch_payment_refunds("pay_1")

    assert refund.kind == REFUND_PROCESSED
    assert refund.amount == Decimal("224.25")
    assert refund.gateway_refund_id == fake_refund_id("refund-7")
    assert stored == [refund]
    FakeGateway.reset()


def test_the_fake_gateway_can_refuse_a_refund() -> None:
    FakeGateway.reset()
    FakeGateway.fail_refund("refund-8")

    with pytest.raises(PaymentGatewayError):
        FakeGateway().create_refund("pay_1", Decimal("1.00"), "refund-8")

    FakeGateway.reset()


def test_the_fake_gateway_can_answer_with_a_failure() -> None:
    FakeGateway.reset()
    FakeGateway.set_refund_kind("refund-9", REFUND_FAILED)

    refund = FakeGateway().create_refund("pay_1", Decimal("1.00"), "refund-9")

    assert refund.kind == REFUND_FAILED
    FakeGateway.reset()


def test_the_fake_gateway_parses_a_refund_webhook() -> None:
    FakeGateway.reset()
    body = json.dumps(
        {
            "event": "refund.processed",
            "event_id": "evt_refund",
            "payment_id": "pay_1",
            "amount": "224.25",
        }
    ).encode("utf-8")

    event = FakeGateway().parse_webhook(body, {FAKE_SIGNATURE_HEADER: "signed"})

    assert event is not None
    assert event.kind == REFUND_PROCESSED
    assert event.event_id == "evt_refund"
    assert event.gateway_payment_id == "pay_1"
    assert event.amount == Decimal("224.25")
    FakeGateway.reset()


def test_razorpay_sends_the_refund_in_paise_with_the_idempotency_header(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(settings)
    calls: list[tuple] = []

    def fake_post(url, payload, *, extra_headers=None, reject_on_4xx=False):
        calls.append((url, payload, extra_headers, reject_on_4xx))
        return {
            "id": "rfnd_1",
            "payment_id": "pay_1",
            "status": "processed",
            "amount": 22425,
            "currency": "INR",
        }

    monkeypatch.setattr(razorpay_gateway, "_post_json", fake_post)

    refund = RazorpayGateway().create_refund("pay_1", Decimal("224.25"), "refund-7")

    url, payload, headers, reject_on_4xx = calls[0]
    assert url == f"{PAYMENTS_URL}/pay_1/refund"
    assert payload["amount"] == 22425
    assert headers == {IDEMPOTENCY_HEADER: "refund-7"}
    assert reject_on_4xx is True
    assert refund.gateway_refund_id == "rfnd_1"
    assert refund.kind == REFUND_PROCESSED
    assert refund.amount == Decimal("224.25")


def test_razorpay_refuses_a_4xx_refund(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(settings)

    def refuse(*args, **kwargs):
        raise HTTPError("https://api.razorpay.com", 400, "bad", {}, None)

    monkeypatch.setattr(razorpay_gateway, "urlopen", refuse)

    with pytest.raises(RefundRejectedError):
        RazorpayGateway().create_refund("pay_1", Decimal("224.25"), "refund-7")


def test_razorpay_lists_the_refunds_of_one_payment(settings, monkeypatch) -> None:
    _settings(settings)
    monkeypatch.setattr(
        razorpay_gateway,
        "_get_json",
        lambda url: {
            "items": [
                {
                    "id": "rfnd_1",
                    "payment_id": "pay_1",
                    "status": "failed",
                    "amount": 22425,
                    "currency": "INR",
                }
            ]
        },
    )

    refunds = RazorpayGateway().fetch_payment_refunds("pay_1")

    assert len(refunds) == 1
    assert refunds[0].kind == REFUND_FAILED
    assert refunds[0].amount == Decimal("224.25")


def test_razorpay_parses_a_processed_refund_webhook(settings) -> None:
    _settings(settings)
    body = json.dumps(
        {
            "event": "refund.processed",
            "payload": {
                "refund": {
                    "entity": {
                        "id": "rfnd_1",
                        "payment_id": "pay_1",
                        "status": "processed",
                        "amount": 22425,
                        "currency": "INR",
                    }
                }
            },
        }
    ).encode("utf-8")
    signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()

    event = RazorpayGateway().parse_webhook(body, {SIGNATURE_HEADER: signature})

    assert event is not None
    assert event.kind == REFUND_PROCESSED
    assert event.gateway_payment_id == "pay_1"
    assert event.amount == Decimal("224.25")
    assert event.safe["refund_id"] == "rfnd_1"


def test_razorpay_marks_a_refund_event_it_does_not_know_as_unknown(settings) -> None:
    _settings(settings)
    body = json.dumps(
        {
            "event": "refund.created",
            "payload": {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_1"}}},
        }
    ).encode("utf-8")
    signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()

    event = RazorpayGateway().parse_webhook(body, {SIGNATURE_HEADER: signature})

    assert event is not None
    assert event.kind == REFUND_UNKNOWN
    assert event.safe["provider_event"] == "refund.created"


def test_the_refund_value_object_defaults_to_an_empty_payload() -> None:
    refund = GatewayRefund(
        gateway_refund_id="rfnd_1",
        gateway_payment_id="pay_1",
        kind=REFUND_PROCESSED,
        amount=Decimal("1.00"),
    )

    assert refund.safe == {}
