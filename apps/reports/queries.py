"""Read-only staff reports: sales, own-brand margin, GST, COD and credit, and
stock valuation (D85).

Nothing in this module writes: no order, invoice, stock movement or credit row
is changed. Every money figure is read from the invoice or the order line it was
frozen on (D80, D82), so a later edit to a product or to the shop settings can
never change a past report. Money is ``Decimal``, never float.

Every report checks the ``VIEW_REPORTS`` capability first (D75) and then reads
the database with a constant number of queries, whatever the number of rows.

REVIEW CAREFULLY: money and tax live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, QuerySet, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.accounts.capabilities import Capability, has_capability
from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.services import round_money
from apps.credit.models import CreditAccount, CreditEntry
from apps.inventory.models import InventoryMovement
from apps.orders.models import CodCollection, Invoice, InvoiceLine, OrderItem
from apps.orders.staff_queries import cash_reconciliation
from apps.orders.staff_services import StaffPermissionError

# Money and quantity zeros. ``sum`` and a missing database sum both start here,
# so an empty report still shows money as ``0.00`` and units as ``0.000``.
ZERO = Decimal("0.00")
QUANTITY_ZERO = Decimal("0.000")
PERCENT_PLACES = Decimal("0.01")
HUNDRED = Decimal("100")

# The longest range a report accepts, both Indian dates included.
MAX_RANGE_DAYS = 366

# How many variants without a cost price a page lists before it only counts them.
BLANK_COST_LIMIT = 50

# The four sections of the sales page. Plain strings, so a template and a test
# can compare them without importing an enum.
SALES_BY_DAY = "day"
SALES_BY_PRODUCT = "product"
SALES_BY_BRAND = "brand"
SALES_BY_CATEGORY = "category"
SALES_KEYS = (SALES_BY_DAY, SALES_BY_PRODUCT, SALES_BY_BRAND, SALES_BY_CATEGORY)

SALES_HEADINGS: dict[str, str] = {
    SALES_BY_DAY: "Day",
    SALES_BY_PRODUCT: "Product",
    SALES_BY_BRAND: "Brand",
    SALES_BY_CATEGORY: "Category",
}

# The two groups of the own-brand report.
OWN_BRAND_LABEL = "Own brand"
THIRD_PARTY_LABEL = "Third-party"

# A line whose order line no longer explains it (for example an invoice line
# typed straight into the database in a test) is still counted, under this name.
UNKNOWN_GROUP = "Unknown"

# The sentence a bad date range shows instead of figures.
BAD_RANGE_DATE = "Enter both dates as YYYY-MM-DD, for example 2026-11-01."
REVERSED_RANGE = "The start date is after the end date."
LONG_RANGE = "The range is longer than 366 days. Choose a shorter range."

# The note the GST page and its CSV must carry until the CA answers (D85).
GST_NOTE = (
    "Unconfirmed until the CA answers: GST on a coupon discount, delivery fee GST, "
    "regular or composition dealer, cancelled invoices."
)

# Shown instead of the COD pending total when a role may read reports but not
# orders: the COD rule belongs to ``cash_reconciliation`` (D79), not to us.
COD_NEEDS_ORDERS = (
    "COD pending is not shown: this role cannot view orders, and the cash rules "
    "live on the order screens."
)


class ReportsPermissionError(PermissionError):
    """The signed-in user may not read this report (D75, D85)."""


def require_reports(actor: User, capability: str = Capability.VIEW_REPORTS) -> None:
    """Refuse a user without the capability before any database read."""
    if not has_capability(actor, capability):
        label = Capability(capability).label
        raise ReportsPermissionError(f"You do not have the right to {label.lower()}.")


# ---------------------------------------------------------------------------
# The date range
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DateRange:
    """Two Indian dates, both included, and the sentence shown when they are bad.

    A bad range still carries the default range, so the page can print the form
    and the message without crashing.
    """

    start: date
    end: date
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the two dates can be reported on."""
        return not self.error


def _today() -> date:
    """Today in the shop's time zone (``TIME_ZONE`` is Asia/Kolkata)."""
    return timezone.localdate()


