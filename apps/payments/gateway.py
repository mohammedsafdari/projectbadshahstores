"""The provider boundary for online payments (D76, D77).

Everything outside this package talks to these small types only. A provider
module (``razorpay_gateway``) is imported lazily inside ``get_gateway``, so the
rest of the project never depends on a gateway SDK or on network access.

Money here is always ``Decimal`` INR. The Razorpay module converts to paise
inside itself, because that is a Razorpay detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from django.conf import settings

if TYPE_CHECKING:
    from apps.orders.models import Order

# The two kinds of event this slice understands. Refunds arrive in Slice 41.
PAYMENT_CAPTURED = "payment.captured"
PAYMENT_FAILED = "payment.failed"


class PaymentGatewayError(RuntimeError):
    """Base error for a gateway refusal or a broken gateway reply."""


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
    """One verified webhook event, copied into provider-neutral values."""

    event_id: str
    kind: str
    gateway_order_id: str
    gateway_payment_id: str
    amount: Decimal
    method: str
    raw: dict[str, object] = field(default_factory=dict)


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
        signature: str,
        *,
        event_id: str = "",
    ) -> GatewayEvent:
        """Verify the signature and return one neutral event."""


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
