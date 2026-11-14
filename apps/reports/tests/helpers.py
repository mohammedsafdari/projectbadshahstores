"""Shared setup for the report tests (D85).

Nothing here tests anything by itself. It builds the data a report reads: a
variant in stock (optionally with a cost price), and an order that really went
out, so its invoice and its lines hold the frozen figures.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import Client

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.services import receive_stock
from apps.orders.models import Invoice, Order
from apps.orders.staff_services import pack_and_send_out
from apps.orders.tests.invoice_helpers import packing_order
from apps.orders.tests.staff_helpers import NOW, staff
from tests.factories import UserFactory

IST = ZoneInfo("Asia/Kolkata")

FIRST_DAY = date(2026, 11, 1)
LAST_DAY = date(2026, 11, 30)


def staff_client(role: str = User.ROLE_OWNER) -> Client:
    """A signed-in staff client for this role."""
    client = Client()
    client.force_login(staff(role))
    return client


def member_client() -> Client:
    """A signed-in customer (not staff) client."""
    client = Client()
    client.force_login(UserFactory(is_staff=False))
    return client


def make_variant(
    *,
    name: str = "Atta",
    price: str = "95.00",
    gst: str = "5.00",
    cost: str | None = None,
    own_brand: bool = False,
    loose: bool = False,
    stock: str | None = "20.000",
    hsn: str = "1101",
    brand: object | None = None,
    category: object | None = None,
) -> ProductVariant:
    """One variant that is really in stock, with the cost and flags a test needs."""
    product = ProductFactory(
        name_en=name,
        brand=brand if brand is not None else BrandFactory(),
        category=category if category is not None else CategoryFactory(),
        is_own_brand=own_brand,
    )
    variant = ProductVariantFactory(
        product=product,
        unit=ProductVariant.Unit.KG if loose else ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        is_sold_loose=loose,
        quantity_step=Decimal("0.001") if loose else Decimal("1"),
        hsn_code=hsn,
        gst_rate_percent=Decimal(gst),
        mrp=Decimal(price) * 2,
        selling_price=Decimal(price),
        cost_price=None if cost is None else Decimal(cost),
    )
    if stock is not None:
        receive_stock(variant=variant, quantity=Decimal(stock))
    return variant


def sold(
    *,
    lines: list[tuple[ProductVariant, str]],
    when: datetime = NOW,
    customer: User | None = None,
    delivery_fee: str = "30.00",
    **order_fields: object,
) -> tuple[Order, Invoice]:
    """An order that really went out at ``when``, and its invoice."""
    order, _variants = packing_order(
        lines=lines,
        customer=customer,
        delivery_fee=delivery_fee,
        **order_fields,
    )
    pack_and_send_out(order.pk, actor=staff(), now=when)
    order.refresh_from_db()
    return order, Invoice.objects.get(order=order)