def _parsed(value: str | None) -> date | None:
    """An ISO ``YYYY-MM-DD`` value, or None when it is blank or not a date.

    ``parse_date`` reads the basic form itself and then builds the date from its
    parts, which raises ``ValueError`` for a month such as 13 instead of
    returning None, so the call is wrapped here.
    """
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def parse_range(
    from_value: str | None,
    to_value: str | None,
    *,
    today: date | None = None,
) -> DateRange:
    """Read the ``from`` and ``to`` query parameters into a range (D85).

    Both are inclusive Indian dates. A missing one means "the first day of this
    month" for ``from`` and "today" for ``to``, so a blank page shows the month
    so far. A value that is not a date, a range that runs backwards and a range
    longer than 366 days are each answered with their own sentence, never with
    an error page.
    """
    current = today if today is not None else _today()
    default_start = current.replace(day=1)

    start = _parsed(from_value)
    end = _parsed(to_value)
    if from_value and start is None:
        return DateRange(default_start, current, BAD_RANGE_DATE)
    if to_value and end is None:
        return DateRange(default_start, current, BAD_RANGE_DATE)

    start = start or default_start
    end = end or current
    if start > end:
        return DateRange(default_start, current, REVERSED_RANGE)
    # Both ends are included, so a single day is one day long.
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        return DateRange(default_start, current, LONG_RANGE)
    return DateRange(start, end)


def _start_of(day: date) -> datetime:
    """Midnight at the start of this Indian day, as a timezone-aware datetime.

    An invoice issued at 00:30 Indian time belongs to this day even though it is
    still the previous day in UTC, which is why the boundary is built in the
    shop's zone and not in the server's.
    """
    return datetime.combine(day, time.min, tzinfo=timezone.get_current_timezone())


def _window(prefix: str, period: DateRange) -> dict[str, datetime]:
    """A ``__gte``/``__lt`` pair for one datetime column, in Indian time."""
    return {
        f"{prefix}__gte": _start_of(period.start),
        f"{prefix}__lt": _start_of(period.end + timedelta(days=1)),
    }


# ---------------------------------------------------------------------------
# Small totals
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MoneyTotals:
    """Four figures a sales row carries: units, taxable value, GST and total."""

    units: Decimal
    taxable: Decimal
    gst: Decimal
    total: Decimal


def _dec(value: object, zero: Decimal = ZERO) -> Decimal:
    """A database sum as ``Decimal``, or the zero to use when it summed nothing."""
    return value if isinstance(value, Decimal) else zero


def _add(left: MoneyTotals, right: MoneyTotals) -> MoneyTotals:
    return MoneyTotals(
        units=left.units + right.units,
        taxable=left.taxable + right.taxable,
        gst=left.gst + right.gst,
        total=left.total + right.total,
    )


def _totals_of(
    units: object,
    taxable: object,
    cgst: object,
    sgst: object,
    total: object,
) -> MoneyTotals:
    """Build the four figures from one grouped database row."""
    return MoneyTotals(
        units=_dec(units, QUANTITY_ZERO),
        taxable=_dec(taxable),
        gst=_dec(cgst) + _dec(sgst),
        total=_dec(total),
    )


def _empty_totals() -> MoneyTotals:
    return MoneyTotals(QUANTITY_ZERO, ZERO, ZERO, ZERO)


# ---------------------------------------------------------------------------
# The lines and the invoices every report reads
# ---------------------------------------------------------------------------
def _lines_in_range(period: DateRange, *, fee_lines: bool = False) -> QuerySet[InvoiceLine]:
    """The invoice lines of the non-cancelled invoices issued in the range.

    A cancelled invoice is left out of every total; it is counted on its own
    line instead. The product lines are the default, because the delivery fee is
    reported as a separate row and is not something that was sold.
    """
    queryset = InvoiceLine.objects.filter(
        invoice__cancelled_at__isnull=True,
        **_window("invoice__issued_at", period),
    )
    if not fee_lines:
        queryset = queryset.filter(is_fee_line=False)
    return queryset


def _cancelled_invoices(period: DateRange) -> tuple[int, Decimal]:
    """How many invoices were cancelled in the range, and their grand totals."""
    sums = Invoice.objects.filter(
        cancelled_at__isnull=False,
        **_window("issued_at", period),
    ).aggregate(count=Count("pk"), total=Sum("grand_total"))
    return int(sums["count"] or 0), _dec(sums["total"])


