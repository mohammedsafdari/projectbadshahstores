"""COD collected against pending, and the khata figures (D85)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from apps.credit.models import CreditEntry
from apps.credit.tests.factories import CreditAccountFactory
from apps.orders.models import CodCollection, Order
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.staff_helpers import NOW, staff
from apps.reports.queries import (
    DateRange,
    ReportsPermissionError,
    cod_and_credit_report,
)
from apps.reports.tests.helpers import FIRST_DAY, IST, LAST_DAY
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

NOVEMBER = DateRange(FIRST_DAY, LAST_DAY)


def _order_owing(amount: str) -> Order:
    """A cash order whose frozen money adds up: fee 30.00, the rest the item total."""
    fee = Decimal("30.00")
    return OrderFactory(
        item_total=Decimal(amount) - fee,
        delivery_fee=fee,
        grand_total=Decimal(amount),
        amount_due_on_delivery=Decimal(amount),
    )


def _collection(*, collected_at: datetime, amount: str = "100.00") -> CodCollection:
    order = _order_owing(amount)
    return CodCollection.objects.create(
        order=order,
        amount=Decimal(amount),
        collected_by=UserFactory(is_staff=True),
        collected_at=collected_at,
    )


def test_the_cash_collected_in_the_range_is_summed() -> None:
    _collection(collected_at=datetime(2026, 11, 2, 10, 0, tzinfo=IST), amount="100.00")
    _collection(collected_at=datetime(2026, 11, 3, 10, 0, tzinfo=IST), amount="250.50")

    report = cod_and_credit_report(staff(), NOVEMBER)

    assert report.cod_collected == Decimal("350.50")
    assert report.cod_collected_count == 2


def test_cash_collected_on_another_day_is_left_out() -> None:
    _collection(collected_at=datetime(2026, 10, 31, 10, 0, tzinfo=IST), amount="100.00")

    report = cod_and_credit_report(staff(), NOVEMBER)

    assert report.cod_collected == Decimal("0.00")


def test_an_order_that_still_owes_cash_is_pending() -> None:
    OrderFactory(
        status=Order.Status.OUT_FOR_DELIVERY,
        amount_due_on_delivery=Decimal("230.00"),
    )

    report = cod_and_credit_report(staff(), NOVEMBER)

    assert report.cod_pending == Decimal("230.00")
    assert report.cod_pending_count == 1


def test_a_delivered_order_is_not_pending() -> None:
    OrderFactory(
        status=Order.Status.DELIVERED,
        delivered_at=NOW,
        amount_due_on_delivery=Decimal("230.00"),
    )

    report = cod_and_credit_report(staff(), NOVEMBER)

    assert report.cod_pending == Decimal("0.00")


def test_an_order_whose_cash_was_collected_is_not_pending() -> None:
    _collection(collected_at=datetime(2026, 11, 2, 10, 0, tzinfo=IST), amount="100.00")

    report = cod_and_credit_report(staff(), NOVEMBER)

    assert report.cod_pending == Decimal("0.00")
    assert report.cod_collected == Decimal("100.00")


def test_the_khata_outstanding_is_the_sum_of_the_balances() -> None:
    CreditAccountFactory(balance=Decimal("500.00"))
    CreditAccountFactory(balance=Decimal("250.50"))

    report = cod_and_credit_report(staff(), NOVEMBER)

    assert report.khata_outstanding == Decimal("750.50")
    assert report.khata_account_count == 2


def test_a_khata_payment_in_the_range_is_shown_as_money_received() -> None:
    account = CreditAccountFactory(balance=Decimal("500.00"))
    CreditEntry.objects.create(
        account=account,
        entry_type=CreditEntry.EntryType.PAYMENT,
        amount=Decimal("-200.00"),
        balance_after=Decimal("300.00"),
        payment_mode=CreditEntry.PaymentMode.CASH,
        entry_date=date(2026, 11, 4),
    )

    report = cod_and_credit_report(staff(), NOVEMBER)

    assert report.khata_payments == Decimal("200.00")
    assert report.khata_payment_count == 1


def test_a_khata_payment_outside_the_range_is_left_out() -> None:
    account = CreditAccountFactory(balance=Decimal("500.00"))
    CreditEntry.objects.create(
        account=account,
        entry_type=CreditEntry.EntryType.PAYMENT,
        amount=Decimal("-200.00"),
        balance_after=Decimal("300.00"),
        payment_mode=CreditEntry.PaymentMode.UPI,
        entry_date=date(2026, 10, 20),
    )

    report = cod_and_credit_report(staff(), NOVEMBER)

    assert report.khata_payments == Decimal("0.00")
    assert report.khata_payment_count == 0


def test_a_user_without_the_capability_cannot_read_the_cod_report() -> None:
    with pytest.raises(ReportsPermissionError):
        cod_and_credit_report(staff("PACKER"), NOVEMBER)
