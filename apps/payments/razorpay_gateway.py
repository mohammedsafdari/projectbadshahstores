"""Everything Razorpay-specific lives in this one module (D76, D77).

No other file imports this module except the registry in ``gateways.py``. The
Razorpay REST calls use the standard library, so the project needs no extra
dependency. Amounts leave this module as ``Decimal`` rupees; the paise
conversion is a detail kept inside.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any
from urllib.request import Request, urlopen

from django.conf import settings

from apps.orders.models import Order
from apps.payments.gateway import (
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    GatewayEvent,
    GatewayNotConfigured,
    InvalidWebhookSignature,
    PaymentGatewayError,
    PaymentSession,
)

ORDERS_URL = "https://api.razorpay.com/v1/orders"
REQUEST_TIMEOUT_SECONDS = 10


def _credentials() -> tuple[str, str]:
    """The test-mode key pair, or a clear refusal when it is missing."""
    key_id = str(getattr(settings, "RAZORPAY_KEY_ID", "") or "")
    key_secret = str(getattr(settings, "RAZORPAY_KEY_SECRET", "") or "")
    if not key_id or not key_secret:
        raise GatewayNotConfigured("Razorpay keys are not configured.")
    return key_id, key_secret


def _post_json(url: str, payload: dict[str, object]) -> dict[str, Any]:
    """POST JSON with basic auth and return the decoded reply."""
    key_id, key_secret = _credentials()
    token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode("ascii")
    # The URL is the fixed Razorpay endpoint, never user input.
    request = Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        # The URL is the fixed Razorpay endpoint above, never user input.
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise PaymentGatewayError("Razorpay did not accept the request.") from error
    if not isinstance(data, dict):
        raise PaymentGatewayError("Razorpay returned an unexpected reply.")
    return data


def _rupees(paise: object) -> Decimal:
    """Turn Razorpay's integer paise into rupees with two places."""
    return (Decimal(str(paise)) / Decimal("100")).quantize(Decimal("0.01"))


class RazorpayGateway:
    """The Razorpay test-mode provider."""

    name = "razorpay"

    def public_key_id(self) -> str:
        key_id, _key_secret = _credentials()
        return key_id

    def create_session(self, order: Order) -> PaymentSession:
        key_id, _key_secret = _credentials()
        payload: dict[str, object] = {
            # Razorpay takes the smallest unit, and only whole paise.
            "amount": int(order.online_amount * 100),
            "currency": "INR",
            "receipt": order.order_number[:40],
            "notes": {"order_number": order.order_number},
        }
        data = _post_json(ORDERS_URL, payload)
        gateway_order_id = str(data.get("id", ""))
        if not gateway_order_id:
            raise PaymentGatewayError("Razorpay returned no order id.")
        return PaymentSession(
            provider=self.name,
            key_id=key_id,
            gateway_order_id=gateway_order_id,
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
        secret = str(getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "") or "")
        if not secret:
            raise GatewayNotConfigured("The Razorpay webhook secret is not configured.")
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        # compare_digest avoids leaking how much of the signature matched.
        if not signature or not hmac.compare_digest(expected, signature):
            raise InvalidWebhookSignature("The Razorpay signature is not valid.")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PaymentGatewayError("The Razorpay webhook body is not JSON.") from error
        if not isinstance(data, dict):
            raise PaymentGatewayError("The Razorpay webhook body is not an object.")

        kind = str(data.get("event", ""))
        if kind not in (PAYMENT_CAPTURED, PAYMENT_FAILED):
            raise PaymentGatewayError(f"Razorpay event {kind!r} is not handled.")
        payload = data.get("payload")
        payment = payload.get("payment") if isinstance(payload, dict) else None
        entity = payment.get("entity") if isinstance(payment, dict) else None
        if not isinstance(entity, dict):
            raise PaymentGatewayError("The Razorpay webhook has no payment entity.")

        gateway_payment_id = str(entity.get("id", ""))
        return GatewayEvent(
            # Razorpay sends the event id in a header; the payment id is a safe
            # fallback because one payment produces at most one captured event.
            event_id=event_id or f"{kind}:{gateway_payment_id}",
            kind=kind,
            gateway_order_id=str(entity.get("order_id", "")),
            gateway_payment_id=gateway_payment_id,
            amount=_rupees(entity.get("amount", 0)),
            method=str(entity.get("method", "")),
            raw=data,
        )