def _grouped(fields: tuple[str, ...], period: DateRange) -> list[dict[str, Any]]:
    """One database row per value of ``fields`` with the five summed columns."""
    return list(
        _lines_in_range(period)
        .values(*fields)
        .annotate(
            units=Sum("quantity"),
            taxable=Sum("taxable_value"),
            cgst=Sum("cgst"),
            sgst=Sum("sgst"),
            total=Sum("line_total"),
        )
    )


# ---------------------------------------------------------------------------
# 1. Sales by day, product, brand and category
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SalesRow:
    """One label of a sales section and its figures."""

    label: str
    totals: MoneyTotals


@dataclass(frozen=True)
class SalesReport:
    """One sales section: its rows, their total and the cancelled invoices."""

    period: DateRange
    key: str
    heading: str
    rows: list[SalesRow]
    totals: MoneyTotals
    cancelled_count: int
    cancelled_total: Decimal


@dataclass(frozen=True)
class LineOwner:
    """What the product behind one invoice line was, frozen nowhere on the line."""

    brand: str
    category: str
    own_brand: bool
    # True only when the variant has a cost price, because a margin needs one.
    has_cost: bool


def _owner_of(row: dict[str, Any]) -> LineOwner:
    """The brand, the category, the own-brand flag and the cost of one line.

    They come from the variant of the order line the invoice line links to
    (D86), so two packs of one product (Atta 1 kg with a cost, Atta 5 kg with
    none) each keep their own answer: the name and the HSN are the same, the
    variant is not. A line with no link — the delivery-fee line, or an invoice
    written before the link existed — keeps the unknown owner and counts as
    having no cost, because guessing from a name is what let the wrong variant
    through.
    """
    brand = row["order_item__variant__product__brand__name"]
    if brand is None:
        return LineOwner(
            brand=UNKNOWN_GROUP,
            category=UNKNOWN_GROUP,
            own_brand=False,
            has_cost=False,
        )
    return LineOwner(
        brand=brand,
        category=row["order_item__variant__product__category__name"],
        own_brand=bool(row["order_item__variant__product__is_own_brand"]),
        # The cost price itself, so a blank can be told apart from 0 (D85).
        has_cost=row["order_item__variant__cost_price"] is not None,
    )


def _lines_with_owner(period: DateRange) -> list[dict[str, Any]]:
    """One row per invoice line, with the product behind it and its four totals.

    The join follows the invoice line's own order line (D86), which is one query
    for the whole range: the owner is never looked up once per line.
    """
    # dict() turns each typed row into a plain lookup table the loops below read.
    return [
        dict(row)
        for row in _lines_in_range(period).values(
            "order_item__variant__product__brand__name",
            "order_item__variant__product__category__name",
            "order_item__variant__product__is_own_brand",
            "order_item__variant__cost_price",
            "quantity",
            "taxable_value",
            "cgst",
            "sgst",
            "line_total",
        )
    ]


def _row_totals(row: dict[str, Any]) -> MoneyTotals:
    """The four figures of one invoice-line row."""
    return _totals_of(
        row["quantity"],
        row["taxable_value"],
        row["cgst"],
        row["sgst"],
        row["line_total"],
    )


def _totals_by_label(period: DateRange, key: str) -> dict[str, MoneyTotals]:
    """Add the invoice lines up by brand or by category.

    The brand and the category are read through the line's own order line (D86),
    in the same single query that reads the money, so the lines are added up
    here; the money still comes from the line, never from a recomputed price.
    """
    totals: dict[str, MoneyTotals] = {}
    for row in _lines_with_owner(period):
        owner = _owner_of(row)
        label = owner.brand if key == SALES_BY_BRAND else owner.category
        line = _row_totals(row)
        totals[label] = _add(totals[label], line) if label in totals else line
    return totals


def _totals_by_own_brand(period: DateRange) -> dict[bool, MoneyTotals]:
    """Add the invoice lines up by the own-brand flag of their product."""
    totals: dict[bool, MoneyTotals] = {}
    for row in _lines_with_owner(period):
        own_brand = _owner_of(row).own_brand
        line = _row_totals(row)
        totals[own_brand] = _add(totals[own_brand], line) if own_brand in totals else line
    return totals


