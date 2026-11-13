"""Tests for the banner admin screens (D84).

REVIEW CAREFULLY: the staff rights and the audit trail.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.promotions.admin import BANNER_CREATE_ACTION, BANNER_UPDATE_ACTION, BannerAdmin
from apps.promotions.models import BANNER_LINK_MESSAGE, Banner
from apps.promotions.tests.helpers import make_banner, png_bytes
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:promotions_banner_changelist"
ADD = "admin:promotions_banner_add"
_request_factory = RequestFactory()


@pytest.fixture(autouse=True)
def _media_root_on_tmp_path(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path


def _staff(role: str) -> User:
    return UserFactory(is_staff=True, role=role)


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _admin_for(user: User):
    """Bind the banner admin to the real site with request.user set."""
    request = _request_factory.get("/admin/")
    request.user = user
    return BannerAdmin(Banner, admin.site), request


def _banner_data(**overrides: Any) -> dict[str, Any]:
    """The add form for an active banner linking to the offers page."""
    data: dict[str, Any] = {
        "title": "Diwali offer",
        "image": SimpleUploadedFile("banner.png", png_bytes(), content_type="image/png"),
        "alt_text": "Diwali offer banner",
        "link_url": "/offers",
        "sort_order": "0",
        "starts_at_0": "",
        "starts_at_1": "",
        "ends_at_0": "",
        "ends_at_1": "",
        "is_active": "on",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Who may see and edit
# ---------------------------------------------------------------------------
def test_the_owner_sees_the_banner_list() -> None:
    make_banner()

    response = _client(_staff(User.ROLE_OWNER)).get(reverse(CHANGELIST))

    assert response.status_code == 200


def test_the_manager_sees_the_banner_list() -> None:
    make_banner()

    response = _client(_staff(User.ROLE_MANAGER)).get(reverse(CHANGELIST))

    assert response.status_code == 200


@pytest.mark.parametrize("role", [User.ROLE_PACKER, User.ROLE_DELIVERY, User.ROLE_MEMBER])
def test_other_roles_are_refused_the_banner_list(role: str) -> None:
    response = _client(_staff(role)).get(reverse(CHANGELIST))

    assert response.status_code == 403


def test_a_member_without_the_capability_has_no_banner_access() -> None:
    banner_admin, request = _admin_for(_staff(User.ROLE_MEMBER))

    assert banner_admin.has_module_permission(request) is False
    assert banner_admin.has_view_permission(request) is False
    assert banner_admin.has_add_permission(request) is False
    assert banner_admin.has_change_permission(request) is False
    assert banner_admin.has_delete_permission(request) is False


def test_nobody_can_delete_a_banner() -> None:
    banner = make_banner()
    client = _client(_staff(User.ROLE_OWNER))

    response = client.get(reverse("admin:promotions_banner_delete", args=[banner.pk]))

    assert response.status_code == 403
    assert Banner.objects.filter(pk=banner.pk).exists()


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------
def test_adding_a_banner_writes_one_create_row() -> None:
    user = _staff(User.ROLE_MANAGER)

    response = _client(user).post(reverse(ADD), _banner_data())

    assert response.status_code == 302
    banner = Banner.objects.get()
    row = AuditLog.objects.get(action=BANNER_CREATE_ACTION)
    # The action name is part of the audit trail, so it is checked literally.
    assert row.action == "BANNER_CREATE"
    assert row.target_type == "promotions.banner"
    assert row.target_id == str(banner.pk)
    assert row.actor_id == user.pk
    assert row.changes["title"] == [None, "Diwali offer"]
    assert row.changes["link_url"] == [None, "/offers"]


def test_changing_a_banner_writes_one_update_row_with_the_changed_field() -> None:
    banner = make_banner(title="Old title")
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(
        reverse("admin:promotions_banner_change", args=[banner.pk]),
        _banner_data(title="New title", alt_text=banner.alt_text, link_url=""),
    )

    assert response.status_code == 302
    row = AuditLog.objects.get(action=BANNER_UPDATE_ACTION)
    assert row.action == "BANNER_UPDATE"
    assert row.changes == {"title": ["Old title", "New title"]}


def test_changing_the_active_switch_is_audited() -> None:
    banner = make_banner(is_active=True)
    client = _client(_staff(User.ROLE_OWNER))
    data = _banner_data(title=banner.title, alt_text=banner.alt_text, link_url="")
    data.pop("is_active")

    response = client.post(
        reverse("admin:promotions_banner_change", args=[banner.pk]),
        data,
    )

    assert response.status_code == 302
    banner.refresh_from_db()
    assert banner.is_active is False
    row = AuditLog.objects.get(action=BANNER_UPDATE_ACTION)
    assert row.changes == {"is_active": [True, False]}


def test_an_unchanged_save_writes_no_row() -> None:
    banner = make_banner()
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(
        reverse("admin:promotions_banner_change", args=[banner.pk]),
        _banner_data(title=banner.title, alt_text=banner.alt_text, link_url=""),
    )

    assert response.status_code == 302
    assert AuditLog.objects.filter(action=BANNER_UPDATE_ACTION).count() == 0


# ---------------------------------------------------------------------------
# The form and the list
# ---------------------------------------------------------------------------
def test_a_bad_link_shows_its_sentence_in_the_form() -> None:
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(reverse(ADD), _banner_data(link_url="javascript:alert(1)"))
    html = response.content.decode()

    assert response.status_code == 200
    assert str(BANNER_LINK_MESSAGE) in html
    assert Banner.objects.count() == 0


def test_the_preview_escapes_a_hostile_alt_text() -> None:
    make_banner(alt_text='"><script>alert(1)</script>')
    client = _client(_staff(User.ROLE_OWNER))

    response = client.get(reverse(CHANGELIST))
    html = response.content.decode()

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_the_preview_uses_the_display_copy() -> None:
    banner = make_banner()
    client = _client(_staff(User.ROLE_OWNER))

    response = client.get(reverse(CHANGELIST))
    html = response.content.decode()

    assert banner.display.url in html
    assert banner.display.name.endswith(".webp")
