"""VIEW_CREDIT_DUES and RECORD_CREDIT_PAYMENT are the only gates for the credit
admin (D75 amendment).

Every staff user here has no Django model permission.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.capabilities import Capability
from apps.accounts.models import RoleCapability, User
from apps.credit.models import CreditEntry
from apps.credit.tests.factories import CreditAccountFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

ACCOUNTS = "admin:credit_creditaccount_changelist"
ENTRIES = "admin:credit_creditentry_changelist"
ENTRY_ADD = "admin:credit_creditentry_add"


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _staff(role: str) -> User:
    return UserFactory(is_staff=True, role=role)


def _grant(role: str, capability: str, allowed: bool = True) -> None:
    RoleCapability.objects.create(role=role, capability=capability, allowed=allowed)


def _payment_data(account) -> dict[str, str]:
    return {
        "account": str(account.pk),
        "entry_type": CreditEntry.EntryType.PAYMENT,
        "amount": "200.00",
        "payment_mode": CreditEntry.PaymentMode.CASH,
        "entry_date": timezone.localdate().isoformat(),
        "reference": "",
        "note": "",
    }


def test_a_manager_can_read_the_credit_ledger_without_any_django_permission() -> None:
    CreditAccountFactory()
    user = _staff(User.ROLE_MANAGER)
    assert not user.user_permissions.exists()
    client = _client(user)

    assert client.get(reverse(ACCOUNTS)).status_code == 200
    assert client.get(reverse(ENTRIES)).status_code == 200


def test_a_packer_cannot_read_the_credit_ledger() -> None:
    CreditAccountFactory()

    response = _client(_staff(User.ROLE_PACKER)).get(reverse(ACCOUNTS))

    assert response.status_code == 403


def test_a_delivery_user_cannot_read_the_credit_ledger() -> None:
    CreditAccountFactory()

    response = _client(_staff(User.ROLE_DELIVERY)).get(reverse(ACCOUNTS))

    assert response.status_code == 403


def test_the_record_payment_page_needs_record_credit_payment() -> None:
    _grant(User.ROLE_MANAGER, Capability.RECORD_CREDIT_PAYMENT, allowed=False)
    client = _client(_staff(User.ROLE_MANAGER))

    assert client.get(reverse(ENTRY_ADD)).status_code == 403


def test_view_credit_dues_alone_cannot_open_the_add_page() -> None:
    _grant(User.ROLE_PACKER, Capability.VIEW_CREDIT_DUES, allowed=True)
    client = _client(_staff(User.ROLE_PACKER))

    assert client.get(reverse("admin:credit_creditaccount_add")).status_code == 403


def test_view_credit_dues_alone_cannot_change_a_limit() -> None:
    account = CreditAccountFactory(credit_limit=Decimal("1000.00"))
    _grant(User.ROLE_PACKER, Capability.VIEW_CREDIT_DUES, allowed=True)
    client = _client(_staff(User.ROLE_PACKER))

    response = client.post(
        reverse("admin:credit_creditaccount_change", args=[account.pk]),
        {
            "user": str(account.user_id),
            "credit_limit": "9000.00",
            "is_active": "on",
        },
    )

    assert response.status_code == 403
    account.refresh_from_db()
    assert account.credit_limit == Decimal("1000.00")


def test_a_packer_with_record_credit_payment_can_add_a_payment() -> None:
    account = CreditAccountFactory(balance=Decimal("500.00"))
    _grant(User.ROLE_PACKER, Capability.RECORD_CREDIT_PAYMENT, allowed=True)
    client = _client(_staff(User.ROLE_PACKER))

    response = client.post(reverse(ENTRY_ADD), _payment_data(account))

    assert response.status_code == 302
    entry = CreditEntry.objects.get(account=account)
    assert entry.entry_type == CreditEntry.EntryType.PAYMENT
    assert entry.amount == Decimal("-200.00")
    account.refresh_from_db()
    assert account.balance == Decimal("300.00")


def test_a_manager_is_refused_once_record_credit_payment_is_removed() -> None:
    account = CreditAccountFactory(balance=Decimal("500.00"))
    _grant(User.ROLE_MANAGER, Capability.RECORD_CREDIT_PAYMENT, allowed=False)
    client = _client(_staff(User.ROLE_MANAGER))

    response = client.post(reverse(ENTRY_ADD), _payment_data(account))

    assert response.status_code == 403
    assert CreditEntry.objects.count() == 0


def test_a_refused_payment_post_writes_no_entry() -> None:
    account = CreditAccountFactory(balance=Decimal("500.00"))
    client = _client(_staff(User.ROLE_PACKER))

    response = client.post(reverse(ENTRY_ADD), _payment_data(account))

    assert response.status_code == 403
    assert CreditEntry.objects.count() == 0
    account.refresh_from_db()
    assert account.balance == Decimal("500.00")


def test_the_credit_menu_entries_follow_the_capability() -> None:
    manager_html = _client(_staff(User.ROLE_MANAGER)).get(reverse("admin:index")).content.decode()
    packer_html = _client(_staff(User.ROLE_PACKER)).get(reverse("admin:index")).content.decode()

    assert reverse(ACCOUNTS) in manager_html
    assert reverse(ENTRIES) in manager_html
    assert reverse(ACCOUNTS) not in packer_html
    assert reverse(ENTRIES) not in packer_html


def test_a_superuser_still_passes() -> None:
    account = CreditAccountFactory()
    client = _client(UserFactory(is_staff=True, is_superuser=True, role=User.ROLE_MEMBER))

    assert client.get(reverse(ACCOUNTS)).status_code == 200
    assert client.get(reverse(ENTRIES)).status_code == 200
    assert client.get(reverse(ENTRY_ADD)).status_code == 200
    assert (
        client.get(reverse("admin:credit_creditaccount_change", args=[account.pk])).status_code
        == 200
    )