def sales_report(actor: User, period: DateRange, key: str) -> SalesReport:
    """One section of the sales page, read-only (D85).

    The four sections all read the same invoice lines, so they always add up to
    the same sales total: the line totals of the non-cancelled invoices, which
    is each invoice's own total minus its delivery-fee line.
    """
    require_reports(actor)
    if key not in SALES_KEYS:
        raise ValueError(f"Unknown sales section {key!r}.")

    rows: list[SalesRow]
    if key == SALES_BY_DAY:
        # TruncDate asks the database for the Indian date of ``issued_at``.
        day = TruncDate("invoice__issued_at", tzinfo=timezone.get_current_timezone())
        grouped = list(
            _lines_in_range(period)
            .annotate(day=day)
            .values("day")
            .annotate(
                units=Sum("quantity"),
                taxable=Sum("taxable_value"),
                cgst=Sum("cgst"),
                sgst=Sum("sgst"),
                total=Sum("line_total"),
            )
            .order_by("day")
        )
        rows = [
            SalesRow(
                label=row["day"].isoformat(),
                totals=_totals_of(
                    row["units"], row["taxable"], row["cgst"], row["sgst"], row["total"]
                ),
            )
            for row in grouped
        ]
    elif key == SALES_BY_PRODUCT:
        rows = [
            SalesRow(
                label=row["description"],
                totals=_totals_of(
                    row["units"], row["taxable"], row["cgst"], row["sgst"], row["total"]
                ),
            )
            for row in sorted(
                _grouped(("description",), period),
                key=lambda row: (-_dec(row["total"]), row["description"]),
            )
        ]
    else:
        by_label = _totals_by_label(period, key)
        rows = [
            SalesRow(label=label, totals=by_label[label])
            for label in sorted(by_label, key=lambda label: (-by_label[label].total, label))
        ]

    summed = _empty_totals()
    for row in rows:
        summed = _add(summed, row.totals)
    cancelled_count, cancelled_total = _cancelled_invoices(period)
    return SalesReport(
        period=period,
        key=key,
        heading=SALES_HEADINGS[key],
        rows=rows,
        totals=summed,
        cancelled_count=cancelled_count,
        cancelled_total=cancelled_total,
    )


def sales_reports(actor: User, period: DateRange) -> list[SalesReport]:
    """The four sales sections, in the order the page prints them."""
    return [sales_report(actor, period, key) for key in SALES_KEYS]


# ---------------------------------------------------------------------------
# 2. Own brand against third-party
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MarginGroup:
    """One group: its revenue, the part of it a margin can use, and the margin."""

    label: str
    revenue: Decimal
    costed_revenue: Decimal
    uncosted_revenue: Decimal
    cost: Decimal
    margin: Decimal
    margin_percent: Decimal


@dataclass(frozen=True)
class BlankCostVariant:
    """One variant with no cost price, listed so the owner can fill it in."""

    sku_code: str
    name: str


@dataclass(frozen=True)
class OwnBrandReport:
    """The two margin groups, their cost coverage and the variants with no cost."""

    period: DateRange
    own_brand: MarginGroup
    third_party: MarginGroup
    blank_cost_count: int
    blank_cost_variants: list[BlankCostVariant]
    revenue_total: Decimal

    @property
    def blank_cost_more(self) -> int:
        """How many variants with no cost the list left out."""
        return max(self.blank_cost_count - len(self.blank_cost_variants), 0)

    @property
    def groups(self) -> list[MarginGroup]:
        """The two groups, in the order the page and the CSV print them."""
        return [self.own_brand, self.third_party]


def blank_cost_variants(limit: int = BLANK_COST_LIMIT) -> tuple[int, list[BlankCostVariant]]:
    """Every variant whose cost price is blank: a count and the first few.

    A blank cost is never read as zero (D85); it is a fact the owner still has
    to enter, so the pages say how many there are instead of guessing.
    """
    queryset = (
        ProductVariant.objects.filter(cost_price__isnull=True)
        .select_related("product")
        .order_by("sku_code")
    )
    count = queryset.count()
    rows = list(queryset[:limit])
    return count, [
        BlankCostVariant(sku_code=variant.sku_code, name=variant.product.name_en)
        for variant in rows
    ]


