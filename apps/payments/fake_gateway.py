"""A deterministic gateway for development and tests (D77).

It never opens a connection. Two calls with the same order number produce the
same provider order id, so a test can post a webhook without reading a random
value out of the database first.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from apps.orders.models import Order
from apps.payments.gateway import (
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    GatewayEvent,
    InvalidWebhookSignature,
    PaymentSession,
)


def fake_gateway_order_id(order_number: str) -> str:
    """A stable id built from the order number, never from random data."""
    digest = hashlib.sha256(order_number.encode("utf-8")).hexdigest()
    return f"fake_order_{digest[:24]}"


class FakeGateway:
    """The development and test provider."""

    name = "fake"

    def public_key_id(self) -> str:
        return "fake-key"

    def create_session(self, order: Order) -> PaymentSession:
        return PaymentSession(
            provider=self.name,
            key_id="fake-key",
            gateway_order_id=fake_gateway_order_id(order.order_number),
            amount=order.online_amount,
            currency="INR",
            description=f"Order {order.order_number}",
        )

    def parse_webhook(
        self,
        body: bytes,
        signature: str,
        *,
        event_id: str = "",
    ) -> GatewayEvent:
        # The fake gateway has no secret to verify, but it still refuses a
        # missing signature so the view's error path can be tested.
        if not signature:
            raise InvalidWebhookSignature("The fake gateway needs a signature.")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidWebhookSignature("The webhook body is not JSON.") from error
        kind = str(data.get("event", ""))
        if kind not in (PAYMENT_CAPTURED, PAYMENT_FAILED):
            raise InvalidWebhookSignature("The fake webhook event is not known.")
        return GatewayEvent(
            event_id=str(data.get("event_id", "")) or event_id,
            kind=kind,
            gateway_order_id=str(data.get("order_id", "")),
            gateway_payment_id=str(data.get("payment_id", "")),
            amount=Decimal(str(data.get("amount", "0.00"))),
            method=str(data.get("method", "")),
            raw=data,
        )
