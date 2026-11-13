"""What the home page shows: banners and featured products (D84).

This module must not import ``apps.storefront``: the storefront imports it, so
a reverse import would be a cycle. It only reads the two promotions tables; the
storefront still decides which products may be shown.
"""

from __future__ import annotations

from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from apps.promotions.models import Banner, FeaturedProduct

# The home page shows at most this many banners, whatever staff create.
HOME_BANNER_LIMIT = 5


def visible_banners(now: datetime | None = None) -> list[Banner]:
    """The banners whose window holds ``now``, in staff order (D84).

    A banner is visible while ``starts_at <= now < ends_at``, with an empty time
    meaning \"no bound\": the same boundaries as a coupon. The order is the staff
    order, and only the first few are returned.
    """
    moment = now if now is not None else timezone.now()
    # AND of two ORs: started (or no start) AND not ended (or no end).
    window = (Q(starts_at__isnull=True) | Q(starts_at__lte=moment)) & (
        Q(ends_at__isnull=True) | Q(ends_at__gt=moment)
    )
    return list(
        Banner.objects.filter(window, is_active=True).order_by("sort_order", "id")[
            :HOME_BANNER_LIMIT
        ]
    )


def featured_product_ids() -> list[int]:
    """The featured products' ids in staff order, unfiltered (D84).

    The storefront decides what may be shown; this only says which products the
    shop asked for and in what order. ``flat=True`` gives one id per row rather
    than a tuple.
    """
    return list(
        FeaturedProduct.objects.order_by("sort_order", "id").values_list("product_id", flat=True)
    )
