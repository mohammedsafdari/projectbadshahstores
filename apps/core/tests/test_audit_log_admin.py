"""Tests for the read-only AuditLog admin."""

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.core.admin import AuditLogAdmin, format_changes
from apps.core.models import AuditLog
from apps.core.tests.factories import AuditLogFactory

pytestmark = pytest.mark.django_db


def test_format_changes_one_field() -> None:
    changes = {"selling_price": ["10.00", "12.00"]}
    assert format_changes(changes) == "selling_price: 10.00 -> 12.00"


def test_format_changes_two_fields_joined_in_stored_order() -> None:
    changes = {"selling_price": ["10.00", "12.00"], "mrp": ["15.00", "16.00"]}
    assert format_changes(changes) == "selling_price: 10.00 -> 12.00; mrp: 15.00 -> 16.00"


def test_format_changes_empty_dict_gives_dash() -> None:
    assert format_changes({}) == "-"


def test_format_changes_long_result_cut_to_120_with_ellipsis() -> None:
    changes: dict[str, list[str]] = {}
    value = "x" * 50
    for i in range(5):
        changes[f"field_{i}"] = [value, value]
    text = format_changes(changes)
    assert len(text) == 120
    assert text.endswith("...")


def test_format_changes_exact_120_not_cut() -> None:
    changes: dict[str, list[str]] = {"f": ["a" * 56, "b" * 57]}
    text = format_changes(changes)
    assert len(text) == 120
    assert not text.endswith("...")


def _admin() -> AuditLogAdmin:
    return AuditLogAdmin(AuditLog, admin.site)


def _superuser_client() -> Client:
    user = User.objects.create_superuser(phone="+919876543210", password="ok")
    client = Client()
    client.force_login(user)
    return client


def _request_with_user(user: User) -> object:
    request = RequestFactory().get("/admin/core/auditlog/")
    request.user = user
    return request


def test_has_add_permission_false_for_superuser() -> None:
    admin_instance = _admin()
    user = User.objects.create_superuser(phone="+919876543211", password="ok")
    request = _request_with_user(user)
    assert admin_instance.has_add_permission(request) is False


def test_has_change_permission_false_for_superuser() -> None:
    admin_instance = _admin()
    user = User.objects.create_superuser(phone="+919876543212", password="ok")
    request = _request_with_user(user)
    assert admin_instance.has_change_permission(request) is False


def test_has_delete_permission_false_for_superuser() -> None:
    admin_instance = _admin()
    user = User.objects.create_superuser(phone="+919876543213", password="ok")
    request = _request_with_user(user)
    assert admin_instance.has_delete_permission(request) is False


def test_get_readonly_fields_contains_every_field_name() -> None:
    admin_instance = _admin()
    user = User.objects.create_superuser(phone="+919876543215", password="ok")
    request = _request_with_user(user)
    readonly = admin_instance.get_readonly_fields(request)
    expected = {f.name for f in AuditLog._meta.fields}
    assert set(readonly) == expected


def test_list_page_200_for_superuser_contains_action_text() -> None:
    entry = AuditLogFactory(action="PRICE_CHANGE")
    client = _superuser_client()
    response = client.get(reverse("admin:core_auditlog_changelist"))
    assert response.status_code == 200
    content = response.content.decode("utf-8", "replace")
    assert entry.action in content


def test_list_page_shows_changes_summary_text() -> None:
    AuditLogFactory(changes={"selling_price": ["10.00", "12.00"]})
    client = _superuser_client()
    response = client.get(reverse("admin:core_auditlog_changelist"))
    assert response.status_code == 200
    content = response.content.decode("utf-8", "replace")
    # The admin escapes HTML, so "->" appears as "-&gt;" in the page.
    assert "selling_price: 10.00 -&gt; 12.00" in content


def test_detail_page_200_for_superuser() -> None:
    entry = AuditLogFactory()
    client = _superuser_client()
    response = client.get(reverse("admin:core_auditlog_change", args=[entry.pk]))
    assert response.status_code == 200


def test_add_page_403_for_superuser() -> None:
    client = _superuser_client()
    response = client.get(reverse("admin:core_auditlog_add"))
    assert response.status_code == 403


def test_post_to_detail_page_403_and_entry_unchanged() -> None:
    entry = AuditLogFactory(note="original note")
    original_note = entry.note
    client = _superuser_client()
    response = client.post(
        reverse("admin:core_auditlog_change", args=[entry.pk]),
        data={"note": "tampered note"},
    )
    assert response.status_code == 403
    entry.refresh_from_db()
    assert entry.note == original_note


def test_delete_page_403_for_superuser() -> None:
    entry = AuditLogFactory()
    client = _superuser_client()
    response = client.get(reverse("admin:core_auditlog_delete", args=[entry.pk]))
    assert response.status_code == 403


def test_staff_without_permissions_gets_403_on_list_page() -> None:
    user = User.objects.create_user(phone="+919876543220", password="ok", is_staff=True)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("admin:core_auditlog_changelist"))
    assert response.status_code == 403


def test_staff_with_view_permission_can_read_but_not_add() -> None:
    # ``user_permissions`` is the set of extra permissions granted directly to
    # a user, separate from group permissions. A staff user without any
    # permission cannot read the audit log; giving them view_auditlog lets them
    # read it but still not add it (has_add_permission returns False).
    user = User.objects.create_user(phone="+919876543230", password="ok", is_staff=True)
    view_perm = Permission.objects.get(codename="view_auditlog")
    user.user_permissions.add(view_perm)
    client = Client()
    client.force_login(user)
    list_response = client.get(reverse("admin:core_auditlog_changelist"))
    add_response = client.get(reverse("admin:core_auditlog_add"))
    assert list_response.status_code == 200
    assert add_response.status_code == 403
