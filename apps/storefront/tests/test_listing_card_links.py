"""Tests for listing card links (Slice 25)."""

from __future__ import annotations

import re

import pytest
from django.test import Client

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)


@pytest.mark.django_db
def test_listing_cards_link_to_detail_page(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    assert f"/product/{p.slug}/" in html


@pytest.mark.django_db
def test_card_has_exactly_one_link(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get("/products/")
    html = response.content.decode()
    m = re.search(r"<article[^>]*>.*?</article>", html, re.S)
    assert m is not None
    assert m.group(0).count("<a ") == 1


@pytest.mark.django_db
def test_card_link_works_on_category_and_brand_pages(client: Client) -> None:
    brand = BrandFactory(is_active=True)
    cat = CategoryFactory(is_active=True)
    p = ProductFactory(brand=brand, category=cat, is_active=True)
    ProductVariantFactory(product=p, is_active=True)
    response = client.get(f"/category/{cat.slug}/")
    html = response.content.decode()
    assert f"/product/{p.slug}/" in html
    response2 = client.get(f"/brand/{brand.slug}/")
    html2 = response2.content.decode()
    assert f"/product/{p.slug}/" in html2
