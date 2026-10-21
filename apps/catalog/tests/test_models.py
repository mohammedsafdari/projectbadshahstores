"""Tests for the catalog Brand and Category models."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError

from apps.catalog.models import Brand, Category

from .factories import BrandFactory, CategoryFactory

# Every test below touches the database.
pytestmark = pytest.mark.django_db


def test_brand_str_is_name() -> None:
    brand = BrandFactory(name="Tata")
    assert str(brand) == "Tata"


def test_brand_defaults_and_timestamps() -> None:
    brand = BrandFactory()
    assert brand.is_active is True
    assert brand.created_at is not None
    assert brand.updated_at is not None


def test_brand_name_must_be_unique() -> None:
    BrandFactory(name="Tata", slug="tata")
    with pytest.raises(IntegrityError):
        BrandFactory(name="Tata", slug="tata-2")


def test_brands_are_ordered_by_name() -> None:
    BrandFactory(name="Zed", slug="zed")
    BrandFactory(name="Alpha", slug="alpha")
    assert [b.name for b in Brand.objects.all()] == ["Alpha", "Zed"]


def test_slug_accepts_romanised_text() -> None:
    # full_clean() runs Django's validators without saving.
    brand = Brand(name="आटा", slug="atta-flour")
    brand.full_clean()


def test_slug_rejects_devanagari() -> None:
    # Slugs are romanised on purpose. Hindi text belongs in name fields.
    brand = Brand(name="आटा", slug="आटा")
    with pytest.raises(ValidationError):
        brand.full_clean()


def test_category_parent_and_children() -> None:
    parent = CategoryFactory(name="Staples")
    child = CategoryFactory(name="Atta and Flour", parent=parent)
    assert child.parent == parent
    assert list(parent.children.all()) == [child]


def test_top_level_category_has_no_parent() -> None:
    assert CategoryFactory().parent is None


def test_category_with_children_cannot_be_deleted() -> None:
    parent = CategoryFactory()
    CategoryFactory(parent=parent)
    with pytest.raises(ProtectedError):
        parent.delete()
    assert Category.objects.filter(pk=parent.pk).exists()


def test_category_str_and_plural_name() -> None:
    category = CategoryFactory(name="Spices")
    assert str(category) == "Spices"
    assert Category._meta.verbose_name_plural == "categories"
