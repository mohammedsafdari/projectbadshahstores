"""Tests for the deterministic fake gateway (D77)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from apps.payments.fake_gateway import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    FakeGateway,
    fake_gateway_order_id,
)
from apps.payments.gateway import PAYMENT_CAPTURED, PAYMENT_FAILED, InvalidWebhookSignature
from apps.payments.tests.helpers import factory_online_order

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_seed():
    """No seeded payment survives from one test to the next."""
    FakeGateway.reset()
    yield
    FakeGateway.reset()


def _body(**values: object) -> bytes:
    return json.dumps(values).encode("utf-8")


def _headers(*, event_id: str = "") -> dict[str, str]:
    return {SIGNATURE_HEADER: "signature", EVENT_ID_HEADER: event_id}


def test_the_fake_order_id_is_the_same_for_the_same_order_number() -> None:
    first = fake_gateway_order_id("BS/26-27/000001")
    second = fake_gateway_order_id("BS/26-27/000001")

    assert first == second
    assert first.startswith("fake_order_")
    assert len(first) == len("fake_order_") + 24


def test_two_order_numbers_get_different_fake_ids() -> None:
    assert fake_gateway_order_id("BS/26-27/1") != fake_gateway_order_id("BS/26-27/2")


def test_create_session_copies_the_online_part() -> None:
    order = factory_online_order()
    gateway = FakeGateway()

    session = gateway.create_session(order)

    assert session.provider == "fake"
    assert session.key_id == "fake-key"
    assert session.gateway_order_id == fake_gateway_order_id(order.order_number)
    assert session.amount == Decimal("230.00")
    assert session.currency == "INR"
    assert order.order_number in session.description


def test_parse_webhook_reads_a_captured_event() -> None:
    event = FakeGateway().parse_webhook(
        _body(
            event=PAYMENT_CAPTURED,
            event_id="evt_1",
            order_id="fake_order_1",
            payment_id="pay_1",
            amount="230.00",
            method="upi",
        ),
        _headers(),
    )

    assert event is not None
    assert event.event_id == "evt_1"
    assert event.kind == PAYMENT_CAPTURED
    assert event.gateway_order_id == "fake_order_1"
    assert event.gateway_payment_id == "pay_1"
    assert event.amount == Decimal("230.00")
    assert event.method == "upi"


def test_parse_webhook_accepts_a_failed_event() -> None:
    event = FakeGateway().parse_webhook(
        _body(event=PAYMENT_FAILED, order_id="fake_order_1", amount="230.00"),
        _headers(),
    )

    assert event is not None
    assert event.kind == PAYMENT_FAILED


def test_a_missing_body_event_id_falls_back_to_the_header() -> None:
    event = FakeGateway().parse_webhook(
        _body(event=PAYMENT_CAPTURED, order_id="fake_order_1"),
        _headers(event_id="evt_header"),
    )

    assert event is not None
    assert event.event_id == "evt_header"


def test_the_fake_gateway_reads_its_own_signature_header() -> None:
    with pytest.raises(InvalidWebhookSignature):
        FakeGateway().parse_webhook(
            _body(event=PAYMENT_CAPTURED),
            {"X-Other-Signature": "signature"},
        )


def test_a_missing_signature_is_refused() -> None:
    with pytest.raises(InvalidWebhookSignature):
        FakeGateway().parse_webhook(_body(event=PAYMENT_CAPTURED), {})


def test_a_body_that_is_not_json_is_refused() -> None:
    with pytest.raises(InvalidWebhookSignature):
        FakeGateway().parse_webhook(b"not-json", _headers())


def test_an_unknown_event_is_ignored() -> None:
    assert FakeGateway().parse_webhook(_body(event="payment.refunded"), _headers()) is None


def test_the_safe_payload_is_kept_for_support() -> None:
    body = _body(
        event=PAYMENT_FAILED,
        order_id="fake_order_1",
        payment_id="pay_1",
        amount="1.00",
        error="card declined",
    )

    event = FakeGateway().parse_webhook(body, _headers())

    assert event is not None
    assert event.safe["order_id"] == "fake_order_1"
    assert event.safe["error_description"] == "card declined"


def test_fetch_returns_the_seeded_events_for_one_order() -> None:
    event = FakeGateway().parse_webhook(
        _body(event=PAYMENT_CAPTURED, order_id="fake_order_1", amount="1.00"),
        _headers(),
    )
    assert event is not None
    FakeGateway.seed_payments("fake_order_1", [event])

    assert FakeGateway().fetch_order_payments("fake_order_1") == [event]
    assert FakeGateway().fetch_order_payments("fake_order_2") == []


def test_reset_forgets_every_seeded_payment() -> None:
    event = FakeGateway().parse_webhook(
        _body(event=PAYMENT_CAPTURED, order_id="fake_order_1", amount="1.00"),
        _headers(),
    )
    assert event is not None
    FakeGateway.seed_payments("fake_order_1", [event])

    FakeGateway.reset()

    assert FakeGateway().fetch_order_payments("fake_order_1") == []
