"""Fixtures shared by the report tests (D85).

An invoice refuses a blank shop name, address or GSTIN (D80), so every test that
sends an order out needs those three filled. Filling them here keeps each test
about the report it checks.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from apps.core.models import SHOP_SETTINGS_CACHE_KEY
from apps.orders.tests.invoice_helpers import fill_shop_details


@pytest.fixture(autouse=True)
def _shop_details_for_invoices(request):
    """Fill the shop details a tax invoice prints, and clear the cached copy."""
    # A pure test (a date range, a CSV writer) needs no database row.
    if request.node.get_closest_marker("django_db") is None:
        yield
        return
    fill_shop_details()
    yield
    # The cache lives per process and is not rolled back with the database.
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
