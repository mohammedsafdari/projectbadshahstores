"""Tests for marking variants sold loose or as packets in bulk (Slice 29c).

The service tests cover the rules of D42 and D56. The admin tests post to the real
variant changelist, the way Django's action bar does, so login, permissions,
Django's action handling and the two messages are exercised.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.admin import ProductVariantAdmin
from apps.catalog.audit import VARIANT_UPDATE_ACTION
from apps.catalog.loose_marking import (
    LooseMarkingRejected,
    LooseMarkResult,
    set_variants_loose,
)
from apps.catalog.models import ProductVariant
from apps.catalog.price_update import MAX_VARIANTS
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:catalog_productvariant_changelist"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _packet_variant(**changes: Any) -> ProductVariant:
    """A saved packet variant that may become loose: unit KG, pack size 1, step 1."""
    return ProductVariantFactory(**changes)


def _loose_variant(**changes: Any) -> ProductVariant:
    """A saved loose variant: unit KG, pack size 1, step 0.001 unless overridden."""
    fields: dict[str, Any] = {
        "unit": ProductVariant.Unit.KG,
        "pack_size": Decimal("1"),
        "is_sold_loose": True,
        "quantity_step": Decimal("0.001"),
    }
    # One dict, then the overrides: naming a key twice in one call would fail.
    fields.update(changes)
    return ProductVariantFactory(**fields)


def _staff(*codenames: str, phone: str) -> User:
    """A staff user holding exactly the given permission codenames."""
    user = User.objects.create_user(phone=phone, password="ok", is_staff=True)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _post_action(client: Client, action: str, pks: list[int]) -> HttpResponse:
    """Post to the changelist the way the action bar does."""
    data: dict[str, object] = {
        "action": action,
        "select_across": "0",
        "index": "0",
        "_selected_action": [str(pk) for pk in pks],
    }
    return client.post(reverse(CHANGELIST), data)


def _page(response: HttpResponse) -> str:
    return response.content.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# The service: what changes and what does not
# ---------------------------------------------------------------------------
def test_eligible_variant_becomes_loose_with_step_and_one_audit_row() -> None:
    variant = _packet_variant()
    user = User.objects.create_superuser(phone="+919876543500", password="ok")

    result = set_variants_loose([variant.pk], loose=True, actor=user)

    assert result.changed == 1
    assert result.unchanged == 0
    assert result.skipped == []

    variant.refresh_from_db()
    assert variant.is_sold_loose is True
    assert variant.quantity_step == Decimal("0.001")

    row = AuditLog.objects.get()
    assert row.action == VARIANT_UPDATE_ACTION
    assert row.actor == user
    assert row.target_id == str(variant.pk)
    assert row.changes == {
        "is_sold_loose": [False, True],
        "quantity_step": ["1.000", "0.001"],
    }
    assert row.note == "Marked sold loose (admin action)"


@pytest.mark.parametrize(
    ("unit", "pack_size", "reason_part"),
    [
        (ProductVariant.Unit.PACKET, Decimal("1"), "not kg or litre"),
        (ProductVariant.Unit.G, Decimal("1"), "not kg or litre"),
        (ProductVariant.Unit.KG, Decimal("0.5"), "Pack size"),
    ],
)
def test_ineligible_variant_is_skipped_with_a_reason_and_unchanged(
    unit: str, pack_size: Decimal, reason_part: str
) -> None:
    variant = ProductVariantFactory(unit=unit, pack_size=pack_size)

    result = set_variants_loose([variant.pk], loose=True, actor=None)

    assert result.changed == 0
    assert result.unchanged == 0
    assert len(result.skipped) == 1
    skip = result.skipped[0]
    assert skip.sku_code == variant.sku_code
    assert reason_part in skip.reason

    variant.refresh_from_db()
    assert variant.is_sold_loose is False
    assert variant.quantity_step == Decimal("1.000")
    assert not AuditLog.objects.exists()


def test_already_loose_variant_keeps_its_step_and_writes_no_audit_row() -> None:
    variant = _loose_variant(quantity_step=Decimal("0.100"))

    result = set_variants_loose([variant.pk], loose=True, actor=None)

    assert result.changed == 0
    assert result.unchanged == 1
    assert result.skipped == []

    variant.refresh_from_db()
    assert variant.is_sold_loose is True
    # The variant's own step survives; only a newly loose variant gets 0.001.
    assert variant.quantity_step == Decimal("0.100")
    assert not AuditLog.objects.exists()


def test_marking_as_packet_restores_step_one_and_writes_one_row() -> None:
    variant = _loose_variant()

    result = set_variants_loose([variant.pk], loose=False, actor=None)

    assert result.changed == 1
    assert result.unchanged == 0

    variant.refresh_from_db()
    assert variant.is_sold_loose is False
    assert variant.quantity_step == Decimal("1")

    row = AuditLog.objects.get()
    assert row.changes == {
        "is_sold_loose": [True, False],
        "quantity_step": ["0.001", "1"],
    }
    assert row.note == "Marked as packet (admin action)"


def test_already_packet_variant_gets_no_audit_row() -> None:
    variant = _packet_variant()

    result = set_variants_loose([variant.pk], loose=False, actor=None)

    assert result.changed == 0
    assert result.unchanged == 1
    assert result.skipped == []
    assert not AuditLog.objects.exists()


def test_more_than_max_variants_is_refused_and_writes_nothing() -> None:
    # The limit is checked before any row is read, so the ids need not exist.
    pks = range(1, MAX_VARIANTS + 2)

    with pytest.raises(LooseMarkingRejected):
        set_variants_loose(pks, loose=True, actor=None)

    assert not AuditLog.objects.exists()


def test_one_skipped_variant_does_not_stop_the_others() -> None:
    eligible = _packet_variant()
    skipped = ProductVariantFactory(
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1"),
    )

    result = set_variants_loose([eligible.pk, skipped.pk], loose=True, actor=None)

    assert result.changed == 1
    assert result.unchanged == 0
    assert [item.sku_code for item in result.skipped] == [skipped.sku_code]

    eligible.refresh_from_db()
    skipped.refresh_from_db()
    assert eligible.is_sold_loose is True
    assert skipped.is_sold_loose is False
    assert AuditLog.objects.count() == 1


# ---------------------------------------------------------------------------
# The timestamp: save(update_fields=...) must name updated_at too
# ---------------------------------------------------------------------------
def test_marking_as_loose_moves_updated_at_forward() -> None:
    variant = _packet_variant()
    variant.refresh_from_db()  # read the timestamp the database really stores
    before = variant.updated_at

    set_variants_loose([variant.pk], loose=True, actor=None)

    variant.refresh_from_db()
    assert variant.is_sold_loose is True
    assert variant.updated_at > before


def test_marking_as_packet_moves_updated_at_forward() -> None:
    variant = _loose_variant()
    variant.refresh_from_db()
    before = variant.updated_at

    set_variants_loose([variant.pk], loose=False, actor=None)

    variant.refresh_from_db()
    assert variant.is_sold_loose is False
    assert variant.updated_at > before


# ---------------------------------------------------------------------------
# The admin action: who may run it
# ---------------------------------------------------------------------------
def test_the_actions_are_missing_without_the_change_permission() -> None:
    # Django hides the whole action bar on an empty changelist, so one row is needed.
    _packet_variant()
    viewer = _staff("view_productvariant", phone="+919876543501")
    page = _page(_client(viewer).get(reverse(CHANGELIST)))
    assert "Mark selected variants as sold loose" not in page
    assert "Mark selected variants as packets" not in page

    editor = _staff("view_productvariant", "change_productvariant", phone="+919876543502")
    page = _page(_client(editor).get(reverse(CHANGELIST)))
    assert "Mark selected variants as sold loose" in page
    assert "Mark selected variants as packets" in page


def test_a_forged_post_without_the_change_permission_writes_nothing() -> None:
    variant = _packet_variant()
    viewer = _staff("view_productvariant", phone="+919876543503")

    response = _post_action(_client(viewer), "mark_sold_loose", [variant.pk])

    # Django refuses the action before calling it: it redirects back to the
    # changelist, so the action never runs and nothing is written.
    assert response.status_code == 302
    assert response["Location"] == reverse(CHANGELIST)
    variant.refresh_from_db()
    assert variant.is_sold_loose is False
    assert not AuditLog.objects.exists()


def test_a_direct_call_without_the_change_permission_is_denied() -> None:
    # A forged post never reaches the method, so this calls it directly to prove
    # the permission check inside the action is really there.
    variant = _packet_variant()
    viewer = _staff("view_productvariant", phone="+919876543504")
    request = RequestFactory().post("/")
    request.user = viewer
    model_admin = ProductVariantAdmin(ProductVariant, django_admin.site)

    with pytest.raises(PermissionDenied):
        model_admin.mark_sold_loose(request, ProductVariant.objects.all())

    variant.refresh_from_db()
    assert variant.is_sold_loose is False
    assert not AuditLog.objects.exists()


def test_a_superuser_post_changes_the_variants_and_redirects_with_a_message() -> None:
    client = _client(User.objects.create_superuser(phone="+919876543505", password="ok"))
    first = _packet_variant()
    second = _packet_variant()

    response = _post_action(client, "mark_sold_loose", [first.pk, second.pk])

    assert response.status_code == 302
    assert response["Location"] == reverse(CHANGELIST)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_sold_loose is True
    assert second.is_sold_loose is True
    assert first.quantity_step == Decimal("0.001")
    assert AuditLog.objects.count() == 2

    # The success message is shown on the page the redirect points at.
    page = _page(client.get(reverse(CHANGELIST)))
    assert "Marked 2 variant(s) as sold loose" in page
    assert "0 were already and are unchanged" in page


def test_the_warning_lists_skipped_skus_and_escapes_a_script_tag() -> None:
    client = _client(User.objects.create_superuser(phone="+919876543506", password="ok"))
    eligible = _packet_variant()
    skipped = ProductVariantFactory(
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1"),
        sku_code="<script>alert(1)</script>",
    )

    response = _post_action(client, "mark_sold_loose", [eligible.pk, skipped.pk])

    assert response.status_code == 302
    page = _page(client.get(reverse(CHANGELIST)))
    # The escaped SKU and its reason sit in the same warning message.
    assert "Skipped 1 variant(s):" in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;: Unit PACKET is not kg or litre." in page
    assert "<script>alert(1)</script>" not in page


# ---------------------------------------------------------------------------
# The lock: marking loose must wait for a transaction that holds the variant
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_marking_loose_waits_for_a_lock_held_by_another_transaction() -> None:
    variant = _packet_variant()
    variant_pk = variant.pk

    locked = threading.Event()
    outcome: list[LooseMarkResult] = []
    errors: list[BaseException] = []

    def hold_the_lock() -> None:
        """Make the variant loose with step 0.100 while holding the row lock."""
        try:
            with transaction.atomic():
                row = ProductVariant.objects.select_for_update().get(pk=variant_pk)
                row.is_sold_loose = True
                row.quantity_step = Decimal("0.100")
                row.save(update_fields=["is_sold_loose", "quantity_step", "updated_at"])
                locked.set()  # tell the other thread the lock is held
                time.sleep(0.5)  # keep it for a moment, so the other thread must wait
            # Leaving the ``with`` block commits, which releases the lock.
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()  # this thread's own connection

    def mark_while_locked() -> None:
        """Run the marking: it must block, then find the variant already loose."""
        try:
            assert locked.wait(timeout=10)
            outcome.append(set_variants_loose([variant_pk], loose=True, actor=None))
        except BaseException as exc:  # reported to the test below
            errors.append(exc)
        finally:
            connection.close()

    first = threading.Thread(target=hold_the_lock)
    second = threading.Thread(target=mark_while_locked)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    assert not errors, errors
    assert len(outcome) == 1

    # Without the lock, the marking would have read the packet row before the other
    # transaction committed: it would report a change, write its own step 0.001 and
    # add an audit row. With the lock it sees the committed loose row and leaves it.
    assert outcome[0].changed == 0
    assert outcome[0].unchanged == 1
    assert outcome[0].skipped == []

    variant.refresh_from_db()
    assert variant.is_sold_loose is True
    assert variant.quantity_step == Decimal("0.100")
    assert AuditLog.objects.count() == 0
