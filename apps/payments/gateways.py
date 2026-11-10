"""The provider registry: one name in settings, one module here (D77).

Adding a provider means one entry in this function and one module that offers
``create_session``, ``parse_webhook`` and ``fetch_order_payments``. Nothing else
in the project changes.
"""

from __future__ import annotations

from django.conf import settings

from apps.payments.gateway import GatewayNotConfigured, PaymentGateway


def build_gateway(name: str | None = None) -> PaymentGateway | None:
    """Return the configured provider, or None when no provider is set."""
    provider = (name if name is not None else settings.PAYMENT_PROVIDER).strip().lower()
    if not provider:
        return None
    if provider == "razorpay":
        # Imported here so the provider module is only loaded when it is used.
        from apps.payments.razorpay_gateway import RazorpayGateway

        return RazorpayGateway()
    if provider == "fake":
        # The fake gateway is a development and test tool only. A production
        # deployment cannot reach it unless the owner turns the switch on.
        if not settings.PAYMENT_ALLOW_FAKE_GATEWAY:
            raise GatewayNotConfigured("The fake payment gateway is not allowed.")
        from apps.payments.fake_gateway import FakeGateway

        return FakeGateway()
    raise GatewayNotConfigured(f"Unknown payment provider {provider!r}.")
