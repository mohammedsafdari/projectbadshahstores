"""Tests for the provider boundary (D76, D77)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.payments.fake_gateway import FakeGateway
from apps.payments.gateway import (
    PAYMENT_CAPTURED,
    GatewayEvent,
    GatewayNotConfigured,
    InvalidWebhookSignature,
    PaymentGatewayError,
    PaymentSession,
    enabled_payment_methods,
    get_gateway,
    online_payment_available,
)
from apps.payments.razorpay_gateway import RazorpayGateway

pytestmark = pytest.mark.django_db


def test_an_empty_provider_means_no_gateway(settings) -> None:
    settings.PAYMENT_PROVIDER = ""

    assert get_gateway() is None
    assert online_payment_available() is False


def test_an_unknown_provider_is_a_clear_error(settings) -> None:
    settings.PAYMENT_PROVIDER = "paypal"

    with pytest.raises(GatewayNotConfigured):
        get_gateway()

    # online_payment_available turns the same refusal into "not offered".
    assert online_payment_available() is False


def test_the_fake_gateway_is_refused_unless_the_switch_is_on(settings) -> None:
    settings.PAYMENT_PROVIDER = "fake"
    settings.PAYMENT_ALLOW_FAKE_GATEWAY = False

    with pytest.raises(GatewayNotConfigured):
        get_gateway()


def test_the_allowed_fake_gateway_is_built_by_name(settings) -> None:
    settings.PAYMENT_PROVIDER = "fake"
    settings.PAYMENT_ALLOW_FAKE_GATEWAY = True

    gateway = get_gateway()

    assert isinstance(gateway, FakeGateway)
    assert gateway.name == "fake"
    assert get_gateway("fake") is not None


def test_the_razorpay_provider_is_built_without_a_network_call(settings) -> None:
    settings.PAYMENT_PROVIDER = "razorpay"

    gateway = get_gateway()

    assert isinstance(gateway, RazorpayGateway)
    assert gateway.name == "razorpay"


def test_configured_methods_are_trimmed_and_lower_cased(settings) -> None:
    settings.PAYMENT_ENABLED_METHODS = [" UPI ", "Card", "", "   "]

    assert enabled_payment_methods() == ["upi", "card"]


def test_an_empty_method_list_stays_empty(settings) -> None:
    settings.PAYMENT_ENABLED_METHODS = []

    assert enabled_payment_methods() == []


def test_a_missing_method_setting_is_treated_as_empty(settings) -> None:
    del settings.PAYMENT_ENABLED_METHODS

    assert enabled_payment_methods() == []


def test_the_session_value_object_holds_the_browser_values() -> None:
    session = PaymentSession(
        provider="fake",
        key_id="fake-key",
        gateway_order_id="fake_order_1",
        amount=Decimal("230.00"),
        currency="INR",
        description="Order BS/26-27/1",
    )

    assert session.amount == Decimal("230.00")
    assert session.provider == "fake"


def test_the_event_value_object_defaults_to_an_empty_payload() -> None:
    event = GatewayEvent(
        event_id="evt_1",
        kind=PAYMENT_CAPTURED,
        gateway_order_id="fake_order_1",
        gateway_payment_id="pay_1",
        amount=Decimal("230.00"),
        method="upi",
    )

    assert event.safe == {}


def test_a_bad_signature_is_a_gateway_error() -> None:
    assert issubclass(InvalidWebhookSignature, PaymentGatewayError)
    assert issubclass(GatewayNotConfigured, PaymentGatewayError)
