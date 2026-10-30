"""Tests for the "Change prices of selected variants" admin action (Slice 21e).

The page is the action's intermediate page, so every request posts to the variant
changelist with ``action=change_prices`` and the selected ids, exactly as Django's
own action bar and the page's two forms do. The real admin URL is used throughout,
so login, permissions, Django's action handling and both templates are exercised.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.audit import VARIANT_UPDATE_ACTION
from apps.catalog.price_update import MAX_VARIANTS
from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.catalog.tests.test_price_update import make_variant
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:catalog_productvariant_changelist"

# The spec most tests preview: +10% on the selling price, rounded to a rupee. The
# variants from ``make_variant`` sell at 10.00, so the new price is 11.00.
SPEC = {"field": "selling_price", "method": "percent", "value": "10", "step": "1"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _client(
    *, superuser: bool = False, codenames: tuple[str, ...] = (), phone: str = "+919876543400"
) -> Client:
    """A logged-in client: a superuser, or a staff user with those permissions."""
    if superuser:
        user = User.objects.create_superuser(phone=phone, password="ok")
    else:
        user = User.objects.create_user(phone=phone, password="ok", is_staff=True)
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
    client = Client()
    client.force_login(user)
    return client


def _post_action(client: Client, pks: list[int], **extra: str) -> HttpResponse:
    """Post to the changelist the way the action bar and the page's buttons do."""
    data: dict[str, object] = {
        "action": "change_prices",
        "select_across": "0",
        "index": "0",
        "_selected_action": [str(pk) for pk in pks],
    }
    data.update(extra)
    return client.post(reverse(CHANGELIST), data)


def _text(response: HttpResponse) -> str:
    return response.content.decode("utf-8", "replace")


def _hidden_values(page: str, name: str) -> list[str]:
    """The values of every hidden input with this name, in page order."""
    return re.findall(rf'name="{name}" value="([^"]*)"', page)


# ---------------------------------------------------------------------------
# Who may run the action
# ---------------------------------------------------------------------------
def test_the_action_is_missing_without_the_change_permission() -> None:
    # Django hides the whole action bar on an empty changelist (ChangeList builds
    # show_admin_actions from the row count), so this test needs one row: without it
    # the "not in page" assertion would pass for every user.
    make_variant()
    # ``permissions=["change"]`` makes Django look for has_change_permission, so the
    # action must not be offered to somebody who may only look at variants.
    viewer = _client(codenames=("view_productvariant",), phone="+919876543401")
    page = _text(viewer.get(reverse(CHANGELIST)))
    assert "Change prices of selected variants" not in page

    # The same staff user with the change permission does see it.
    editor = _client(
        codenames=("view_productvariant", "change_productvariant"),
        phone="+919876543402",
    )
    page = _text(editor.get(reverse(CHANGELIST)))
    assert "Change prices of selected variants" in page


# ---------------------------------------------------------------------------
# The form and the preview
# ---------------------------------------------------------------------------
def test_the_first_post_shows_the_form() -> None:
    client = _client(superuser=True)
    variant = make_variant()

    response = _post_action(client, [variant.pk])

    assert response.status_code == 200
    page = _text(response)
    assert "Change prices of selected variants" in page
    assert 'name="preview"' in page
    assert f'name="_selected_action" value="{variant.pk}"' in page
    assert "1 variant(s) selected" in page
    # Showing the form builds no plan and writes nothing.
    assert not AuditLog.objects.exists()


def test_preview_writes_nothing_and_offers_confirm() -> None:
    client = _client(superuser=True)
    variant = make_variant()

    response = _post_action(client, [variant.pk], **SPEC, preview="1")

    assert response.status_code == 200
    page = _text(response)
    assert "What this run would do" in page
    assert "11.00" in page  # the new price in the changes table
    assert "Confirm the change" in page

    # The preview is read-only: the price and the audit log are untouched.
    variant.refresh_from_db()
    assert variant.selling_price == Decimal("10.00")
    assert not AuditLog.objects.exists()


