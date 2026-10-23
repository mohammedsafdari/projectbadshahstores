"""Tests for loose-by-weight variants and quantity step rules."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.catalog.models import ProductVariant

from .factories import ProductFactory, ProductVariantFactory

# Every test below touches the database.
pytestmark = pytest.mark.django_db


def test_packet_variant_defaults() -> None:
    variant = ProductVariantFactory()
    assert variant.is_sold_loose is False
    assert variant.quantity_step == Decimal("1")


def test_loose_kg_variant_saves_and_passes_full_clean() -> None:
    variant = ProductVariantFactory(
        unit=ProductVariant.Unit.KG,
        pack_size=Decimal("1"),
        is_sold_loose=True,
        quantity_step=Decimal("0.100"),
    )
    variant.full_clean()
    variant.save()
    variant.refresh_from_db()
    assert variant.is_sold_loose is True
    assert variant.quantity_step == Decimal("0.100")


def test_loose_l_variant_saves() -> None:
    variant = ProductVariantFactory(
        unit=ProductVariant.Unit.L,
        pack_size=Decimal("1"),
        is_sold_loose=True,
    )
    variant.save()
    assert variant.unit == ProductVariant.Unit.L


@pytest.mark.parametrize(
    "unit",
    [ProductVariant.Unit.PACKET],
)
def test_loose_variant_with_packet_unit_is_rejected_by_database(unit: str) -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(
        product=product,
        unit=unit,
        pack_size=Decimal("1"),
        is_sold_loose=True,
    )
    with pytest.raises(IntegrityError):
        variant.save()


@pytest.mark.parametrize(
    "pack_size",
    [Decimal("0.5")],
)
def test_loose_variant_with_non_unit_pack_size_is_rejected_by_database(
    pack_size: Decimal,
) -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(
        product=product,
        unit=ProductVariant.Unit.KG,
        pack_size=pack_size,
        is_sold_loose=True,
    )
    with pytest.raises(IntegrityError):
        variant.save()


def test_packet_variant_with_non_unit_quantity_step_is_rejected_by_database() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(
        product=product,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1"),
        is_sold_loose=False,
        quantity_step=Decimal("0.5"),
    )
    with pytest.raises(IntegrityError):
        variant.save()


def test_zero_quantity_step_on_loose_variant_is_rejected_by_database() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(
        product=product,
        unit=ProductVariant.Unit.KG,
        pack_size=Decimal("1"),
        is_sold_loose=True,
        quantity_step=Decimal("0"),
    )
    with pytest.raises(IntegrityError):
        variant.save()


def test_loose_variant_with_packet_unit_fails_full_clean() -> None:
    product = ProductFactory()
    variant = ProductVariantFactory.build(
        product=product,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1"),
        is_sold_loose=True,
    )
    with pytest.raises(ValidationError) as excinfo:
        variant.full_clean()
    # ``full_clean`` reports a violated CheckConstraint under NON_FIELD_ERRORS
    # (key ``"__all__"``), not under a model field name.
    all_messages = " ".join(excinfo.value.message_dict["__all__"])
    assert "catalog_variant_loose_unit_and_pack_size" in all_messages
