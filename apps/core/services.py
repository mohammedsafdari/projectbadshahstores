"""Core app business logic lives here."""

from django.core.cache import cache

from apps.core.models import SHOP_SETTINGS_CACHE_KEY, ShopSettings

# How long a cached copy of the settings row stays valid (D58).
SHOP_SETTINGS_CACHE_SECONDS = 60


def get_shop_settings() -> ShopSettings:
    """Return the one ShopSettings row, through a 60-second cache (D58).

    Every save clears the cache key (see ``ShopSettings.save``), so a staff edit
    is visible on the next request. A cache miss calls ``ShopSettings.load()``,
    which also creates the row with its defaults on the first ever read.
    """
    row = cache.get(SHOP_SETTINGS_CACHE_KEY)
    if row is None:
        # A miss or an expired entry: read the row from the database and cache
        # it again for the next request.
        row = ShopSettings.load()
        cache.set(SHOP_SETTINGS_CACHE_KEY, row, SHOP_SETTINGS_CACHE_SECONDS)
    return row
