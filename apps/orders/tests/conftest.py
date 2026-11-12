"""Fixtures shared by the order tests (D80).

Sending an order out now issues its GST tax invoice, and an invoice refuses a
blank shop name, address or GSTIN. Filling the one settings row here keeps
every existing send-out test working without touching the test itself; a test
that wants a blank field sets it itself.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from apps.core.models import SHOP_SETTINGS_CACHE_KEY
from apps.orders.tests.invoice_helpers import fill_shop_details


@pytest.fixture(autouse=True)
def _shop_details_for_invoices(request):
    """Fill the shop details a tax invoice prints (D80).

    Only a test allowed to touch the database gets the settings row: a pure
    test (the state table, the wording helpers) would fail on the first query.
    """
    # ``get_closest_marker`` finds the ``django_db`` mark on the test or on its
    # module's ``pytestmark``, so this needs no list of test files.
    if request.node.get_closest_marker("django_db") is None:
        yield
        return
    fill_shop_details()
    yield
    # The cache lives per process and is not rolled back with the database.
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
