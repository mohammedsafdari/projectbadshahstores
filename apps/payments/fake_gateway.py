"""A deterministic gateway for development and tests (D77).

It never opens a connection. Two calls with the same order number produce the
same provider order id, so a test can post a webhook without reading a random
value out of the database first.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import ClassVar

from apps.orders.models import Order
from apps.payments.gateway import (
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    REFUND_FAILED,
    REFUND_PROCESSED,
    REFUND_UNKNOWN,
    GatewayEvent,
    GatewayRefund,
    InvalidWebhookSignature,
    PaymentGatewayError,
    PaymentSession,
)

# The fake gateway's own header names, kept out of the view like a real one.
SIGNATURE_HEADER = "X-Fake-Signature"
EVENT_ID_HEADER = "X-Fake-Event-Id"


def fake_gateway_order_id(order_number: str) -> str:
    """A stable id built from the order number, never from random data."""
    digest = hashlib.sha256(order_number.encode("utf-8")).hexdigest()
    return f"fake_order_{digest[:24]}"


def fake_refund_id(idempotency_key: str) -> str:
    """A stable refund id built from the idempotency key (D78)."""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"fake_refund_{digest[:24]}"


def _safe_payload(data: dict[str, object], kind: str) -> dict[str, object]:
    """The development stand-in for a provider's allow-listed subset."""
    return {
        "event": kind,
        "payment_id": str(data.get("payment_id", "")),
        "order_id": str(data.get("order_id", "")),
        "status": str(data.get("status", kind)),
        "method": str(data.get("method", "")),
        "amount": str(Decimal(str(data.get("amount", "0.00")))),
        "currency": str(data.get("currency", "INR")),
        "error_code": str(data.get("error_code", "")),
        "error_description": str(data.get("error", "")),
        "error_reason": str(data.get("error_reason", "")),
    }


class FakeGateway:
    """The development and test provider."""

    name = "fake"

    # Payments a test has seeded for one provider order. The class-level store
    # lets a test set facts before the service asks for them.
    _payments: ClassVar[dict[str, list[GatewayEvent]]] = {}
    # Refunds a test has seeded for one provider payment (D78).
    _refunds: ClassVar[dict[str, list[GatewayRefund]]] = {}
    # The kind the next create_refund call should return, per idempotency key.
    _refund_kinds: ClassVar[dict[str, str]] = {}
    # Keys whose create_refund call should fail as a temporary provider error.
    _refund_failures: ClassVar[set[str]] = set()

    @classmethod
    def seed_payments(cls, gateway_order_id: str, events: list[GatewayEvent]) -> None:
        """Make ``fetch_order_payments`` return these events for this order."""
        cls._payments[gateway_order_id] = list(events)

    @classmethod
    def seed_refunds(cls, gateway_payment_id: str, refunds: list[GatewayRefund]) -> None:
        """Make ``fetch_payment_refunds`` return these refunds."""
        cls._refunds[gateway_payment_id] = list(refunds)

    @classmethod
    def set_refund_kind(cls, idempotency_key: str, kind: str) -> None:
        """Make the next create_refund for this key return this kind."""
        cls._refund_kinds[idempotency_key] = kind

    @classmethod
    def fail_refund(cls, idempotency_key: str) -> None:
        """Make the next create_refund for this key raise a provider error."""
        cls._refund_failures.add(idempotency_key)

    @classmethod
    def reset(cls) -> None:
        """Forget every seeded payment and refund, so tests cannot leak."""
        cls._payments.clear()
        cls._refunds.clear()
        cls._refund_kinds.clear()
        cls._refund_failures.clear()

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
        headers: Mapping[str, str],
    ) -> GatewayEvent | None:
        signature = headers.get(SIGNATURE_HEADER, "")
        # The fake gateway has no secret to verify, but it still refuses a
        # missing signature so the view's error path can be tested.
        if not signature:
            raise InvalidWebhookSignature("The fake gateway needs a signature.")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidWebhookSignature("The webhook body is not JSON.") from error
        kind = str(data.get("event", ""))
        if kind.startswith("refund."):
            if kind not in (REFUND_PROCESSED, REFUND_FAILED):
                kind = REFUND_UNKNOWN
            return GatewayEvent(
                event_id=str(data.get("event_id", "")) or headers.get(EVENT_ID_HEADER, ""),
                kind=kind,
                gateway_order_id="",
                gateway_payment_id=str(data.get("payment_id", "")),
                amount=Decimal(str(data.get("amount", "0.00"))),
                method="",
                safe=_safe_payload(data, kind),
            )
        if kind not in (PAYMENT_CAPTURED, PAYMENT_FAILED):
            # A validly signed event the shop does not act on.
            return None
        return GatewayEvent(
            event_id=str(data.get("event_id", "")) or headers.get(EVENT_ID_HEADER, ""),
            kind=kind,
            gateway_order_id=str(data.get("order_id", "")),
            gateway_payment_id=str(data.get("payment_id", "")),
            amount=Decimal(str(data.get("amount", "0.00"))),
            method=str(data.get("method", "")),
            safe=_safe_payload(data, kind),
        )

    def fetch_order_payments(self, gateway_order_id: str) -> list[GatewayEvent]:
        return list(self._payments.get(gateway_order_id, []))

    def create_refund(
        self,
        gateway_payment_id: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> GatewayRefund:
        """Return a deterministic refund, or fail on request (D78)."""
        if idempotency_key in self._refund_failures:
            raise PaymentGatewayError("The fake gateway refused the refund.")
        kind = self._refund_kinds.get(idempotency_key, REFUND_PROCESSED)
        refund = GatewayRefund(
            gateway_refund_id=fake_refund_id(idempotency_key),
            gateway_payment_id=gateway_payment_id,
            kind=kind,
            amount=amount,
            safe={
                "event": kind,
                "refund_id": fake_refund_id(idempotency_key),
                "payment_id": gateway_payment_id,
                "status": kind,
                "amount": str(amount),
                "currency": "INR",
            },
        )
        self._refunds.setdefault(gateway_payment_id, []).append(refund)
        return refund

    def fetch_payment_refunds(self, gateway_payment_id: str) -> list[GatewayRefund]:
        return list(self._refunds.get(gateway_payment_id, []))
