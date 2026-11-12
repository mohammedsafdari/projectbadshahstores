"""Small factories shared by the promotions tests (D81).

A plain function is enough here: each needs only a couple of sensible defaults,
so there is no factory_boy class to keep in step with the model.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Order
from apps.orders.tests.factories import OrderFactory
from apps.promotions.models import Coupon, CouponRedemption
from tests.factories import UserFactory

ZERO = Decimal("0.00")


def make_coupon(**overrides: object) -> Coupon:
    """Create one coupon: a 10% festival code unless a field is overridden."""
    kind = overrides.pop("kind", Coupon.Kind.PERCENT)
    values: dict[str, object] = {
        "code": "DIWALI10",
        "description": "Festival offer",
        "kind": kind,
    }
    if kind == Coupon.Kind.PERCENT:
        values["percent_off"] = Decimal("10.00")
    elif kind == Coupon.Kind.FIXED:
        values["amount_off"] = Decimal("50.00")
    values.update(overrides)
    return Coupon.objects.create(**values)


def make_order(user: User, status: str) -> Order:
    """Create one order in a status, with the timestamp that status requires."""
    extra: dict[str, object] = {}
    # The database requires cancelled_at exactly for CANCELLED and
    # delivered_at exactly for DELIVERED.
    if status == Order.Status.CANCELLED:
        extra["cancelled_at"] = timezone.now()
    if status == Order.Status.DELIVERED:
        extra["delivered_at"] = timezone.now()
    return OrderFactory(user=user, status=status, **extra)


def make_redemption(
    coupon: Coupon,
    *,
    user: User | None = None,
    order: Order | None = None,
    **overrides: object,
) -> CouponRedemption:
    """Create one redemption, with a fresh customer and order unless given."""
    customer = user if user is not None else UserFactory()
    values: dict[str, object] = {
        "coupon": coupon,
        "user": customer,
        "order": order if order is not None else OrderFactory(user=customer),
        "discount_amount": Decimal("10.00"),
        "delivery_discount_amount": ZERO,
    }
    values.update(overrides)
    return CouponRedemption.objects.create(**values)
