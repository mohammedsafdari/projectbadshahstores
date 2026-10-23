"""Tests for ProductVariant.low_stock_threshold and its database constraint."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from .factories import ProductFactory, ProductVariantFactory

# Every test below touches the database.
pytestmark = pytest.mark.django_db


def test_low_stock_threshold_default_is_none() -> None:
    variant = ProductVariantFactory()
    assert variant.low_stock_threshold is None


def test_blank_low_stock_threshold_passes_full_clean_and_saves() -> None:
    variant = ProductVariantFactory(low_stock_threshold=None)
    variant.full_clean()
    variant.save()
    variant.refresh_from_db()
    assert variant.low_stock_threshold is None


def test_zero_and_positive_threshold_save_and_round_trip() -> None:
    for value in (Decimal("0"), Decimal("5.500")):
        variant = ProductVariantFactory(low_stock_threshold=value)
        variant.save()
        variant.refresh_from_db()
        assert variant.low_stock_threshold == value


def test_negative_threshold_is_rejected_by_database() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(
        product=product,
        low_stock_threshold=Decimal("-1.000"),
    )
    with pytest.raises(IntegrityError):
        variant.save()


def test_negative_threshold_fails_full_clean() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(
        product=product,
        low_stock_threshold=Decimal("-1.000"),
    )
    with pytest.raises(ValidationError) as excinfo:
        variant.full_clean()
    message_dict = excinfo.value.message_dict
    assert "__all__" in message_dict or "low_stock_threshold" in message_dict
