"""Tests for the credit admin screens (Slice 37a, D67 point 13)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.credit.models import CreditEntry
from apps.credit.services import CREDIT_LIMIT_ACTION, record_adjustment
from apps.credit.tests.factories import CreditAccountFactory
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _superuser(phone: str = "+919876543210") -> User:
    return User.objects.create_superuser(phone=phone, password="ok")


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _account_url(account) -> str:
    return reverse("admin:credit_creditaccount_change", args=[account.pk])


def _payment_data(account, **overrides) -> dict[str, str]:
    """The entry add form, filled in for a cash payment of 200 today."""
    data = {
        "account": str(account.pk),
        "entry_type": CreditEntry.EntryType.PAYMENT,
        "amount": "200.00",
        "payment_mode": CreditEntry.PaymentMode.CASH,
        "entry_date": timezone.localdate().isoformat(),
        "reference": "",
        "note": "",
    }
    data.update(overrides)
    return data


def test_account_admin_changelist_lists_balance_and_available_credit() -> None:
    user = UserFactory(full_name="Asha Sharma")
    CreditAccountFactory(
        user=user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("250.00"),
    )
    client = _client(_superuser())

    response = client.get(reverse("admin:credit_creditaccount_changelist"))
    html = response.content.decode()

    assert response.status_code == 200
    assert user.phone in html
    assert "250.00" in html
    assert "750.00" in html


def test_account_admin_balance_is_read_only() -> None:
    account = CreditAccountFactory(balance=Decimal("250.00"))
    client = _client(_superuser())

    response = client.get(_account_url(account))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'name="balance"' not in html
    assert "250.00" in html


def test_account_admin_cannot_delete() -> None:
    account = CreditAccountFactory()
    client = _client(_superuser())

    response = client.get(reverse("admin:credit_creditaccount_delete", args=[account.pk]))

    assert response.status_code == 403


def test_account_admin_saving_a_limit_goes_through_the_service_and_audits() -> None:
    user = UserFactory()
    account = CreditAccountFactory(user=user, credit_limit=Decimal("1000.00"))
    actor = _superuser()
    client = _client(actor)

    response = client.post(
        _account_url(account),
        {
            "user": str(user.pk),
            "credit_limit": "1500.00",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    account.refresh_from_db()
    assert account.credit_limit == Decimal("1500.00")
    audit = AuditLog.objects.get(action=CREDIT_LIMIT_ACTION)
    assert audit.actor_id == actor.pk
    assert audit.changes == {"credit_limit": ["1000.00", "1500.00"]}


def test_account_admin_requires_staff() -> None:
    # A customer cannot even reach the admin; Django sends them to the login.
    non_staff = UserFactory(is_staff=False, role=User.ROLE_MEMBER)
    response = _client(non_staff).get(reverse("admin:credit_creditaccount_changelist"))
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]

    # Staff status alone is not enough: the role must be OWNER or MANAGER.
    staff = UserFactory(is_staff=True, role=User.ROLE_PACKER)
    response = _client(staff).get(reverse("admin:credit_creditaccount_changelist"))
    assert response.status_code == 403


def test_entry_admin_is_read_only_for_existing_rows() -> None:
    user = UserFactory()
    CreditAccountFactory(user=user)
    entry = record_adjustment(
        user,
        Decimal("50.00"),
        note="Rounding correction",
        actor=None,
    )
    client = _client(_superuser())

    response = client.get(reverse("admin:credit_creditentry_change", args=[entry.pk]))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'name="amount"' not in html
    assert 'name="entry_type"' not in html
    assert "50.00" in html


def test_entry_admin_cannot_delete() -> None:
    user = UserFactory()
    CreditAccountFactory(user=user)
    entry = record_adjustment(
        user,
        Decimal("50.00"),
        note="Rounding correction",
        actor=None,
    )
    client = _client(_superuser())

    response = client.get(reverse("admin:credit_creditentry_delete", args=[entry.pk]))

    assert response.status_code == 403


def test_entry_admin_add_records_a_payment_through_the_service() -> None:
    user = UserFactory()
    account = CreditAccountFactory(user=user, balance=Decimal("500.00"))
    actor = _superuser()
    client = _client(actor)

    response = client.post(
        reverse("admin:credit_creditentry_add"),
        _payment_data(account, reference="UPI-123456", note="Paid by UPI"),
    )

    assert response.status_code == 302
    entry = CreditEntry.objects.get(account=account)
    assert entry.entry_type == CreditEntry.EntryType.PAYMENT
    assert entry.amount == Decimal("-200.00")
    assert entry.payment_mode == CreditEntry.PaymentMode.CASH
    assert entry.reference == "UPI-123456"
    assert entry.created_by_id == actor.pk
    account.refresh_from_db()
    assert account.balance == Decimal("300.00")


def test_entry_admin_add_records_an_adjustment_through_the_service() -> None:
    user = UserFactory()
    account = CreditAccountFactory(user=user)
    client = _client(_superuser())

    response = client.post(
        reverse("admin:credit_creditentry_add"),
        {
            "account": str(account.pk),
            "entry_type": CreditEntry.EntryType.ADJUSTMENT,
            "amount": "50.00",
            "payment_mode": "",
            "entry_date": timezone.localdate().isoformat(),
            "reference": "",
            "note": "Rounding correction",
        },
    )

    assert response.status_code == 302
    entry = CreditEntry.objects.get(account=account)
    assert entry.entry_type == CreditEntry.EntryType.ADJUSTMENT
    assert entry.amount == Decimal("50.00")
    assert entry.note == "Rounding correction"
    account.refresh_from_db()
    assert account.balance == Decimal("50.00")


def test_entry_admin_add_refuses_an_overpayment_with_a_form_error() -> None:
    user = UserFactory()
    account = CreditAccountFactory(user=user, balance=Decimal("100.00"))
    client = _client(_superuser())

    response = client.post(
        reverse("admin:credit_creditentry_add"),
        _payment_data(account, amount="150.00"),
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "The payment is more than the outstanding balance" in html
    assert CreditEntry.objects.count() == 0
    account.refresh_from_db()
    assert account.balance == Decimal("100.00")


def test_entry_admin_add_offers_only_payment_and_adjustment() -> None:
    client = _client(_superuser())

    response = client.get(reverse("admin:credit_creditentry_add"))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'value="PAYMENT"' in html
    assert 'value="ADJUSTMENT"' in html
    assert 'value="ORDER"' not in html
    assert 'value="CANCEL_REVERSAL"' not in html


def test_entry_admin_add_is_refused_for_a_user_without_the_right_role() -> None:
    packer = UserFactory(is_staff=True, role=User.ROLE_PACKER)
    # Even with the Django permission, the role check must refuse the page.
    packer.user_permissions.add(
        Permission.objects.get(
            codename="add_creditentry",
            content_type__app_label="credit",
        )
    )
    client = _client(packer)

    response = client.get(reverse("admin:credit_creditentry_add"))

    assert response.status_code == 403