def _percent(part: Decimal, whole: Decimal) -> Decimal:
    """``part`` as a percent of ``whole``, rounded half-up to two places."""
    if whole <= ZERO:
        return ZERO
    return (part / whole * HUNDRED).quantize(PERCENT_PLACES)


def _revenue_by_own_brand(period: DateRange) -> dict[bool, Decimal]:
    """Taxable value of the non-cancelled invoice lines, split by own brand."""
    revenue = {True: ZERO, False: ZERO}
    for own_brand, totals in _totals_by_own_brand(period).items():
        revenue[own_brand] = totals.taxable
    return revenue


def _costed_revenue_by_own_brand(period: DateRange) -> dict[bool, Decimal]:
    """Taxable value of only the lines whose variant has a cost price, by owner.

    One pass over the same lines the revenue reads, so the report costs the same
    number of queries as before. A blank cost is never read as zero (D85): the
    revenue of such a line is reported on its own and is not part of the margin.
    """
    costed = {True: ZERO, False: ZERO}
    for row in _lines_with_owner(period):
        owner = _owner_of(row)
        if not owner.has_cost:
            continue
        costed[owner.own_brand] += _dec(row["taxable_value"])
    return costed


def _cost_by_own_brand(period: DateRange) -> dict[bool, Decimal]:
    """Cost of the goods sold in the range, at the variant's cost price today.

    A variant with no cost price is skipped, not counted as free (D85). Its sale
    is left out of the margin as well, so a group is never shown as a 100 percent
    margin just because the cost was never entered; the page lists those
    variants and their revenue.
    """
    cost = {True: ZERO, False: ZERO}
    rows = OrderItem.objects.filter(
        order__invoice__isnull=False,
        order__invoice__cancelled_at__isnull=True,
        item_status=OrderItem.ItemStatus.FULFILLED,
        variant__cost_price__isnull=False,
        **_window("order__invoice__issued_at", period),
    ).values("variant__product__is_own_brand", "quantity_fulfilled", "variant__cost_price")
    for row in rows:
        cost_price = row["variant__cost_price"]
        # The filter above guarantees a cost, but the type checker cannot see it.
        if cost_price is None:
            continue
        own_brand = bool(row["variant__product__is_own_brand"])
        cost[own_brand] += round_money(row["quantity_fulfilled"] * cost_price)
    return cost


def own_brand_report(actor: User, period: DateRange) -> OwnBrandReport:
    """Own brand against third-party: revenue, cost at today's cost, margin.

    Every margin figure is at today's cost, because no order line, invoice line
    or stock movement freezes a cost (D85). The margin covers only the sales of
    variants that have a cost price; the rest is shown as uncosted revenue.
    """
    require_reports(actor)
    revenue = _revenue_by_own_brand(period)
    costed = _costed_revenue_by_own_brand(period)
    cost = _cost_by_own_brand(period)

    def group(label: str, own_brand: bool) -> MarginGroup:
        costed_revenue = costed[own_brand]
        margin = costed_revenue - cost[own_brand]
        return MarginGroup(
            label=label,
            revenue=revenue[own_brand],
            costed_revenue=costed_revenue,
            uncosted_revenue=revenue[own_brand] - costed_revenue,
            cost=cost[own_brand],
            margin=margin,
            margin_percent=_percent(margin, costed_revenue),
        )

    blank_count, blank_rows = blank_cost_variants()
    return OwnBrandReport(
        period=period,
        own_brand=group(OWN_BRAND_LABEL, True),
        third_party=group(THIRD_PARTY_LABEL, False),
        blank_cost_count=blank_count,
        blank_cost_variants=blank_rows,
        revenue_total=revenue[True] + revenue[False],
    )


# ---------------------------------------------------------------------------
# 3. GST summary by rate
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GstRow:
    """One GST rate: the taxable value, the two halves, the tax and the invoices."""

    rate: Decimal
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    gst: Decimal
    invoice_count: int
    is_fee_line: bool


