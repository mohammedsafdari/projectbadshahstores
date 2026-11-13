"""Tests for the featured product model (D84).

REVIEW CAREFULLY: the home page order and the twelve-row cap.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.promotions.models import FEATURED_MAX_ROWS, FeaturedProduct

pytestmark = pytest.mark.django_db

# The exact sentence a thirteenth row shows.
SENTENCE = "You can feature at most 12 products. Remove one before adding another."


def _fill(rows: int = FEATURED_MAX_ROWS) -> list[FeaturedProduct]:
    """Create ``rows`` featured rows, each with its own product."""
    return [
        FeaturedProduct.objects.create(product=ProductFactory(), sort_order=index)
        for index in range(rows)
    ]


def test_twelve_rows_are_allowed() -> None:
    _fill()

    assert FeaturedProduct.objects.count() == 12


def test_the_thirteenth_row_is_refused_with_the_sentence() -> None:
    _fill()

    with pytest.raises(ValidationError) as caught:
        FeaturedProduct(product=ProductFactory()).full_clean()

    assert caught.value.messages == [SENTENCE]


def test_eleven_rows_leave_room_for_one_more() -> None:
    _fill(11)

    # No error: a twelfth row still fits.
    FeaturedProduct(product=ProductFactory()).full_clean()

    assert FeaturedProduct.objects.count() == 11


def test_editing_one_of_the_twelve_rows_is_allowed() -> None:
    rows = _fill()
    rows[0].sort_order = 3

    # An existing row never counts itself, so this is not a thirteenth row.
    rows[0].full_clean()

    assert FeaturedProduct.objects.count() == 12


def test_a_second_row_for_the_same_product_is_refused() -> None:
    product = ProductFactory()
    FeaturedProduct.objects.create(product=product)

    with pytest.raises(IntegrityError), transaction.atomic():
        FeaturedProduct.objects.create(product=product)


def test_the_rows_are_ordered_by_sort_order_then_id() -> None:
    second = FeaturedProduct.objects.create(product=ProductFactory(), sort_order=2)
    first = FeaturedProduct.objects.create(product=ProductFactory(), sort_order=1)
    tied_later = FeaturedProduct.objects.create(product=ProductFactory(), sort_order=1)

    assert list(FeaturedProduct.objects.all()) == [first, tied_later, second]


def test_the_sort_order_defaults_to_zero() -> None:
    row = FeaturedProduct.objects.create(product=ProductFactory())

    assert row.sort_order == 0


def test_deleting_a_product_removes_its_row() -> None:
    row = FeaturedProduct.objects.create(product=ProductFactory())

    row.product.delete()

    assert not FeaturedProduct.objects.filter(pk=row.pk).exists()


def test_deleting_a_row_keeps_the_product() -> None:
    row = FeaturedProduct.objects.create(product=ProductFactory())
    product_id = row.product_id

    row.delete()

    assert Product.objects.filter(pk=product_id).exists()


def test_the_printed_name_is_the_product_name() -> None:
    product = ProductFactory(name_en="Basmati Rice")

    assert str(FeaturedProduct(product=product)) == "Basmati Rice"
