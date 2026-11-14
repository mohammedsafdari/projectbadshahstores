"""Own brand against third-party, with the margin at today's cost (D85)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.orders.tests.staff_helpers import staff
from apps.reports.queries import (
    OWN_BRAND_LABEL,
    THIRD_PARTY_LABEL,
    DateRange,
    ReportsPermissionError,
    blank_cost_variants,
    own_brand_report,
)
from apps.reports.tests.helpers import FIRST_DAY, LAST_DAY, make_variant, sold

pytestmark = pytest.mark.django_db

NOVEMBER = DateRange(FIRST_DAY, LAST_DAY)


def test_the_own_brand_group_reads_its_revenue_and_cost() -> None:
    variant = make_variant(name="Haldi", price="95.00", cost="60.00", own_brand=True)
    _order, invoice = sold(lines=[(variant, "2")])
    line = invoice.lines.get(is_fee_line=False)

    report = own_brand_report(staff(), NOVEMBER)

    assert report.own_brand.label == OWN_BRAND_LABEL
    assert report.own_brand.revenue == line.taxable_value
    assert report.own_brand.cost == Decimal("120.00")


def test_the_margin_amount_and_percent_follow_the_two_groups() -> None:
    variant = make_variant(price="95.00", cost="60.00", own_brand=True)
    _order, invoice = sold(lines=[(variant, "2")])
    revenue = invoice.lines.get(is_fee_line=False).taxable_value
    expected_percent = ((revenue - Decimal("120.00")) / revenue * Decimal("100")).quantize(
        Decimal("0.01")
    )

    report = own_brand_report(staff(), NOVEMBER)

    assert report.own_brand.margin == revenue - Decimal("120.00")
    assert report.own_brand.margin_percent == expected_percent


def test_a_third_party_line_lands_in_the_third_party_group() -> None:
    variant = make_variant(name="KitKat", cost="40.00", own_brand=False)
    _order, invoice = sold(lines=[(variant, "3")])
    revenue = invoice.lines.get(is_fee_line=False).taxable_value

    report = own_brand_report(staff(), NOVEMBER)

    assert report.own_brand.revenue == Decimal("0.00")
    assert report.third_party.label == THIRD_PARTY_LABEL
    assert report.third_party.revenue == revenue
    assert report.third_party.cost == Decimal("120.00")


def test_a_group_with_no_revenue_has_a_zero_margin_percent() -> None:
    variant = make_variant(cost="60.00", own_brand=True)
    sold(lines=[(variant, "1")])

    report = own_brand_report(staff(), NOVEMBER)

    assert report.third_party.revenue == Decimal("0.00")
    assert report.third_party.cost == Decimal("0.00")
    assert report.third_party.margin_percent == Decimal("0.00")


def test_revenue_is_the_taxable_value_not_the_printed_total() -> None:
    variant = make_variant(price="95.00", cost="60.00", own_brand=True)
    _order, invoice = sold(lines=[(variant, "1")])
    line = invoice.lines.get(is_fee_line=False)

    report = own_brand_report(staff(), NOVEMBER)

    assert report.own_brand.revenue == line.taxable_value
    assert report.own_brand.revenue < line.line_total


def test_a_variant_without_a_cost_is_skipped_and_listed() -> None:
    cheap = make_variant(name="No cost", own_brand=True, cost=None)
    priced = make_variant(name="With cost", own_brand=True, cost="50.00")
    sold(lines=[(cheap, "1")])
    sold(lines=[(priced, "2")])

    report = own_brand_report(staff(), NOVEMBER)

    # The cost covers only the variant that has one.
    assert report.own_brand.cost == Decimal("100.00")
    assert report.blank_cost_count == 1
    assert [row.sku_code for row in report.blank_cost_variants] == [cheap.sku_code]
    assert report.blank_cost_variants[0].name == "No cost"


def test_a_sale_with_no_cost_is_left_out_of_the_margin_and_shown_as_uncosted() -> None:
    variant = make_variant(name="No cost", price="95.00", own_brand=True, cost=None)
    _order, invoice = sold(lines=[(variant, "2")])
    revenue = invoice.lines.get(is_fee_line=False).taxable_value

    report = own_brand_report(staff(), NOVEMBER)

    # Without this, a blank cost would read as a 100 percent margin.
    assert report.own_brand.revenue == revenue
    assert report.own_brand.costed_revenue == Decimal("0.00")
    assert report.own_brand.uncosted_revenue == revenue
    assert report.own_brand.cost == Decimal("0.00")
    assert report.own_brand.margin == Decimal("0.00")
    assert report.own_brand.margin_percent == Decimal("0.00")


def test_a_group_where_every_cost_is_blank_has_a_zero_margin() -> None:
    first = make_variant(name="No cost one", own_brand=True, cost=None)
    second = make_variant(name="No cost two", own_brand=False, cost=None)
    sold(lines=[(first, "1")])
    sold(lines=[(second, "4")])

    report = own_brand_report(staff(), NOVEMBER)

    for group in report.groups:
        assert group.margin == Decimal("0.00")
        assert group.margin_percent == Decimal("0.00")
        assert group.uncosted_revenue == group.revenue
    assert report.revenue_total == report.own_brand.revenue + report.third_party.revenue
    assert report.revenue_total > Decimal("0.00")


def test_a_mix_of_costed_and_uncosted_lines_splits_one_group() -> None:
    priced = make_variant(name="With cost", price="95.00", own_brand=True, cost="50.00")
    free = make_variant(name="No cost", price="95.00", own_brand=True, cost=None)
    _priced_order, priced_invoice = sold(lines=[(priced, "2")])
    _free_order, free_invoice = sold(lines=[(free, "3")])
    priced_revenue = priced_invoice.lines.get(is_fee_line=False).taxable_value
    free_revenue = free_invoice.lines.get(is_fee_line=False).taxable_value

    report = own_brand_report(staff(), NOVEMBER)
    group = report.own_brand

    assert group.costed_revenue == priced_revenue
    assert group.uncosted_revenue == free_revenue
    assert group.revenue == priced_revenue + free_revenue
    assert group.cost == Decimal("100.00")
    assert group.margin == priced_revenue - Decimal("100.00")


def test_the_margin_percent_is_taken_over_the_costed_revenue_only() -> None:
    priced = make_variant(name="With cost", price="95.00", own_brand=True, cost="50.00")
    free = make_variant(name="No cost", price="95.00", own_brand=True, cost=None)
    _a, priced_invoice = sold(lines=[(priced, "2")])
    sold(lines=[(free, "2")])
    costed = priced_invoice.lines.get(is_fee_line=False).taxable_value
    expected = ((costed - Decimal("100.00")) / costed * Decimal("100")).quantize(Decimal("0.01"))

    report = own_brand_report(staff(), NOVEMBER)
    group = report.own_brand
    # What the percent would be if it were taken over the whole revenue instead.
    over_all = (group.margin / group.revenue * Decimal("100")).quantize(Decimal("0.01"))

    assert group.costed_revenue == costed
    assert group.revenue > costed
    assert group.margin_percent == expected
    assert group.margin_percent != over_all


def test_a_line_with_no_order_line_counts_as_uncosted() -> None:
    variant = make_variant(name="Haldi", price="95.00", cost="60.00", own_brand=True)
    _order, invoice = sold(lines=[(variant, "1")])
    line = invoice.lines.get(is_fee_line=False)
    # A name the order never had: the line can no longer be matched to a product,
    # so the fallback owner has no brand and, above all, no known cost.
    line.description = "A name the order never had"
    line.save(update_fields=["description"])

    report = own_brand_report(staff(), NOVEMBER)

    assert report.third_party.revenue == line.taxable_value
    assert report.third_party.costed_revenue == Decimal("0.00")
    assert report.third_party.uncosted_revenue == line.taxable_value
    assert report.third_party.margin == Decimal("0.00")


def test_costed_and_uncosted_revenue_add_up_to_the_revenue() -> None:
    priced = make_variant(name="With cost", price="95.00", own_brand=True, cost="50.00")
    free = make_variant(name="No cost", price="95.00", own_brand=False, cost=None)
    sold(lines=[(priced, "2")])
    sold(lines=[(free, "1")])

    report = own_brand_report(staff(), NOVEMBER)

    for group in report.groups:
        assert group.costed_revenue + group.uncosted_revenue == group.revenue
    assert report.revenue_total == report.own_brand.revenue + report.third_party.revenue


def test_each_group_keeps_its_own_costed_and_uncosted_figures() -> None:
    own_priced = make_variant(name="Haldi", price="95.00", own_brand=True, cost="60.00")
    own_free = make_variant(name="Masala", price="95.00", own_brand=True, cost=None)
    third = make_variant(name="KitKat", price="95.00", own_brand=False, cost="40.00")
    _a, own_priced_invoice = sold(lines=[(own_priced, "1")])
    _b, own_free_invoice = sold(lines=[(own_free, "2")])
    _c, third_invoice = sold(lines=[(third, "3")])

    report = own_brand_report(staff(), NOVEMBER)

    assert report.own_brand.costed_revenue == (
        own_priced_invoice.lines.get(is_fee_line=False).taxable_value
    )
    assert report.own_brand.uncosted_revenue == (
        own_free_invoice.lines.get(is_fee_line=False).taxable_value
    )
    assert report.third_party.costed_revenue == (
        third_invoice.lines.get(is_fee_line=False).taxable_value
    )
    assert report.third_party.uncosted_revenue == Decimal("0.00")
    assert report.own_brand.cost == Decimal("60.00")
    assert report.third_party.cost == Decimal("120.00")


def test_the_blank_cost_list_is_capped() -> None:
    for index in range(3):
        make_variant(name=f"No cost {index}", cost=None)

    count, rows = blank_cost_variants(limit=2)

    assert count == 3
    assert len(rows) == 2


def test_the_blank_cost_remainder_is_counted_for_the_page() -> None:
    for index in range(3):
        make_variant(name=f"No cost {index}", cost=None)

    report = own_brand_report(staff(), NOVEMBER)

    assert report.blank_cost_count == 3
    assert report.blank_cost_more == 0


def test_a_user_without_the_capability_cannot_read_the_margin() -> None:
    with pytest.raises(ReportsPermissionError):
        own_brand_report(staff("PACKER"), NOVEMBER)
