"""Tests for the ProductImage admin inline.

All tests use PostgreSQL via pytest-django and the Django test client.
"""

import pytest
from django.contrib import admin
from django.test import Client

from apps.accounts.models import User
from apps.catalog.admin import ProductImageInline
from apps.catalog.models import Product, ProductImage
from apps.catalog.tests.factories import ProductFactory, ProductImageFactory

pytestmark = pytest.mark.django_db


def product_change_url(product_pk):
    return f"/admin/catalog/product/{product_pk}/change/"


def client_as_superuser():
    """Return a test client logged in as a fresh superuser identified by phone."""
    user = User.objects.create_superuser(phone="+919876543210", password="ok")
    client = Client()
    client.force_login(user)
    return client


def _inline():
    """Return a ProductImageInline bound to a real admin site.

    InlineModelAdmin's constructor takes the *parent* model and the admin site
    as positional arguments. The inline already knows its own ``model``
    (ProductImage) from the class body, so we pass Product here and use the
    real admin.site that Django registers all @admin.register models on.
    We only call ``preview`` on the instance, which does not need a real
    request, so this is enough for the unit checks below.
    """
    return ProductImageInline(Product, admin.site)


class TestProductChangePage:
    def test_change_page_loads_with_image(self):
        product = ProductFactory()
        image = ProductImageFactory(product=product)
        # The factory saves the row, but the Storage-based url is only set once
        # the file is committed to MEDIA_ROOT. Save again so the change page
        # reflects the same behavior staff see in the app.
        image.save()
        client = client_as_superuser()
        response = client.get(product_change_url(product.pk))
        assert response.status_code == 200
        assert image.image.url in response.content.decode("utf-8", "replace")


class TestProductImageInlinePreview:
    def test_preview_contains_image_url_for_saved_image(self):
        product = ProductFactory()
        image = ProductImageFactory(product=product)
        inline = _inline()
        image.save()
        html = inline.preview(image)
        assert image.image.url in html

    def test_preview_returns_placeholder_for_unsaved_image(self):
        inline = _inline()
        assert inline.preview(ProductImage()) == "-"

    def test_preview_escapes_script_in_alt_text(self):
        product = ProductFactory()
        alt_text = "<script>alert('x')</script>"
        # ProductImageFactory already saves the row, so the extra .save() is
        # redundant; we only need the field values the factory sets.
        image = ProductImageFactory(product=product, alt_text=alt_text, sort_order=0)
        inline = _inline()
        html = inline.preview(image)
        # The raw tag must not appear; the escaped version must.
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
