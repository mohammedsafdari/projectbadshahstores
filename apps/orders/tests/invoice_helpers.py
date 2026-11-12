"""Shared setup for the tax invoice tests (D80).

Nothing here tests anything by itself. It gives the tests the two things an
invoice needs: a settings row whose shop details are filled in, and an order
that really went out with real stock behind it.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from django.core.cache import cache

from apps.accounts.models import User
from apps.catalog.models import ProductVariant
from apps.catalog.services import round_money
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import SHOP_SETTINGS_CACHE_KEY, ShopSettings
from apps.inventory.services import receive_stock
from apps.orders.models import Invoice, Order
from apps.orders.staff_services import pack_and_send_out
from apps.orders.tests.factories import OrderFactory, OrderItemFactory
from apps.orders.tests.staff_helpers import NOW, hold_for, staff, stocked_variant
from tests.factories import UserFactory

SHOP_NAME = "Badshah Store"
SHOP_ADDRESS = "12 Saifee Nagar, Indore, Madhya Pradesh 452012"
SHOP_GSTIN = "23AAAAA0000A1Z5"


def fill_shop_details(**values: object) -> ShopSettings:
    """Fill the shop name, address and GSTIN a tax invoice must print."""
    cache.delete(SHOP_SETTINGS_CACHE_KEY)
    row = ShopSettings.load()
    row.shop_name = SHOP_NAME
    row.address = SHOP_ADDRESS
    row.gstin = SHOP_GSTIN
    for name, value in values.items():
        setattr(row, name, value)
    # A set collects the field names, so an override is written too. The ``*``
    # unpacks the dict's keys into the set literal.
    fields = {"shop_name", "address", "gstin", *values}
    row.save(update_fields=[*fields, "updated_at"])
    return row


def gst_variant(
    *,
    gst: str = "5.00",
    price: str = "95.00",
    hsn: str = "1905",
    stock: str = "10.000",
    loose: bool = False,
) -> ProductVariant:
    """One variant that is really in stock, with the GST rate a test needs.

    A loose variant is sold by the kilogram; a packet variant is one packet.
    """
    row = ProductVariantFactory(
        is_sold_loose=loose,
        unit=ProductVariant.Unit.KG if loose else ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("0.001") if loose else Decimal("1"),
        hsn_code=hsn,
        gst_rate_percent=Decimal(gst),
        mrp=Decimal(price) * 2,
        selling_price=Decimal(price),
    )
    receive_stock(variant=row, quantity=Decimal(stock))
    return row


def packing_order(
    *,
    lines: list[tuple[ProductVariant, str]] | None = None,
    customer: User | None = None,
    delivery_fee: str = "30.00",
    **order_fields: object,
) -> tuple[Order, list[ProductVariant]]:
    """A packing order with one stock hold and one line per (variant, quantity).

    The frozen money is built from the variants, so the order is consistent
    with its own lines and with the shop's 30.00 delivery fee.
    """
    user = customer if customer is not None else UserFactory()
    pairs = lines if lines is not None else [(stocked_variant(), "2")]
    group = uuid4()
    item_total = Decimal("0.00")
    for row, quantity in pairs:
        hold_for(row, user, group=group, quantity=quantity)
        item_total += round_money(row.selling_price * Decimal(quantity))
    fee = Decimal(delivery_fee)
    order = OrderFactory(
        user=user,
        status=Order.Status.PACKING,
        checkout_group=group,
        item_total=item_total,
        delivery_fee=fee,
        grand_total=item_total + fee,
        **order_fields,
    )
    for row, quantity in pairs:
        OrderItemFactory(order=order, variant=row, quantity_ordered=Decimal(quantity))
    return order, [row for row, _quantity in pairs]


def sent_out_order(
    *,
    lines: list[tuple[ProductVariant, str]] | None = None,
    customer: User | None = None,
    delivery_fee: str = "30.00",
    **order_fields: object,
) -> tuple[Order, Invoice]:
    """Pack and send out one order through the real service, and return it."""
    order, _variants = packing_order(
        lines=lines,
        customer=customer,
        delivery_fee=delivery_fee,
        **order_fields,
    )
    pack_and_send_out(order.pk, actor=staff(), now=NOW)
    order.refresh_from_db()
    return order, Invoice.objects.get(order=order)
