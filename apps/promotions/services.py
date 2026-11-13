"""Promotions business logic: giving a cancelled order's coupon use back (D82).

The checkout itself lives in ``apps/orders/services.py``: it calls
``evaluate_coupon`` with the coupon row locked and then writes the redemption.
This module owns the one other write, the release, and it never imports
``apps.orders.services`` (that module imports this one, so the reverse import
would be a cycle). It reads the orders models only.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction

from apps.orders.models import Invoice, Order
from apps.promotions.models import CouponRedemption


def release_redemption(order: Order, *, now: datetime) -> CouponRedemption | None:
    """Give this order's coupon use back, unless the order was sent out.

    An invoice means the goods left the shop, so a cancel after send-out keeps
    the use counted: the customer already had the discount. The caller must run
    this inside the transaction that cancels the order, so a refused cancel
    releases nothing. A second call changes nothing: a released row is skipped,
    and an order with no coupon has no row and returns ``None``.
    """
    # A ``select_for_update()`` needs an open transaction; the cancel already
    # holds one, and this keeps a direct call from failing.
    with transaction.atomic():
        redemption = (
            CouponRedemption.objects.select_for_update()
            .filter(order=order, released_at__isnull=True)
            .first()
        )
        if redemption is None:
            return None
        if Invoice.objects.filter(order=order).exists():
            return redemption
        redemption.released_at = now
        # The row carries no ``updated_at``, so only the release time moves.
        redemption.save(update_fields=["released_at"])
        return redemption