@dataclass(frozen=True)
class GstReport:
    """Every GST rate of the range, the delivery-fee rows and the note."""

    period: DateRange
    rows: list[GstRow]
    fee_rows: list[GstRow]
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    gst: Decimal
    cancelled_count: int
    cancelled_total: Decimal
    note: str


def _gst_rows(rows: list[dict[str, Any]], *, fee_lines: bool) -> list[GstRow]:
    built = [
        GstRow(
            rate=row["gst_rate_percent"],
            taxable=_dec(row["taxable"]),
            cgst=_dec(row["cgst"]),
            sgst=_dec(row["sgst"]),
            gst=_dec(row["cgst"]) + _dec(row["sgst"]),
            invoice_count=int(row["invoice_count"]),
            is_fee_line=fee_lines,
        )
        for row in rows
    ]
    return sorted(built, key=lambda row: row.rate)


def gst_report(actor: User, period: DateRange) -> GstReport:
    """The GST summary by rate, from the frozen invoice lines (D85).

    The delivery fee is its own row, because its rate is a separate, unconfirmed
    business fact. The total GST is the sum of every line's two halves, which is
    the sum of the invoices' own ``gst_total``.
    """
    require_reports(actor)
    grouped = list(
        _lines_in_range(period, fee_lines=True)
        .values("gst_rate_percent", "is_fee_line")
        .annotate(
            taxable=Sum("taxable_value"),
            cgst=Sum("cgst"),
            sgst=Sum("sgst"),
            invoice_count=Count("invoice", distinct=True),
        )
        .order_by("gst_rate_percent")
    )
    rows = _gst_rows([row for row in grouped if not row["is_fee_line"]], fee_lines=False)
    fee_rows = _gst_rows([row for row in grouped if row["is_fee_line"]], fee_lines=True)

    taxable = sum((row.taxable for row in rows + fee_rows), ZERO)
    cgst = sum((row.cgst for row in rows + fee_rows), ZERO)
    sgst = sum((row.sgst for row in rows + fee_rows), ZERO)
    cancelled_count, cancelled_total = _cancelled_invoices(period)
    return GstReport(
        period=period,
        rows=rows,
        fee_rows=fee_rows,
        taxable=taxable,
        cgst=cgst,
        sgst=sgst,
        gst=cgst + sgst,
        cancelled_count=cancelled_count,
        cancelled_total=cancelled_total,
        note=GST_NOTE,
    )


# ---------------------------------------------------------------------------
# 4. COD and credit
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CodCreditReport:
    """The cash collected, the cash still to collect and the khata figures."""

    period: DateRange
    cod_collected: Decimal
    cod_collected_count: int
    cod_pending: Decimal
    cod_pending_count: int
    cod_pending_note: str
    khata_outstanding: Decimal
    khata_account_count: int
    khata_payments: Decimal
    khata_payment_count: int


def cod_and_credit_report(actor: User, period: DateRange) -> CodCreditReport:
    """COD collected against pending, plus khata outstanding and payments (D85).

    The pending rule is ``cash_reconciliation``'s own: the staff cash page lists
    every order that still owes cash until it is collected or cancelled, and
    this report asks that function instead of writing the rule a second time.
    The collected figure is the range's total, which the one-day cash page does
    not answer.
    """
    require_reports(actor)
    collected = CodCollection.objects.filter(**_window("collected_at", period)).aggregate(
        count=Count("pk"), total=Sum("amount")
    )

    pending_note = ""
    pending = ZERO
    pending_count = 0
    try:
        unpaid = cash_reconciliation(actor, period.end).unpaid_orders
    except StaffPermissionError:
        # A role may hold the reports right without the order right.
        pending_note = COD_NEEDS_ORDERS
    else:
        for order in unpaid:
            pending += order.amount_due_on_delivery
        pending_count = len(unpaid)

    khata = CreditAccount.objects.aggregate(count=Count("pk"), total=Sum("balance"))
    paid = CreditEntry.objects.filter(
        entry_type=CreditEntry.EntryType.PAYMENT,
        entry_date__gte=period.start,
        entry_date__lte=period.end,
    ).aggregate(count=Count("pk"), total=Sum("amount"))

    return CodCreditReport(
        period=period,
        cod_collected=_dec(collected["total"]),
        cod_collected_count=int(collected["count"] or 0),
        cod_pending=pending,
        cod_pending_count=pending_count,
        cod_pending_note=pending_note,
        khata_outstanding=_dec(khata["total"]),
        khata_account_count=int(khata["count"] or 0),
        # A payment is stored as a negative amount, so the report shows the
        # money the shop received as a positive figure.
        khata_payments=-_dec(paid["total"]),
        khata_payment_count=int(paid["count"] or 0),
    )


