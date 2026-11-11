"""The provider boundary for online payments (D76, D77).

Everything outside this package talks to these small types only. A provider
module is imported lazily inside ``get_gateway``, so the rest of the project
never depends on a gateway SDK or on network access.

Money here is always ``Decimal`` INR. A provider module converts to its own
smallest unit inside itself, because that is a provider detail.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from django.conf import settings

if TYPE_CHECKING:
    from apps.orders.models import Order

# The payment events this project understands (D77) and the refund events it
# adds in D78. A provider's own names never leave its module.
PAYMENT_CAPTURED = "payment.captured"
PAYMENT_FAILED = "payment.failed"
REFUND_PROCESSED = "refund.processed"
REFUND_FAILED = "refund.failed"
REFUND_PENDING = "refund.pending"
# A refund event the shop has no rule for. It is stored and explained instead
# of being silently dropped (D78).
REFUND_UNKNOWN = "refund.unknown"
REFUND_KINDS = (REFUND_PROCESSED, REFUND_FAILED)


class PaymentGatewayError(RuntimeError):
    """Base error for a gateway refusal or a broken gateway reply."""


class RefundRejectedError(PaymentGatewayError):
    """The provider refused the refund itself (a 4xx), so retrying cannot help."""


class GatewayNotConfigured(PaymentGatewayError):
    """No provider is configured, or its credentials are missing."""


class InvalidWebhookSignature(PaymentGatewayError):
    """The webhook body was not signed by the configured provider."""


@dataclass(frozen=True)
class PaymentSession:
    """What the browser needs to open the provider's payment page."""

    provider: str
    key_id: str
    gateway_order_id: str
    amount: Decimal
    currency: str
    description: str


@dataclass(frozen=True)
class GatewayEvent:
    """One verified gateway event, copied into provider-neutral values."""

    event_id: str
    kind: str
    gateway_order_id: str
    gateway_payment_id: str
    amount: Decimal
    method: str
    # The allow-listed part of the provider reply that may be stored. Personal
    # data (phone, email, UPI address, card, bank, notes) never goes in here.
    safe: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayRefund:
    """One provider refund, copied into provider-neutral values.

    ``kind`` is one of ``REFUND_PROCESSED``, ``REFUND_FAILED`` or
    ``REFUND_PENDING``. The amount is the money going back to the customer,
    which is the payment amount minus any cancellation fee (D78).
    """

    gateway_refund_id: str
    gateway_payment_id: str
    kind: str
    amount: Decimal
    safe: dict[str, object] = field(default_factory=dict)


class PaymentGateway(Protocol):
    """The small surface every provider module must offer."""

    name: str

    def public_key_id(self) -> str:
        """The key the browser may see (never the secret)."""

    def create_session(self, order: Order) -> PaymentSession:
        """Create the provider's order and return the browser values."""

    def parse_webhook(
        self,
        body: bytes,
        headers: Mapping[str, str],
    ) -> GatewayEvent | None:
        """Verify the signature and return one neutral event.

        ``None`` means the event was signed correctly but the shop does not act
        on it, which the view answers with 200 and stores nothing.
        """

    def fetch_order_payments(self, gateway_order_id: str) -> list[GatewayEvent]:
        """The captured and failed payments the provider has for one order."""

    def create_refund(
        self,
        gateway_payment_id: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> GatewayRefund:
        """Ask the provider to send ``amount`` back for one captured payment.

        ``idempotency_key`` is stable for the whole refund, so a retried call
        cannot send the money twice.
        """

    def fetch_payment_refunds(self, gateway_payment_id: str) -> list[GatewayRefund]:
        """The refunds the provider holds for one captured payment."""


def get_gateway(name: str | None = None):
    """Build the configured gateway, or return None when none is configured.

    The import is inside the function so a project with no online payments
    never imports a provider module at all.
    """
    from apps.payments.gateways import build_gateway

    return build_gateway(name)


def online_payment_available() -> bool:
    """True only when a provider is configured and ready to take a payment."""
    try:
        return get_gateway() is not None
    except GatewayNotConfigured:
        return False


def enabled_payment_methods() -> list[str]:
    """The method names the owner configured, trimmed and lower-cased.

    The list is configuration, never a rule in code: the project does not
    decide which gateway methods exist or whether one of them is free.
    """
    methods = getattr(settings, "PAYMENT_ENABLED_METHODS", []) or []
    return [str(method).strip().lower() for method in methods if str(method).strip()]
