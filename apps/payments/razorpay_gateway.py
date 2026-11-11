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
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings

from apps.orders.models import Order
from apps.payments.gateway import (
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    REFUND_FAILED,
    REFUND_PENDING,
    REFUND_PROCESSED,
    REFUND_UNKNOWN,
    GatewayEvent,
    GatewayNotConfigured,
    GatewayRefund,
    InvalidWebhookSignature,
    PaymentGatewayError,
    PaymentSession,
    RefundRejectedError,
)

ORDERS_URL = "https://api.razorpay.com/v1/orders"
PAYMENTS_URL = "https://api.razorpay.com/v1/payments"
REQUEST_TIMEOUT_SECONDS = 10
# The header names Razorpay uses are a provider detail and stay here.
SIGNATURE_HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "X-Razorpay-Event-Id"
IDEMPOTENCY_HEADER = "X-Razorpay-Idempotency-Key"
ORDER_PAID_EVENT = "order.paid"


def _credentials() -> tuple[str, str]:
    """The test-mode key pair, or a clear refusal when it is missing."""
    key_id = str(getattr(settings, "RAZORPAY_KEY_ID", "") or "")
    key_secret = str(getattr(settings, "RAZORPAY_KEY_SECRET", "") or "")
    if not key_id or not key_secret:
        raise GatewayNotConfigured("Razorpay keys are not configured.")
    return key_id, key_secret


def _auth_header() -> str:
    """The basic-auth value built from the key pair, never logged."""
    key_id, key_secret = _credentials()
    token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode("ascii")
    return f"Basic {token}"


def _post_json(
    url: str,
    payload: dict[str, object],
    *,
    extra_headers: Mapping[str, str] | None = None,
    reject_on_4xx: bool = False,
) -> dict[str, Any]:
    """POST JSON with basic auth and return the decoded reply."""
    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    # The URL is the fixed Razorpay endpoint, never user input.
    request = Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if reject_on_4xx and 400 <= error.code < 500:
            # A refused refund (a bad payment id or an amount the provider will
            # not refund) cannot succeed on a retry.
            raise RefundRejectedError("Razorpay refused the refund.") from error
        raise PaymentGatewayError("Razorpay did not accept the request.") from error
    except Exception as error:
        raise PaymentGatewayError("Razorpay did not accept the request.") from error
    if not isinstance(data, dict):
        raise PaymentGatewayError("Razorpay returned an unexpected reply.")
    return data


