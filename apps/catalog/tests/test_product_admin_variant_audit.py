"""Tests for auditing variant edits made inside the product page (Slice 17f).

These post the real product change page through the Django test client, so the
whole chain runs: the inline formset, ProductAdmin.save_formset and the audit helpers.
"""

from decimal import Decimal
from typing import Any

import pytest
from django import forms
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product, ProductVariant
from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def _login() -> tuple[Client, User]:
    user = User.objects.create_superuser(phone="+919876543210", password="ok")
    client = Client()
    client.force_login(user)
    return client, user


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _add_form_values(data: dict[str, str], prefix: str, form: forms.BaseForm) -> None:
    """Copy one form's initial values into POST data, the way a browser would.

    A browser sends text for every field, an empty string for a blank one, and
    sends a ticked checkbox as "on" and leaves an unticked one out.
    """
    for name, field in form.fields.items():
        value = form.initial.get(name, field.initial)
        key = f"{prefix}{name}"
        if isinstance(field, forms.BooleanField):
            if value:
                data[key] = "on"
        else:
            data[key] = _text(value)


def _post_data(client: Client, product: Product) -> dict[str, str]:
    """Build the POST data for the product page from what the page itself shows."""
    url = reverse("admin:catalog_product_change", args=[product.pk])
    response = client.get(url)
    assert response.status_code == 200
    data: dict[str, str] = {}
    _add_form_values(data, "", response.context["adminform"].form)
    for inline_admin_formset in response.context["inline_admin_formsets"]:
        formset = inline_admin_formset.formset
        for name, value in formset.management_form.initial.items():
            data[f"{formset.prefix}-{name}"] = _text(value)
        for index, form in enumerate(formset.forms):
            _add_form_values(data, f"{formset.prefix}-{index}-", form)
    return data


def _post(client: Client, product: Product, data: dict[str, str]) -> Any:
    url = reverse("admin:catalog_product_change", args=[product.pk])
    return client.post(url, data)


def test_unchanged_post_saves_and_writes_no_row() -> None:
    client, _ = _login()
    variant = ProductVariantFactory()
    data = _post_data(client, variant.product)
    response = _post(client, variant.product, data)
    assert response.status_code == 302
    assert AuditLog.objects.count() == 0


def test_price_change_inside_product_page_writes_one_row() -> None:
    client, user = _login()
    variant = ProductVariantFactory()
    data = _post_data(client, variant.product)
    data["variants-0-selling_price"] = "90.00"
    response = _post(client, variant.product, data)
    assert response.status_code == 302
    variant.refresh_from_db()
    assert variant.selling_price == Decimal("90.00")
    row = AuditLog.objects.get()
    assert row.actor == user
    assert row.target_type == "catalog.productvariant"
    assert row.target_id == str(variant.pk)
    assert row.changes == {"selling_price": ["95.00", "90.00"]}


def test_two_changed_variants_write_two_rows() -> None:
    client, _ = _login()
    product = ProductFactory()
    first = ProductVariantFactory(product=product)
    second = ProductVariantFactory(product=product)
    data = _post_data(client, product)
    data["variants-0-selling_price"] = "90.00"
    data["variants-1-selling_price"] = "90.00"
    response = _post(client, product, data)
    assert response.status_code == 302
    assert AuditLog.objects.count() == 2
    assert set(AuditLog.objects.values_list("target_id", flat=True)) == {
        str(first.pk),
        str(second.pk),
    }


def test_change_to_a_non_audited_inline_field_writes_no_row() -> None:
    client, _ = _login()
    variant = ProductVariantFactory()
    data = _post_data(client, variant.product)
    data["variants-0-sku_code"] = "CHANGED-SKU"
    response = _post(client, variant.product, data)
    assert response.status_code == 302
    variant.refresh_from_db()
    assert variant.sku_code == "CHANGED-SKU"
    assert AuditLog.objects.count() == 0


def test_change_to_the_product_only_writes_no_row() -> None:
    client, _ = _login()
    variant = ProductVariantFactory()
    data = _post_data(client, variant.product)
    data["name_en"] = "A new product name"
    response = _post(client, variant.product, data)
    assert response.status_code == 302
    variant.product.refresh_from_db()
    assert variant.product.name_en == "A new product name"
    assert AuditLog.objects.count() == 0


def test_invalid_price_is_rejected_and_writes_no_row() -> None:
    client, _ = _login()
    variant = ProductVariantFactory()
    data = _post_data(client, variant.product)
    data["variants-0-selling_price"] = "150.00"  # above the MRP of 100.00
    response = _post(client, variant.product, data)
    assert response.status_code == 200  # the page is shown again with an error
    variant.refresh_from_db()
    assert variant.selling_price == Decimal("95.00")
    assert AuditLog.objects.count() == 0


def test_variant_ticked_for_deletion_writes_no_row() -> None:
    client, _ = _login()
    variant = ProductVariantFactory()
    data = _post_data(client, variant.product)
    data["variants-0-DELETE"] = "on"
    data["variants-0-selling_price"] = "90.00"
    response = _post(client, variant.product, data)
    assert response.status_code == 302
    assert not ProductVariant.objects.filter(pk=variant.pk).exists()
    assert AuditLog.objects.count() == 0
