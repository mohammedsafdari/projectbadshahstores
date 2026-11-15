"""Scheduled cart jobs (D94).

One job so far: a saved cart nobody has touched is deleted after its retention
window, so the two cart tables cannot grow for ever.
"""

from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.cart.models import Cart

# How many days a saved cart is kept when nobody touches it.
SAVED_CART_RETENTION_DAYS = 30


@shared_task
def delete_stale_saved_carts() -> int:
    """Delete the saved carts nobody has touched inside the window (D94).

    Returns how many carts went. A cart exactly ``SAVED_CART_RETENTION_DAYS``
    old is kept, because the filter is strictly older than the cutoff (the
    same rule as the search words, D89).

    A cart's own row is not always touched when a line changes, so a cart whose
    row is old but which still holds a line newer than the cutoff is kept: the
    ``exclude`` drops every cart that has at least one such line.

    The lines go with their cart (CASCADE). The customer and the variants stay.
    """
    cutoff = timezone.now() - timedelta(days=SAVED_CART_RETENTION_DAYS)
    stale_pks = list(
        Cart.objects.filter(updated_at__lt=cutoff)
        .exclude(items__updated_at__gte=cutoff)
        .values_list("pk", flat=True)
    )
    # delete() returns a pair: the total number of rows deleted (the lines
    # counted too) and a dict of counts per model. The cart count is the entry
    # for this model, which is why the answer is not the first number.
    _deleted, details = Cart.objects.filter(pk__in=stale_pks).delete()
    return details.get(Cart._meta.label, 0)
