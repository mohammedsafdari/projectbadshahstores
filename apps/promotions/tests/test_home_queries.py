"""Tests for the two home page queries (D84).

REVIEW CAREFULLY: what the public home page shows, and in what order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.tests.factories import ProductFactory
from apps.promotions.home import featured_product_ids, visible_banners
from apps.promotions.models import FeaturedProduct
from apps.promotions.tests.helpers import make_banner

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


@pytest.fixture(autouse=True)
def _media_root_on_tmp_path(tmp_path, settings):
    """Every uploaded banner image goes to the temporary folder."""
    settings.MEDIA_ROOT = tmp_path


# ---------------------------------------------------------------------------
# visible_banners: the window
# ---------------------------------------------------------------------------
def test_an_inactive_banner_is_hidden() -> None:
    make_banner(is_active=False)

    assert visible_banners(NOW) == []


def test_a_banner_that_has_not_started_is_hidden() -> None:
    make_banner(starts_at=NOW + HOUR)

    assert visible_banners(NOW) == []


def test_a_banner_that_starts_exactly_now_is_shown() -> None:
    banner = make_banner(starts_at=NOW)

    assert visible_banners(NOW) == [banner]


def test_a_banner_that_starts_in_the_past_is_shown() -> None:
    banner = make_banner(starts_at=NOW - HOUR)

    assert visible_banners(NOW) == [banner]


def test_a_banner_that_ends_exactly_now_is_hidden() -> None:
    # The end is exclusive: at the end moment the offer is already over.
    make_banner(ends_at=NOW)

    assert visible_banners(NOW) == []


def test_a_banner_that_ends_in_the_future_is_shown() -> None:
    banner = make_banner(ends_at=NOW + HOUR)

    assert visible_banners(NOW) == [banner]


def test_a_banner_that_ended_in_the_past_is_hidden() -> None:
    make_banner(ends_at=NOW - HOUR)

    assert visible_banners(NOW) == []


def test_a_banner_with_no_dates_is_shown() -> None:
    banner = make_banner()

    assert visible_banners(NOW) == [banner]


def test_a_banner_with_no_start_but_an_end_in_the_future_is_shown() -> None:
    banner = make_banner(ends_at=NOW + HOUR)

    assert visible_banners(NOW) == [banner]


def test_a_banner_with_a_start_in_the_past_and_no_end_is_shown() -> None:
    banner = make_banner(starts_at=NOW - HOUR)

    assert visible_banners(NOW) == [banner]


def test_the_clock_defaults_to_now() -> None:
    # No ``now`` argument: the real clock is used, so a window that already
    # opened and has not closed is visible.
    banner = make_banner(starts_at=NOW - timedelta(days=365), ends_at=NOW + timedelta(days=365))

    assert visible_banners() == [banner]


# ---------------------------------------------------------------------------
# visible_banners: the order and the limit
# ---------------------------------------------------------------------------
def test_banners_are_ordered_by_sort_order_then_id() -> None:
    second = make_banner(title="Second", sort_order=2)
    first = make_banner(title="First", sort_order=1)
    tied_later = make_banner(title="Tied", sort_order=1)

    assert visible_banners(NOW) == [first, tied_later, second]


def test_at_most_five_banners_are_returned() -> None:
    # The five is written out, not read from the constant: the test must fail if
    # somebody changes the limit, which is the point of the limit.
    for index in range(6):
        make_banner(title=f"Banner {index}", sort_order=index)

    assert len(visible_banners(NOW)) == 5


def test_the_sixth_banner_is_the_one_left_out() -> None:
    banners = [make_banner(title=f"Banner {index}", sort_order=index) for index in range(6)]

    assert visible_banners(NOW) == banners[:5]


# ---------------------------------------------------------------------------
# featured_product_ids
# ---------------------------------------------------------------------------
def test_featured_ids_keep_the_stored_order() -> None:
    second = FeaturedProduct.objects.create(product=ProductFactory(), sort_order=2)
    first = FeaturedProduct.objects.create(product=ProductFactory(), sort_order=1)

    assert featured_product_ids() == [first.product_id, second.product_id]


def test_featured_ids_are_ordered_by_sort_order_then_id() -> None:
    second = FeaturedProduct.objects.create(product=ProductFactory(), sort_order=2)
    first = FeaturedProduct.objects.create(product=ProductFactory(), sort_order=1)
    tied_later = FeaturedProduct.objects.create(product=ProductFactory(), sort_order=1)

    assert featured_product_ids() == [first.product_id, tied_later.product_id, second.product_id]


def test_no_featured_rows_gives_an_empty_list() -> None:
    assert featured_product_ids() == []


def test_an_inactive_product_id_is_still_returned() -> None:
    # This query only reports what staff chose; the storefront decides what shows.
    product = ProductFactory(is_active=False)
    FeaturedProduct.objects.create(product=product)

    assert featured_product_ids() == [product.pk]
