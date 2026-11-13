"""Tests for the coupon admin screens (D81).

REVIEW CAREFULLY: money and the staff rights.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.promotions.admin import COUPON_CREATE_ACTION, COUPON_UPDATE_ACTION, CouponAdmin
from apps.promotions.models import Coupon, CouponRedemption
from apps.promotions.tests.helpers import make_coupon, make_redemption
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:promotions_coupon_changelist"
ADD = "admin:promotions_coupon_add"
_request_factory = RequestFactory()


def _staff(role: str) -> User:
    return UserFactory(is_staff=True, role=role)


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _admin_for(user: User):
    """Bind the coupon admin to the real site with request.user set."""
    request = _request_factory.get("/admin/")
    request.user = user
    return CouponAdmin(Coupon, admin.site), request


def _coupon_data(**overrides: Any) -> dict[str, Any]:
    """The add form for a 10% coupon, with empty optional fields."""
    data: dict[str, Any] = {
        "code": "DIWALI10",
        "description": "Festival offer",
        "kind": Coupon.Kind.PERCENT,
        "percent_off": "10.00",
        "max_discount": "",
        "amount_off": "",
        "min_order_value": "0.00",
        "total_limit": "",
        "per_customer_limit": "1",
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
def test_the_owner_sees_the_coupon_list() -> None:
    make_coupon()

    response = _client(_staff(User.ROLE_OWNER)).get(reverse(CHANGELIST))

    assert response.status_code == 200


def test_the_manager_sees_the_coupon_list() -> None:
    make_coupon()

    response = _client(_staff(User.ROLE_MANAGER)).get(reverse(CHANGELIST))

    assert response.status_code == 200


@pytest.mark.parametrize("role", [User.ROLE_PACKER, User.ROLE_DELIVERY, User.ROLE_MEMBER])
def test_other_roles_are_refused_the_coupon_list(role: str) -> None:
    response = _client(_staff(role)).get(reverse(CHANGELIST))

    assert response.status_code == 403


def test_a_member_without_the_capability_has_no_module_access() -> None:
    coupon_admin, request = _admin_for(_staff(User.ROLE_MEMBER))

    assert coupon_admin.has_module_permission(request) is False
    assert coupon_admin.has_view_permission(request) is False
    assert coupon_admin.has_add_permission(request) is False
    assert coupon_admin.has_change_permission(request) is False


def test_nobody_can_delete_a_coupon() -> None:
    coupon = make_coupon()
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(
        reverse("admin:promotions_coupon_delete", args=[coupon.pk]),
        {"post": "yes"},
    )

    assert response.status_code == 403
    assert Coupon.objects.filter(pk=coupon.pk).exists()


# ---------------------------------------------------------------------------
# Creating and changing
# ---------------------------------------------------------------------------
def test_the_owner_can_add_a_coupon() -> None:
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(reverse(ADD), _coupon_data())

    assert response.status_code == 302
    coupon = Coupon.objects.get(code="DIWALI10")
    assert coupon.percent_off == Decimal("10.00")


def test_the_admin_stores_a_lower_case_code_in_capitals() -> None:
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(reverse(ADD), _coupon_data(code="diwali10"))

    assert response.status_code == 302
    assert Coupon.objects.get(code="DIWALI10").code == "DIWALI10"


def test_creating_a_coupon_writes_one_create_row() -> None:
    client = _client(_staff(User.ROLE_OWNER))

    client.post(reverse(ADD), _coupon_data())

    audit = AuditLog.objects.get(action=COUPON_CREATE_ACTION)
    assert audit.changes["code"] == [None, "DIWALI10"]
    assert AuditLog.objects.count() == 1


def test_changing_a_coupon_writes_one_update_row_with_the_old_and_new_values() -> None:
    coupon = make_coupon(description="Festival offer")
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(
        reverse("admin:promotions_coupon_change", args=[coupon.pk]),
        _coupon_data(description="Winter offer"),
    )

    assert response.status_code == 302
    audit = AuditLog.objects.get(action=COUPON_UPDATE_ACTION)
    assert audit.changes == {"description": ["Festival offer", "Winter offer"]}


def test_saving_without_a_change_writes_no_row() -> None:
    coupon = make_coupon(description="Festival offer")
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(
        reverse("admin:promotions_coupon_change", args=[coupon.pk]),
        _coupon_data(description="Festival offer"),
    )

    assert response.status_code == 302
    assert not AuditLog.objects.exists()


def test_the_form_refuses_a_percent_coupon_without_a_percent() -> None:
    client = _client(_staff(User.ROLE_OWNER))

    response = client.post(reverse(ADD), _coupon_data(percent_off=""))
    html = response.content.decode()

    assert response.status_code == 200
    assert "A percentage coupon needs a percent off." in html
    assert not Coupon.objects.exists()


# ---------------------------------------------------------------------------
# The list, the live uses and the read-only history
# ---------------------------------------------------------------------------
def test_the_list_page_shows_the_code_and_the_live_uses() -> None:
    coupon = make_coupon()
    make_redemption(coupon)
    make_redemption(coupon)
    make_redemption(coupon, released_at=datetime(2026, 11, 1, tzinfo=UTC))
    coupon_admin, request = _admin_for(_staff(User.ROLE_MANAGER))

    row = coupon_admin.get_queryset(request).get(pk=coupon.pk)

    assert coupon_admin.live_uses(row) == 2


def test_the_search_finds_a_code_and_a_description() -> None:
    coupon = make_coupon(code="DIWALI10", description="Winter offer")
    client = _client(_staff(User.ROLE_MANAGER))

    by_code = client.get(reverse(CHANGELIST), {"q": "DIWALI10"})
    by_description = client.get(reverse(CHANGELIST), {"q": "Winter"})

    assert by_code.status_code == 200
    assert coupon.code in by_code.content.decode()
    assert coupon.code in by_description.content.decode()


def test_deactivating_a_coupon_writes_an_audit_row() -> None:
    coupon = make_coupon()
    client = _client(_staff(User.ROLE_OWNER))
    data = _coupon_data()
    # An unchecked BooleanField is simply absent from the POST data.
    data.pop("is_active")

    response = client.post(
        reverse("admin:promotions_coupon_change", args=[coupon.pk]),
        data,
    )

    assert response.status_code == 302
    audit = AuditLog.objects.get(action=COUPON_UPDATE_ACTION)
    assert audit.changes == {"is_active": [True, False]}


def test_the_search_by_code_finds_its_row() -> None:
    coupon = make_coupon(code="DIWALI10", description="Winter offer")
    client = _client(_staff(User.ROLE_MANAGER))

    html = client.get(reverse(CHANGELIST), {"q": "DIWALI10"}).content.decode()

    # The echoed search box carries the code too, so look for the row's link.
    assert reverse("admin:promotions_coupon_change", args=[coupon.pk]) in html


def test_the_search_by_description_finds_its_row() -> None:
    coupon = make_coupon(code="DIWALI10", description="Winter offer")
    client = _client(_staff(User.ROLE_MANAGER))

    html = client.get(reverse(CHANGELIST), {"q": "Winter"}).content.decode()

    assert reverse("admin:promotions_coupon_change", args=[coupon.pk]) in html


def test_the_redemption_list_is_read_only() -> None:
    coupon = make_coupon()
    make_redemption(coupon)
    client = _client(_staff(User.ROLE_MANAGER))

    response = client.get(reverse("admin:promotions_couponredemption_changelist"))
    redemption = CouponRedemption.objects.get()
    redemption_admin = admin.site._registry[CouponRedemption]
    request = _request_factory.get("/admin/")
    request.user = _staff(User.ROLE_MANAGER)

    assert response.status_code == 200
    assert redemption_admin.has_view_permission(request) is True
    assert redemption_admin.has_add_permission(request) is False
    assert redemption_admin.has_change_permission(request, redemption) is False
    assert redemption_admin.has_delete_permission(request, redemption) is False
    expected = {field.name for field in CouponRedemption._meta.fields}
    assert set(redemption_admin.get_readonly_fields(request, redemption)) == expected
