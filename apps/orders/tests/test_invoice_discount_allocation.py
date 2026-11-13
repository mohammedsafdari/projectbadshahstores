"""Tests for the largest-remainder discount split of an invoice (D82)."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from apps.orders.invoices import _allocate_discount, find_invoice_problems
from apps.orders.models import Invoice, Order
from apps.orders.staff_services import pack_and_send_out
from apps.orders.tests.invoice_helpers import gst_variant, packing_order
from apps.orders.tests.staff_helpers import NOW, staff

pytestmark = pytest.mark.django_db

ZERO = Decimal("0.00")
PAISA = Decimal("0.01")


def _send_out_discounted(prices: list[str], *, discount: str) -> tuple[Order, Invoice]:
    """Pack and send out one order whose lines cost ``prices``, with a coupon."""
    lines = [(gst_variant(price=price, gst="5.00"), "1") for price in prices]
    order, _variants = packing_order(lines=lines, delivery_fee="0.00")
    item_discount = Decimal(discount)
    total = order.item_total - item_discount
    # The order factory builds the undiscounted total, so the coupon money is
    # written in one go, with a total that satisfies the order's equation.
    Order.objects.filter(pk=order.pk).update(
        discount_total=item_discount,
        coupon_code="DIWALI10",
        grand_total=total,
        amount_due_on_delivery=total,
    )
    order.refresh_from_db()
    pack_and_send_out(order.pk, actor=staff(), now=NOW)
    order.refresh_from_db()
    return order, Invoice.objects.get(order=order)


# --- the two bug examples ----------------------------------------------------


def test_the_four_line_example_never_makes_a_line_negative() -> None:
    grosses = [Decimal("27.61"), Decimal("29.67"), Decimal("26.60"), Decimal("5.60")]

    shares = _allocate_discount(grosses, Decimal("89.46"))

    # The last line gets exactly its own 5.60, never 5.61, so it stays at 0.00.
    assert shares == [Decimal("27.60"), Decimal("29.66"), Decimal("26.60"), Decimal("5.60")]
    assert sum(shares, ZERO) == Decimal("89.46")
    assert all(share <= gross for share, gross in zip(shares, grosses, strict=True))


def test_every_share_of_the_four_line_example_stays_inside_its_gross() -> None:
    grosses = [Decimal("27.61"), Decimal("29.67"), Decimal("26.60"), Decimal("5.60")]

    shares = _allocate_discount(grosses, Decimal("89.46"))
    discounted = [gross - share for gross, share in zip(grosses, shares, strict=True)]

    # The goods left after the discount, one line at a time.
    assert discounted == [Decimal("0.01"), Decimal("0.01"), ZERO, ZERO]
    assert all(value >= ZERO for value in discounted)


def test_the_six_line_example_never_makes_a_line_negative() -> None:
    grosses = [Decimal("10.00")] * 6

    shares = _allocate_discount(grosses, Decimal("0.04"))

    assert shares == [
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("0.01"),
        ZERO,
        ZERO,
    ]
    assert sum(shares, ZERO) == Decimal("0.04")
    assert all(share <= gross for share, gross in zip(shares, grosses, strict=True))


def test_the_six_line_example_shares_add_up_to_the_discount() -> None:
    shares = _allocate_discount([Decimal("10.00")] * 6, Decimal("0.04"))

    assert sum(shares, ZERO) == Decimal("0.04")


# --- the plain cases ---------------------------------------------------------


def test_a_single_line_takes_the_whole_discount() -> None:
    assert _allocate_discount([Decimal("50.00")], Decimal("12.34")) == [Decimal("12.34")]


def test_two_equal_lines_give_the_odd_paisa_to_the_earlier_one() -> None:
    shares = _allocate_discount([Decimal("10.00"), Decimal("10.00")], Decimal("0.01"))

    assert shares == [PAISA, ZERO]


def test_a_zero_gross_line_in_the_middle_gets_nothing() -> None:
    shares = _allocate_discount(
        [Decimal("10.00"), ZERO, Decimal("10.00")],
        Decimal("0.03"),
    )

    assert shares == [Decimal("0.02"), ZERO, Decimal("0.01")]
    assert sum(shares, ZERO) == Decimal("0.03")


def test_a_discount_equal_to_the_total_gives_each_line_its_gross() -> None:
    grosses = [Decimal("12.34"), Decimal("0.66"), Decimal("3.00")]

    shares = _allocate_discount(grosses, Decimal("16.00"))

    assert shares == grosses


def test_a_discount_above_the_total_is_capped() -> None:
    shares = _allocate_discount([Decimal("12.34"), Decimal("5.00")], Decimal("100.00"))

    assert shares == [Decimal("12.34"), Decimal("5.00")]
    assert sum(shares, ZERO) == Decimal("17.34")


def test_a_one_paisa_discount_over_three_lines_goes_to_one_line() -> None:
    shares = _allocate_discount([Decimal("10.00")] * 3, Decimal("0.01"))

    assert shares == [PAISA, ZERO, ZERO]
    assert sum(shares, ZERO) == PAISA


def test_a_zero_discount_gives_every_line_zero() -> None:
    assert _allocate_discount([Decimal("10.00"), Decimal("20.00")], ZERO) == [ZERO, ZERO]


def test_a_cart_with_no_value_gives_zero_shares() -> None:
    assert _allocate_discount([ZERO, ZERO], Decimal("5.00")) == [ZERO, ZERO]


def test_no_lines_give_no_shares() -> None:
    assert _allocate_discount([], Decimal("10.00")) == []


def test_a_line_with_no_fraction_never_takes_a_paisa() -> None:
    # Both exact shares are whole paise, so there is nothing to hand out.
    shares = _allocate_discount([Decimal("10.00"), Decimal("10.00")], Decimal("0.02"))

    assert shares == [PAISA, PAISA]


def test_equal_fractions_go_to_the_earliest_lines() -> None:
    shares = _allocate_discount([Decimal("10.00")] * 3, Decimal("0.02"))

    assert shares == [PAISA, PAISA, ZERO]


def test_the_shares_keep_the_line_order() -> None:
    # The small line has the bigger fraction, so it takes the paisa, but the
    # shares stay in the order of the lines they belong to.
    shares = _allocate_discount([Decimal("1.00"), Decimal("100.00")], Decimal("1.00"))

    assert shares == [PAISA, Decimal("0.99")]


def test_exact_shares_need_no_paisa() -> None:
    assert _allocate_discount(
        [Decimal("20.00"), Decimal("30.00")],
        Decimal("10.00"),
    ) == [Decimal("4.00"), Decimal("6.00")]


def test_the_smallest_line_can_take_nothing() -> None:
    shares = _allocate_discount([Decimal("0.01"), Decimal("300.00")], Decimal("0.01"))

    # A one-paisa discount belongs to the big line, whose fraction is bigger.
    assert shares == [ZERO, PAISA]


# --- the property ------------------------------------------------------------


def test_every_random_split_stays_inside_its_lines() -> None:
    """Twenty thousand random carts, checked against the three promises."""
    # A seeded generator keeps the test reproducible. S311 warns about random
    # numbers used for security, which this is not.
    rng = random.Random(20261113)  # noqa: S311
    for _case in range(20_000):
        count = rng.randint(1, 6)
        grosses: list[Decimal] = []
        for _line in range(count):
            # About one line in six is worth nothing, to cover a zero gross.
            paise = 0 if rng.random() < 0.16 else rng.randint(1, 30_000)
            grosses.append(Decimal(paise) / 100)
        if sum(grosses, ZERO) <= ZERO:
            grosses[0] = PAISA
        total = sum(grosses, ZERO)
        if rng.random() < 1 / 3:
            # A discount within five paise of the whole cart.
            discount = total - Decimal(rng.randint(0, 5)) / 100
        else:
            discount = Decimal(rng.randint(1, int(total * 100))) / 100
        discount = max(discount, PAISA)

        shares = _allocate_discount(grosses, discount)

        assert len(shares) == len(grosses)
        assert all(share >= ZERO for share in shares)
        assert all(share <= gross for share, gross in zip(shares, grosses, strict=True))
        for share, gross in zip(shares, grosses, strict=True):
            if gross == ZERO:
                assert share == ZERO
        assert sum(shares, ZERO) == min(discount, total)


# --- the whole invoice -------------------------------------------------------


def test_the_four_line_order_is_invoiced_without_a_negative_line() -> None:
    order, invoice = _send_out_discounted(
        ["27.61", "29.67", "26.60", "5.60"],
        discount="89.46",
    )

    assert order.item_total == Decimal("89.48")
    assert order.grand_total == Decimal("0.02")
    assert invoice.grand_total == order.grand_total
    assert [line.discount_amount for line in invoice.lines.all()] == [
        Decimal("27.60"),
        Decimal("29.66"),
        Decimal("26.60"),
        Decimal("5.60"),
    ]
    assert all(line.line_total >= ZERO for line in invoice.lines.all())
    assert find_invoice_problems() == []


def test_the_six_line_order_is_invoiced_without_a_negative_line() -> None:
    order, invoice = _send_out_discounted(["10.00"] * 6, discount="0.04")

    assert order.item_total == Decimal("60.00")
    assert order.grand_total == Decimal("59.96")
    assert invoice.grand_total == order.grand_total
    assert all(line.line_total >= ZERO for line in invoice.lines.all())
    assert find_invoice_problems() == []
