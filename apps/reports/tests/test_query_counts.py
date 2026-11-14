"""Every report reads the database a constant number of times (D85)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.orders.tests.staff_helpers import staff
from apps.reports.queries import (
    SALES_BY_BRAND,
    SALES_BY_CATEGORY,
    SALES_BY_DAY,
    SALES_BY_PRODUCT,
    DateRange,
    gst_report,
    sales_report,
    stock_valuation_report,
)
from apps.reports.tests.helpers import FIRST_DAY, LAST_DAY, make_variant, sold

pytestmark = pytest.mark.django_db

NOVEMBER = DateRange(FIRST_DAY, LAST_DAY)


def _count(call: Callable[[], object]) -> int:
    with CaptureQueriesContext(connection) as queries:
        call()
    return len(queries)


def _more_sales(variant: object, times: int) -> None:
    for _index in range(times):
        sold(lines=[(variant, "1")])


@pytest.mark.parametrize("key", [SALES_BY_DAY, SALES_BY_PRODUCT])
def test_a_sales_section_does_not_get_slower_per_row(key: str) -> None:
    variant = make_variant(name="Atta")
    sold(lines=[(variant, "1")])
    actor = staff()

    small = _count(lambda: sales_report(actor, NOVEMBER, key))
    _more_sales(variant, 3)
    large = _count(lambda: sales_report(actor, NOVEMBER, key))

    assert small == large


@pytest.mark.parametrize("key", [SALES_BY_BRAND, SALES_BY_CATEGORY])
def test_the_owner_sections_do_not_get_slower_per_row(key: str) -> None:
    variant = make_variant(name="Atta")
    sold(lines=[(variant, "1")])
    actor = staff()

    small = _count(lambda: sales_report(actor, NOVEMBER, key))
    _more_sales(variant, 3)
    large = _count(lambda: sales_report(actor, NOVEMBER, key))

    assert small == large


def test_the_gst_summary_does_not_get_slower_per_row() -> None:
    variant = make_variant(name="Atta", gst="5.00")
    sold(lines=[(variant, "1")])
    actor = staff()

    small = _count(lambda: gst_report(actor, NOVEMBER))
    _more_sales(variant, 3)
    large = _count(lambda: gst_report(actor, NOVEMBER))

    assert small == large


def test_the_stock_valuation_does_not_get_slower_per_variant() -> None:
    make_variant(name="Atta", cost="10.00", stock="1.000")
    actor = staff()

    small = _count(lambda: stock_valuation_report(actor))
    for index in range(3):
        make_variant(name=f"Extra {index}", cost="10.00", stock="1.000")
        # Variants with no cost make the blank-cost list longer as well.
        make_variant(name=f"No cost {index}", cost=None, stock="1.000")
    large = _count(lambda: stock_valuation_report(actor))

    assert small == large