def test_preview_with_a_problem_offers_no_confirm() -> None:
    client = _client(superuser=True)
    variant = make_variant()

    response = _post_action(
        client,
        [variant.pk],
        field="selling_price",
        method="set",
        value="0",
        step="1",
        preview="1",
    )

    assert response.status_code == 200
    page = _text(response)
    assert "more than 0" in page
    assert "Confirm the change" not in page
    assert 'name="fingerprint"' not in page


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------
def test_confirm_changes_the_price_audits_it_and_redirects_with_a_message() -> None:
    client = _client(superuser=True)
    variant = make_variant()
    preview = _post_action(client, [variant.pk], **SPEC, preview="1")
    page = _text(preview)
    pks = _hidden_values(page, "_selected_action")
    fingerprint = _hidden_values(page, "fingerprint")[0]

    response = _post_action(client, pks, **SPEC, confirm="1", fingerprint=fingerprint)

    assert response.status_code == 302
    assert response["Location"] == reverse(CHANGELIST)

    variant.refresh_from_db()
    assert variant.selling_price == Decimal("11.00")
    row = AuditLog.objects.get()
    assert row.action == VARIANT_UPDATE_ACTION
    assert row.changes == {"selling_price": ["10.00", "11.00"]}
    assert row.note == "Bulk price update: selling price +10% (round to 1)"
    assert row.actor_id == int(client.session["_auth_user_id"])

    # The success message is shown on the page the redirect points at.
    page = _text(client.get(reverse(CHANGELIST)))
    assert "Changed the price of 1 variant(s)" in page


def test_confirm_with_a_stale_fingerprint_writes_nothing() -> None:
    client = _client(superuser=True)
    variant = make_variant()
    preview = _post_action(client, [variant.pk], **SPEC, preview="1")
    page = _text(preview)
    pks = _hidden_values(page, "_selected_action")
    fingerprint = _hidden_values(page, "fingerprint")[0]

    # Somebody edits the variant after the preview was shown.
    variant.selling_price = Decimal("20.00")
    variant.save()

    response = _post_action(client, pks, **SPEC, confirm="1", fingerprint=fingerprint)

    assert response.status_code == 200
    assert "The prices changed since the preview" in _text(response)
    # The other person's change survives and nothing was audited.
    variant.refresh_from_db()
    assert variant.selling_price == Decimal("20.00")
    assert not AuditLog.objects.exists()


# ---------------------------------------------------------------------------
# The run limit and the carried ids
# ---------------------------------------------------------------------------
def test_more_than_max_variants_is_refused() -> None:
    client = _client(superuser=True)
    # The admin counts the selected rows that exist, so this one needs real rows.
    product = ProductFactory()
    variants = ProductVariantFactory.create_batch(MAX_VARIANTS + 1, product=product)

    # Post the changelist's "select all" state: one ticked row plus select_across=1,
    # which makes Django hand the action the whole queryset. Posting every id would
    # need 1,001 _selected_action fields and trip DATA_UPLOAD_MAX_NUMBER_FIELDS
    # (1,000), which answers 400 before the action ever runs.
    response = _post_action(client, [variants[0].pk], select_across="1")

    assert response.status_code == 302
    page = _text(client.get(reverse(CHANGELIST)))
    assert f"Select at most {MAX_VARIANTS} variants per run." in page
    assert not AuditLog.objects.exists()
    variants[0].refresh_from_db()
    assert variants[0].selling_price == Decimal("95.00")


def test_hidden_ids_survive_from_preview_to_confirm() -> None:
    client = _client(superuser=True)
    first = make_variant()
    second = make_variant()
    untouched = make_variant()

    page = _text(_post_action(client, [first.pk, second.pk], **SPEC, preview="1"))

    # Both forms on the page carry the ids, so the set must be exactly the selection.
    carried = _hidden_values(page, "_selected_action")
    assert set(carried) == {str(first.pk), str(second.pk)}
    fingerprint = _hidden_values(page, "fingerprint")[0]

    response = _post_action(client, carried, **SPEC, confirm="1", fingerprint=fingerprint)

    assert response.status_code == 302
    first.refresh_from_db()
    second.refresh_from_db()
    untouched.refresh_from_db()
    assert first.selling_price == Decimal("11.00")
    assert second.selling_price == Decimal("11.00")
    assert untouched.selling_price == Decimal("10.00")
    assert AuditLog.objects.count() == 2


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------
def test_a_script_in_an_sku_is_escaped_in_the_preview() -> None:
    client = _client(superuser=True)
    variant = make_variant(sku_code="<script>alert(1)</script>")

    page = _text(_post_action(client, [variant.pk], **SPEC, preview="1"))

    # The changes table prints the SKU, so its tags must arrive escaped.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page
