"""More tests for the capability defaults, labels and stored rows (D75)."""

from __future__ import annotations

import pytest

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import RoleCapability, User
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

EXPECTED_CAPABILITIES = {
    "VIEW_ORDERS",
    "START_PACKING",
    "SEND_OUT",
    "DELIVER_COD",
    "STAFF_CANCEL",
    "RECORD_CREDIT_PAYMENT",
    "VIEW_CREDIT_DUES",
    "STOCK_WRITE",
    "AUDIT_READ",
    "EXPORT_CSV",
    "VIEW_PAYMENTS",
    "REFUND_PAYMENTS",
    "VIEW_INVOICES",
    "MANAGE_PROMOTIONS",
}


def _staff(role: str = User.ROLE_MEMBER) -> User:
    return UserFactory(is_staff=True, role=role)


def test_every_capability_has_a_label() -> None:
    for value in Capability.values:
        label = Capability(value).label
        assert label.strip()
        assert label != value


def test_the_capability_keys_are_exactly_the_fourteen_agreed() -> None:
    assert set(Capability.values) == EXPECTED_CAPABILITIES
    assert len(Capability.values) == 14


def test_removing_a_manager_capability_takes_effect_at_once() -> None:
    user = _staff(User.ROLE_MANAGER)
    assert has_capability(user, Capability.STOCK_WRITE) is True

    RoleCapability.objects.create(
        role=user.role,
        capability=Capability.STOCK_WRITE,
        allowed=False,
    )

    # No cache is held on the user: the next call sees the stored row.
    assert has_capability(user, Capability.STOCK_WRITE) is False


def test_a_packer_loses_send_out_when_the_row_says_no() -> None:
    user = _staff(User.ROLE_PACKER)
    RoleCapability.objects.create(
        role=user.role,
        capability=Capability.SEND_OUT,
        allowed=False,
    )

    assert has_capability(user, Capability.SEND_OUT) is False
    # The other defaults of the role are untouched.
    assert has_capability(user, Capability.VIEW_ORDERS) is True
    assert has_capability(user, Capability.START_PACKING) is True


def test_a_stored_row_for_another_role_does_not_change_this_role() -> None:
    RoleCapability.objects.create(
        role=User.ROLE_MANAGER,
        capability=Capability.STOCK_WRITE,
        allowed=False,
    )
    packer = _staff(User.ROLE_PACKER)

    assert has_capability(packer, Capability.SEND_OUT) is True
    assert has_capability(packer, Capability.STOCK_WRITE) is False


def test_a_row_equal_to_the_default_changes_nothing() -> None:
    user = _staff(User.ROLE_MANAGER)
    RoleCapability.objects.create(
        role=user.role,
        capability=Capability.STOCK_WRITE,
        allowed=True,
    )

    # A row that repeats the default is harmless; the rule stays the same.
    assert has_capability(user, Capability.STOCK_WRITE) is True
    assert has_capability(user, Capability.AUDIT_READ) is True


def test_a_delivery_user_can_be_granted_view_orders_by_a_row() -> None:
    user = _staff(User.ROLE_DELIVERY)
    assert has_capability(user, Capability.VIEW_ORDERS) is False

    RoleCapability.objects.create(
        role=user.role,
        capability=Capability.VIEW_ORDERS,
        allowed=True,
    )

    assert has_capability(user, Capability.VIEW_ORDERS) is True
    assert has_capability(user, Capability.DELIVER_COD) is True


def test_a_member_can_be_granted_a_capability_by_a_row() -> None:
    user = _staff()
    assert has_capability(user, Capability.EXPORT_CSV) is False

    RoleCapability.objects.create(
        role=user.role,
        capability=Capability.EXPORT_CSV,
        allowed=True,
    )

    assert has_capability(user, Capability.EXPORT_CSV) is True