# ---------------------------------------------------------------------------
# 5. Stock valuation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ValuationRow:
    """One variant: what is on the shelf, what it cost, and what that is worth."""

    sku_code: str
    name: str
    brand: str
    category: str
    on_hand: Decimal
    cost_price: Decimal
    value: Decimal


@dataclass(frozen=True)
class ValuationGroup:
    """A brand or category subtotal of the stock valuation."""

    label: str
    value: Decimal


@dataclass(frozen=True)
class ValuationReport:
    """The stock valuation as of now, with its subtotals and the blank costs."""

    rows: list[ValuationRow]
    brand_totals: list[ValuationGroup]
    category_totals: list[ValuationGroup]
    grand_total: Decimal
    blank_cost_count: int
    blank_cost_variants: list[BlankCostVariant]

    @property
    def blank_cost_more(self) -> int:
        """How many variants with no cost the list left out."""
        return max(self.blank_cost_count - len(self.blank_cost_variants), 0)


def _on_hand_totals(variant_ids: list[int]) -> dict[int, Decimal]:
    """The ledger sum per variant, in a constant number of queries.

    ``order_by()`` clears the model's ordering, which would otherwise join the
    GROUP BY and split one variant into several rows.
    """
    if not variant_ids:
        return {}
    rows = (
        InventoryMovement.objects.filter(variant_id__in=variant_ids)
        .order_by()
        .values("variant_id")
        .annotate(total=Sum("quantity_change"))
        .values_list("variant_id", "total")
    )
    return {variant_id: _dec(total, QUANTITY_ZERO) for variant_id, total in rows}


def stock_valuation_report(actor: User) -> ValuationReport:
    """What the stock is worth at today's cost price, as of now (D85).

    There is no date range: the shelf is what it is now. A variant with no cost
    price is skipped and counted, and a variant with nothing (or less than
    nothing) on the shelf is left out, so a negative count never becomes a
    negative value.
    """
    require_reports(actor)
    variants = list(
        ProductVariant.objects.filter(cost_price__isnull=False)
        .select_related("product__brand", "product__category")
        .order_by("sku_code")
    )
    on_hand = _on_hand_totals([variant.pk for variant in variants])

    rows: list[ValuationRow] = []
    brand_totals: dict[str, Decimal] = {}
    category_totals: dict[str, Decimal] = {}
    grand_total = ZERO
    for variant in variants:
        quantity = on_hand.get(variant.pk, QUANTITY_ZERO)
        if quantity <= QUANTITY_ZERO:
            continue
        cost_price = variant.cost_price
        # The filter above guarantees a cost, but the type checker cannot see it.
        if cost_price is None:
            continue
        value = round_money(quantity * cost_price)
        rows.append(
            ValuationRow(
                sku_code=variant.sku_code,
                name=variant.product.name_en,
                brand=variant.product.brand.name,
                category=variant.product.category.name,
                on_hand=quantity,
                cost_price=cost_price,
                value=value,
            )
        )
        brand_totals[variant.product.brand.name] = (
            brand_totals.get(variant.product.brand.name, ZERO) + value
        )
        category_totals[variant.product.category.name] = (
            category_totals.get(variant.product.category.name, ZERO) + value
        )
        grand_total += value

    blank_count, blank_rows = blank_cost_variants()
    return ValuationReport(
        rows=rows,
        brand_totals=[
            ValuationGroup(label=label, value=value)
            for label, value in sorted(brand_totals.items())
        ],
        category_totals=[
            ValuationGroup(label=label, value=value)
            for label, value in sorted(category_totals.items())
        ],
        grand_total=grand_total,
        blank_cost_count=blank_count,
        blank_cost_variants=blank_rows,
    )
