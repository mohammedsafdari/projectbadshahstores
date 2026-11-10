"""Tests for the deterministic fake gateway (D77)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from apps.payments.fake_gateway import FakeGateway, fake_gateway_order_id
from apps.payments.gateway import PAYMENT_CAPTURED, PAYMENT_FAILED, InvalidWebhookSignature
from apps.payments.tests.helpers import factory_online_order

pytestmark = pytest.mark.django_db


def _body(**values: object) -> bytes:
    return json.dumps(values).encode("utf-8")


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
        "signature",
    )

    assert event.event_id == "evt_1"
    assert event.kind == PAYMENT_CAPTURED
    assert event.gateway_order_id == "fake_order_1"
    assert event.gateway_payment_id == "pay_1"
    assert event.amount == Decimal("230.00")
    assert event.method == "upi"


def test_parse_webhook_accepts_a_failed_event() -> None:
    event = FakeGateway().parse_webhook(
        _body(event=PAYMENT_FAILED, order_id="fake_order_1", amount="230.00"),
        "signature",
    )

    assert event.kind == PAYMENT_FAILED


def test_a_missing_body_event_id_falls_back_to_the_header() -> None:
    event = FakeGateway().parse_webhook(
        _body(event=PAYMENT_CAPTURED, order_id="fake_order_1"),
        "signature",
        event_id="evt_header",
    )

    assert event.event_id == "evt_header"


def test_a_missing_signature_is_refused() -> None:
    with pytest.raises(InvalidWebhookSignature):
        FakeGateway().parse_webhook(_body(event=PAYMENT_CAPTURED), "")


def test_a_body_that_is_not_json_is_refused() -> None:
    with pytest.raises(InvalidWebhookSignature):
        FakeGateway().parse_webhook(b"not-json", "signature")


def test_an_unknown_event_is_refused() -> None:
    with pytest.raises(InvalidWebhookSignature):
        FakeGateway().parse_webhook(_body(event="payment.refunded"), "signature")


def test_the_raw_payload_is_kept_for_support() -> None:
    body = _body(event=PAYMENT_CAPTURED, order_id="fake_order_1", amount="1.00")

    event = FakeGateway().parse_webhook(body, "signature")

    assert event.raw["order_id"] == "fake_order_1"