def _get_json(
    url: str,
    *,
    reject_on_4xx: bool = False,
) -> dict[str, Any]:
    """GET JSON with basic auth and return the decoded reply."""
    # The URL is built from the fixed Razorpay endpoint and a stored order id,
    # never from anything the browser sent.
    request = Request(  # noqa: S310
        url,
        headers={"Authorization": _auth_header()},
        method="GET",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if reject_on_4xx and 400 <= error.code < 500:
            raise RefundRejectedError("Razorpay refused the request.") from error
        raise PaymentGatewayError("Razorpay did not answer the request.") from error
    except Exception as error:
        raise PaymentGatewayError("Razorpay did not answer the request.") from error
    if not isinstance(data, dict):
        raise PaymentGatewayError("Razorpay returned an unexpected reply.")
    return data


def _rupees(paise: object) -> Decimal:
    """Turn Razorpay's integer paise into rupees with two places."""
    return (Decimal(str(paise)) / Decimal("100")).quantize(Decimal("0.01"))


def _safe_payload(entity: dict[str, Any], kind: str) -> dict[str, object]:
    """The only fields of a payment entity that may be stored (D77).

    Phone, email, UPI address, card, bank, wallet and notes are deliberately
    absent: nothing personal is kept after the event is handled.
    """
    error = entity.get("error")
    error = error if isinstance(error, dict) else {}
    return {
        "event": kind,
        "payment_id": str(entity.get("id", "")),
        "order_id": str(entity.get("order_id", "")),
        "status": str(entity.get("status", "")),
        "method": str(entity.get("method", "")),
        "amount": str(_rupees(entity.get("amount", 0))),
        "currency": str(entity.get("currency", "INR")),
        "error_code": str(error.get("code", "")),
        "error_description": str(error.get("description", "")),
        "error_reason": str(error.get("reason", "")),
    }


def _refund_kind(status: str) -> str:
    """Map the provider's refund status to one neutral kind (D78)."""
    if status == "processed":
        return REFUND_PROCESSED
    if status == "failed":
        return REFUND_FAILED
    # pending, created and anything else is still on its way.
    return REFUND_PENDING


def _safe_refund_payload(entity: dict[str, Any], kind: str) -> dict[str, object]:
    """The only fields of a refund entity that may be stored (D78)."""
    return {
        "event": kind,
        "refund_id": str(entity.get("id", "")),
        "payment_id": str(entity.get("payment_id", "")),
        "status": str(entity.get("status", "")),
        "amount": str(_rupees(entity.get("amount", 0))),
        "currency": str(entity.get("currency", "INR")),
    }


def _refund_from_entity(entity: dict[str, Any]) -> GatewayRefund:
    """Turn one Razorpay refund entity into the neutral type."""
    kind = _refund_kind(str(entity.get("status", "")))
    return GatewayRefund(
        gateway_refund_id=str(entity.get("id", "")),
        gateway_payment_id=str(entity.get("payment_id", "")),
        kind=kind,
        amount=_rupees(entity.get("amount", 0)),
        safe=_safe_refund_payload(entity, kind),
    )


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
        headers: Mapping[str, str],
    ) -> GatewayEvent | None:
        secret = str(getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "") or "")
        if not secret:
            raise GatewayNotConfigured("The Razorpay webhook secret is not configured.")
        signature = headers.get(SIGNATURE_HEADER, "")
        event_id = headers.get(EVENT_ID_HEADER, "")
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

        raw_kind = str(data.get("event", ""))
        payload = data.get("payload")
        if raw_kind.startswith("refund."):
            # A refund event names a refund entity, not a payment entity.
            return self._parse_refund_event(raw_kind, payload, event_id)
        if raw_kind == ORDER_PAID_EVENT:
            # Razorpay sends both events for one payment; order.paid carries
            # the same payment entity and means the money is captured.
            kind = PAYMENT_CAPTURED
        elif raw_kind in (PAYMENT_CAPTURED, PAYMENT_FAILED):
            kind = raw_kind
        else:
            # A valid signature on an event the shop ignores.
            return None

        payment = payload.get("payment") if isinstance(payload, dict) else None
        entity = payment.get("entity") if isinstance(payment, dict) else None
        if not isinstance(entity, dict):
            raise PaymentGatewayError("The Razorpay webhook has no payment entity.")

        gateway_payment_id = str(entity.get("id", ""))
        return GatewayEvent(
            # Razorpay sends the event id in a header. The kind is part of the
            # fallback so order.paid and payment.captured stay two events.
            event_id=event_id or f"{raw_kind}:{gateway_payment_id}",
            kind=kind,
            gateway_order_id=str(entity.get("order_id", "")),
            gateway_payment_id=gateway_payment_id,
            amount=_rupees(entity.get("amount", 0)),
            method=str(entity.get("method", "")),
            safe=_safe_payload(entity, kind),
        )

    def _parse_refund_event(
        self,
        raw_kind: str,
        payload: object,
        event_id: str,
    ) -> GatewayEvent:
        """Turn a refund webhook into a neutral event, known or not (D78)."""
        refund = payload.get("refund") if isinstance(payload, dict) else None
        entity = refund.get("entity") if isinstance(refund, dict) else None
        if not isinstance(entity, dict):
            # A signed refund event with no entity is stored as unknown.
            return GatewayEvent(
                event_id=event_id or f"{raw_kind}:missing",
                kind=REFUND_UNKNOWN,
                gateway_order_id="",
                gateway_payment_id="",
                amount=Decimal("0.00"),
                method="",
                safe={"event": REFUND_UNKNOWN, "provider_event": raw_kind},
            )
        if raw_kind == REFUND_PROCESSED:
            kind = REFUND_PROCESSED
        elif raw_kind == REFUND_FAILED:
            kind = REFUND_FAILED
        else:
            # A refund event this project does not act on. It stays an event so
            # the service can store it and say why it was ignored.
            kind = REFUND_UNKNOWN
        refund_id = str(entity.get("id", ""))
        safe = _safe_refund_payload(entity, kind)
        if kind == REFUND_UNKNOWN:
            # The provider's own event name is kept only to explain why this
            # event was ignored (D78).
            safe["provider_event"] = raw_kind
        return GatewayEvent(
            event_id=event_id or f"{raw_kind}:{refund_id}",
            kind=kind,
            gateway_order_id="",
            gateway_payment_id=str(entity.get("payment_id", "")),
            amount=_rupees(entity.get("amount", 0)),
            method="",
            safe=safe,
        )

    def create_refund(
        self,
        gateway_payment_id: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> GatewayRefund:
        """Ask Razorpay to refund one captured payment (D78)."""
        # quote keeps a stored id from changing the path of the fixed endpoint.
        url = f"{PAYMENTS_URL}/{quote(gateway_payment_id, safe='')}/refund"
        payload: dict[str, object] = {
            # Razorpay takes the smallest unit, and only whole paise.
            "amount": int(amount * 100),
            "notes": {"idempotency_key": idempotency_key},
        }
        data = _post_json(
            url,
            payload,
            extra_headers={IDEMPOTENCY_HEADER: idempotency_key},
            reject_on_4xx=True,
        )
        return _refund_from_entity(data)

    def fetch_payment_refunds(self, gateway_payment_id: str) -> list[GatewayRefund]:
        """The refunds Razorpay holds for one captured payment (D78)."""
        url = f"{PAYMENTS_URL}/{quote(gateway_payment_id, safe='')}/refunds"
        data = _get_json(url)
        items = data.get("items")
        if not isinstance(items, list):
            raise PaymentGatewayError("Razorpay returned an unexpected refunds list.")
        refunds: list[GatewayRefund] = []
        for item in items:
            if isinstance(item, dict):
                refunds.append(_refund_from_entity(item))
        return refunds

    def fetch_order_payments(self, gateway_order_id: str) -> list[GatewayEvent]:
        # quote keeps a stored id from changing the path of the fixed endpoint.
        url = f"{ORDERS_URL}/{quote(gateway_order_id, safe='')}/payments"
        data = _get_json(url)
        items = data.get("items")
        if not isinstance(items, list):
            raise PaymentGatewayError("Razorpay returned an unexpected payments list.")
        events: list[GatewayEvent] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", ""))
            if status == "captured":
                kind = PAYMENT_CAPTURED
            elif status == "failed":
                kind = PAYMENT_FAILED
            else:
                # authorized, created and anything else is not money yet.
                continue
            gateway_payment_id = str(item.get("id", ""))
            events.append(
                GatewayEvent(
                    event_id=f"{kind}:{gateway_payment_id}",
                    kind=kind,
                    gateway_order_id=str(item.get("order_id", "")),
                    gateway_payment_id=gateway_payment_id,
                    amount=_rupees(item.get("amount", 0)),
                    method=str(item.get("method", "")),
                    safe=_safe_payload(item, kind),
                )
            )
        return events
