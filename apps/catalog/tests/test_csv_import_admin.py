"""Tests for the "Import CSV" admin page (Slice 20d, ADR D47).

The page has two steps: upload and preview (writes nothing), then Confirm (writes
through ``apply_import``). The tests use the real admin URL, so login, permissions
and the templates are all exercised.
"""

from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.admin import IMPORT_PERMISSIONS
from apps.catalog.audit import VARIANT_CREATE_ACTION
from apps.catalog.csv_import import MAX_FILE_BYTES
from apps.catalog.models import ProductVariant
from apps.catalog.tests import test_csv_import_plan as plan_tests
from apps.catalog.tests.test_csv_import_parse import make_csv
from apps.catalog.tests.test_csv_import_plan import SKU, stored_catalog
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

IMPORT_URL = "admin:catalog_productvariant_import_csv"
CHANGELIST = "admin:catalog_productvariant_changelist"

# The view needs these four codenames; kept in step with the admin's own tuple.
IMPORT_CODENAMES = tuple(permission.split(".", 1)[1] for permission in IMPORT_PERMISSIONS)

# pytest finds a fixture by looking at this module's attributes, so an alias reuses the
# plan tests' ``catalog`` fixture without shadowing it in the test signatures (F811).
catalog = plan_tests.catalog


def _client(
    *, superuser: bool = False, codenames: tuple[str, ...] = (), phone: str = "+919876543300"
) -> Client:
    if superuser:
        user = User.objects.create_superuser(phone=phone, password="ok")
    else:
        user = User.objects.create_user(phone=phone, password="ok", is_staff=True)
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
    client = Client()
    client.force_login(user)
    return client


def _upload(client: Client, raw: bytes, name: str = "file.csv") -> object:
    return client.post(
        reverse(IMPORT_URL),
        {"csv_file": SimpleUploadedFile(name, raw, content_type="text/csv")},
    )


def _confirm(client: Client) -> object:
    return client.post(reverse(IMPORT_URL), {"confirm": "1"})


def _text(response: object) -> str:
    return response.content.decode("utf-8", "replace")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Who may open the page
# ---------------------------------------------------------------------------
def test_anonymous_is_redirected_to_the_admin_login() -> None:
    response = Client().get(reverse(IMPORT_URL))
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_staff_without_the_permissions_gets_403_on_get() -> None:
    # Seeing variants is not enough: the page changes products and variants.
    client = _client(codenames=("view_productvariant",))
    response = client.get(reverse(IMPORT_URL))
    assert response.status_code == 403


def test_staff_without_the_permissions_gets_403_on_post() -> None:
    client = _client(codenames=("view_productvariant",))
    response = _upload(client, make_csv({}))
    assert response.status_code == 403  # type: ignore[attr-defined]
    assert not ProductVariant.objects.exists()


def test_button_is_absent_without_the_permissions() -> None:
    client = _client(codenames=("view_productvariant",))
    response = client.get(reverse(CHANGELIST))
    assert response.status_code == 200
    assert "Import CSV" not in _text(response)


def test_button_is_present_with_the_permissions() -> None:
    client = _client(codenames=("view_productvariant", *IMPORT_CODENAMES))
    response = client.get(reverse(CHANGELIST))
    assert response.status_code == 200
    page = _text(response)
    assert "Import CSV" in page
    assert reverse(IMPORT_URL) in page


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------
def test_preview_of_a_good_file_writes_nothing_and_offers_confirm(catalog) -> None:
    client = _client(superuser=True)
    response = _upload(client, make_csv({}))
    assert response.status_code == 200  # type: ignore[attr-defined]
    page = _text(response)
    assert "to create" in page
    assert "Confirm import" in page
    # A preview is read-only: no variant and no audit row yet.
    assert not ProductVariant.objects.exists()
    assert not AuditLog.objects.exists()


def test_preview_of_a_bad_file_shows_the_problems_and_no_confirm(catalog) -> None:
    client = _client(superuser=True)
    response = _upload(client, make_csv({"brand_slug": "no-such-brand"}))
    assert response.status_code == 200  # type: ignore[attr-defined]
    page = _text(response)
    assert "Unknown brand" in page
    assert "brand_slug" in page
    assert "Confirm import" not in page


def test_oversized_file_is_refused_without_a_crash(catalog) -> None:
    client = _client(superuser=True)
    response = _upload(client, b"x" * (MAX_FILE_BYTES + 1))
    assert response.status_code == 200  # type: ignore[attr-defined]
    page = _text(response)
    assert "larger than 2 MB" in page
    assert "Confirm import" not in page
    assert not ProductVariant.objects.exists()


def test_html_in_a_cell_is_escaped_in_the_report(catalog) -> None:
    client = _client(superuser=True)
    response = _upload(client, make_csv({"hsn_code": "11<script>alert(1)</script>"}))
    assert response.status_code == 200  # type: ignore[attr-defined]
    page = _text(response)
    # The problem message quotes the cell, so the tags must arrive escaped.
    assert "&lt;script&gt;" in page
    assert "<script>alert(1)</script>" not in page


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------
def test_confirm_creates_the_variant_audits_it_and_redirects(catalog) -> None:
    client = _client(superuser=True)
    _upload(client, make_csv({}))
    response = _confirm(client)
    assert response.status_code == 302  # type: ignore[attr-defined]
    assert response["Location"] == reverse(CHANGELIST)  # type: ignore[index]

    variant = ProductVariant.objects.get(sku_code=SKU)
    assert (
        AuditLog.objects.filter(action=VARIANT_CREATE_ACTION, target_id=str(variant.pk)).count()
        == 1
    )


def test_confirm_without_a_session_entry_asks_for_the_file_again() -> None:
    client = _client(superuser=True)
    response = _confirm(client)
    assert response.status_code == 302  # type: ignore[attr-defined]
    # The message is shown on the page the redirect points at.
    page = _text(client.get(reverse(IMPORT_URL)))
    assert "Upload the file again" in page
    assert not ProductVariant.objects.exists()


def test_confirm_after_an_edit_reports_that_the_data_changed(catalog) -> None:
    client = _client(superuser=True)
    variant = stored_catalog(catalog)
    _upload(client, make_csv({"selling_price": "250.00"}))

    # Somebody changes the variant after the preview was shown.
    variant.selling_price = Decimal("260.00")
    variant.save()

    response = _confirm(client)
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert "The data changed since the preview" in _text(response)

    variant.refresh_from_db()
    assert variant.selling_price == Decimal("260.00")  # the file was not applied
    assert not AuditLog.objects.exists()


def test_confirm_removes_the_session_entry(catalog) -> None:
    client = _client(superuser=True)
    _upload(client, make_csv({}))
    assert "catalog_import" in client.session

    _confirm(client)
    assert "catalog_import" not in client.session
