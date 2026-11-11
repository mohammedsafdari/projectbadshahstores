"""View tests for the staff credit dues screens (D79 amendment)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from apps.accounts.capabilities import Capability
from apps.accounts.models import RoleCapability, User
from apps.credit.models import CreditEntry
from apps.credit.services import record_payment
from apps.credit.tests.factories import CreditAccountFactory
from apps.orders.tests.staff_helpers import staff
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _payment_data(**overrides: str) -> dict[str, str]:
    """The fields the payment form and its confirm step need."""
    data = {
        "amount": "200.00",
        "mode": CreditEntry.PaymentMode.CASH,
        "entry_date": TODAY.isoformat(),
        "reference": "",
        "note": "",
    }
    data.update(overrides)
    return data


def test_dues_list_needs_view_credit_dues() -> None:
    CreditAccountFactory(balance=Decimal("100.00"))

    assert _client(staff(User.ROLE_PACKER)).get(reverse("staff_credit_dues")).status_code == 403
    assert _client(staff(User.ROLE_MANAGER)).get(reverse("staff_credit_dues")).status_code == 200


def test_dues_list_sorted_by_largest_balance() -> None:
    CreditAccountFactory(
        user=UserFactory(full_name="Small Balance"),
        balance=Decimal("100.00"),
    )
    CreditAccountFactory(
        user=UserFactory(full_name="Large Balance"),
        balance=Decimal("900.00"),
    )

    html = _client(staff(User.ROLE_MANAGER)).get(reverse("staff_credit_dues")).content.decode()

    assert html.index("Large Balance") < html.index("Small Balance")


def test_dues_list_shows_limit_and_last_payment() -> None:
    user = UserFactory(full_name="Asha Sharma")
    CreditAccountFactory(
        user=user,
        credit_limit=Decimal("1000.00"),
        balance=Decimal("500.00"),
    )
    record_payment(
        user,
        Decimal("50.00"),
        mode=CreditEntry.PaymentMode.CASH,
        entry_date=TODAY,
        actor=staff(),
    )

    html = _client(staff(User.ROLE_MANAGER)).get(reverse("staff_credit_dues")).content.decode()

    assert "1000.00" in html
    assert date_format(TODAY, "j M Y") in html


def test_record_payment_confirm_step_shows_customer_and_amount() -> None:
    user = UserFactory(full_name="Asha Sharma")
    account = CreditAccountFactory(user=user, balance=Decimal("500.00"))
    client = _client(staff(User.ROLE_MANAGER))
    url = reverse("staff_record_credit_payment", args=[account.pk])

    assert client.get(url).status_code == 200

    response = client.post(url, _payment_data(amount="200.00"))
    html = response.content.decode()

    assert response.status_code == 200
    assert "Asha Sharma" in html
    assert "200.00" in html
    assert "Check before saving" in html
    assert CreditEntry.objects.filter(account=account).count() == 0


def test_record_payment_saves_only_after_confirm() -> None:
    user = UserFactory()
    account = CreditAccountFactory(user=user, balance=Decimal("500.00"))
    client = _client(staff(User.ROLE_MANAGER))
    url = reverse("staff_record_credit_payment", args=[account.pk])

    client.post(url, _payment_data(amount="200.00"))
    assert CreditEntry.objects.filter(account=account).count() == 0

    response = client.post(url, _payment_data(amount="200.00", confirm="1"))

    assert response.status_code == 302
    entry = CreditEntry.objects.get(account=account)
    assert entry.entry_type == CreditEntry.EntryType.PAYMENT
    assert entry.amount == Decimal("-200.00")
    account.refresh_from_db()
    assert account.balance == Decimal("300.00")


def test_record_payment_needs_its_own_capability() -> None:
    member = staff(User.ROLE_MEMBER)
    RoleCapability.objects.create(
        role=User.ROLE_MEMBER,
        capability=Capability.VIEW_CREDIT_DUES,
        allowed=True,
    )
    account = CreditAccountFactory(balance=Decimal("500.00"))
    client = _client(member)

    assert client.get(reverse("staff_credit_dues")).status_code == 200
    assert client.get(reverse("staff_record_credit_payment", args=[account.pk])).status_code == 403


def test_record_payment_rejects_zero_and_negative() -> None:
    account = CreditAccountFactory(balance=Decimal("500.00"))
    client = _client(staff(User.ROLE_MANAGER))
    url = reverse("staff_record_credit_payment", args=[account.pk])

    for amount in ("0.00", "-5.00"):
        response = client.post(url, _payment_data(amount=amount))

        assert response.status_code == 200
        assert "above zero" in response.content.decode()

    assert CreditEntry.objects.filter(account=account).count() == 0


@override_settings(ORDER_SMS_ENABLED=True)
def test_reminder_button_sends_through_the_notification_service() -> None:
    user = UserFactory(phone="+919000000010")
    account = CreditAccountFactory(user=user, balance=Decimal("500.00"))
    client = _client(staff(User.ROLE_MANAGER))

    with patch("apps.notifications.order_notices.get_sms_backend") as backend:
        response = client.post(
            reverse("staff_send_credit_reminder", args=[account.pk]),
            follow=True,
        )

    assert response.status_code == 200
    backend.return_value.send_message.assert_called_once()
    phone, text = backend.return_value.send_message.call_args.args
    assert phone == user.phone
    assert "500.00" in text
    assert "pending" in text


def test_reminder_needs_view_credit_dues() -> None:
    account = CreditAccountFactory(balance=Decimal("500.00"))

    response = _client(staff(User.ROLE_PACKER)).post(
        reverse("staff_send_credit_reminder", args=[account.pk]),
    )

    assert response.status_code == 403


def test_reminder_to_customer_without_balance_is_refused() -> None:
    account = CreditAccountFactory(balance=Decimal("0.00"))
    client = _client(staff(User.ROLE_MANAGER))

    with patch("apps.credit.staff_views.notify_credit_reminder") as notify:
        response = client.post(
            reverse("staff_send_credit_reminder", args=[account.pk]),
            follow=True,
        )

    assert response.status_code == 200
    assert "no pending balance" in response.content.decode().lower()
    notify.assert_not_called()
