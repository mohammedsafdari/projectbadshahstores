"""Tests for the CSV export actions in the admin (Slice 19)."""

import csv
import io

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory

pytestmark = pytest.mark.django_db

VARIANT_CHANGELIST = "admin:catalog_productvariant_changelist"
PRODUCT_CHANGELIST = "admin:catalog_product_changelist"


def _client(
    *, superuser: bool = False, codenames: tuple[str, ...] = (), phone: str = "+919876543220"
) -> Client:
    # Each user needs its own phone number (unique), so a test that makes two
    # clients passes a different ``phone`` for the second one.
    if superuser:
        user = User.objects.create_superuser(phone=phone, password="ok")
    else:
        user = User.objects.create_user(phone=phone, password="ok", is_staff=True)
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
    client = Client()
    client.force_login(user)
    return client


def _run_action(client: Client, changelist: str, action: str, selected: list[int]) -> object:
    # The admin action form posts the action name and the ticked row ids.
    return client.post(reverse(changelist), {"action": action, "_selected_action": selected})


def _text(response: object) -> str:
    # response.content is bytes; "utf-8-sig" removes the BOM as Excel would.
    return response.content.decode("utf-8-sig")  # type: ignore[attr-defined]


def _skus(response: object) -> list[str]:
    return [row["sku_code"] for row in csv.DictReader(io.StringIO(_text(response)))]


def test_variant_action_downloads_only_the_selected_variants() -> None:
    picked = ProductVariantFactory(sku_code="PICKED-1")
    ProductVariantFactory(sku_code="SKIPPED-1")
    response = _run_action(_client(superuser=True), VARIANT_CHANGELIST, "export_csv", [picked.pk])
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert _skus(response) == ["PICKED-1"]


def test_download_headers() -> None:
    variant = ProductVariantFactory()
    response = _run_action(_client(superuser=True), VARIANT_CHANGELIST, "export_csv", [variant.pk])
    assert response["Content-Type"] == "text/csv; charset=utf-8"  # type: ignore[index]
    today = timezone.localdate()
    expected = f'attachment; filename="variants-{today:%Y-%m-%d}.csv"'
    assert response["Content-Disposition"] == expected  # type: ignore[index]
    # The admin adds more cache words of its own, so only check ours is present.
    assert "no-store" in response["Cache-Control"]  # type: ignore[index]
    # The BOM bytes (EF BB BF) tell Excel the file is UTF-8.
    assert response.content.startswith(b"\xef\xbb\xbf")  # type: ignore[attr-defined]


def test_hindi_text_is_written_as_utf8() -> None:
    variant = ProductVariantFactory(product=ProductFactory(name_hi="शरबती आटा"))
    response = _run_action(_client(superuser=True), VARIANT_CHANGELIST, "export_csv", [variant.pk])
    assert "शरबती आटा" in _text(response)


def test_product_action_exports_every_variant_of_the_selected_products() -> None:
    product = ProductFactory()
    ProductVariantFactory(product=product, sku_code="P1-500G")
    ProductVariantFactory(product=product, sku_code="P1-1KG")
    ProductVariantFactory(sku_code="OTHER-1")
    response = _run_action(
        _client(superuser=True), PRODUCT_CHANGELIST, "export_variants_csv", [product.pk]
    )
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert _skus(response) == ["P1-1KG", "P1-500G"]


def test_archived_variants_are_included_when_selected() -> None:
    variant = ProductVariantFactory(sku_code="OLD-1", is_active=False)
    response = _run_action(_client(superuser=True), VARIANT_CHANGELIST, "export_csv", [variant.pk])
    assert _skus(response) == ["OLD-1"]


def test_staff_with_variant_view_permission_can_export() -> None:
    variant = ProductVariantFactory(sku_code="V-1")
    client = _client(codenames=("view_productvariant",))
    response = _run_action(client, VARIANT_CHANGELIST, "export_csv", [variant.pk])
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert _skus(response) == ["V-1"]


def test_staff_without_variant_view_permission_gets_no_file_from_the_variant_page() -> None:
    # This user may see products but not variants (the file would show cost prices).
    variant = ProductVariantFactory(sku_code="V-2")
    client = _client(codenames=("view_product",))
    response = _run_action(client, PRODUCT_CHANGELIST, "export_variants_csv", [variant.product_id])
    # Django does not run an action the user is not allowed: it shows the list
    # page again (an HTML page, not a download).
    assert response["Content-Type"].startswith("text/html")  # type: ignore[index]
    assert not response.content.startswith(b"\xef\xbb\xbf")  # type: ignore[attr-defined]


def test_action_is_listed_only_for_users_who_may_use_it() -> None:
    ProductVariantFactory()
    allowed = _client(codenames=("view_productvariant",)).get(reverse(VARIANT_CHANGELIST))
    assert b'value="export_csv"' in allowed.content
    product_only = _client(codenames=("view_product",), phone="+919876543230").get(
        reverse(PRODUCT_CHANGELIST)
    )
    assert b"export_variants_csv" not in product_only.content
    superuser = _client(superuser=True, phone="+919876543210").get(reverse(PRODUCT_CHANGELIST))
    assert b'value="export_variants_csv"' in superuser.content
