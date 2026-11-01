"""Tests for storefront listing views (Slice 23d/23f)."""

from __future__ import annotations

import pytest
from django.test import Client

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
)


@pytest.mark.django_db
def test_all_pages_200(client: Client) -> None:
    ProductFactory()
    assert client.get("/products/").status_code == 200
    cat = CategoryFactory(is_active=True)
    assert client.get(f"/category/{cat.slug}/").status_code == 200
    brand = BrandFactory(is_active=True)
    assert client.get(f"/brand/{brand.slug}/").status_code == 200


@pytest.mark.django_db
def test_404_cases(client: Client) -> None:
    assert client.get("/category/nope/").status_code == 404
    assert client.get("/brand/nope/").status_code == 404
    cat = CategoryFactory(is_active=False)
    assert client.get(f"/category/{cat.slug}/").status_code == 404


@pytest.mark.django_db
def test_home_nav_links(client: Client) -> None:
    response = client.get("/")
    html = response.content.decode()
    assert 'href="/products/"' in html
    assert 'href="/"' in html


@pytest.mark.django_db
def test_base_html_htmx_script(client: Client) -> None:
    response = client.get("/")
    html = response.content.decode()
    assert "htmx-2.0.4.min.js" in html
    assert 'rel="stylesheet"' not in html or "htmx" not in html.split('rel="stylesheet"')[0]
