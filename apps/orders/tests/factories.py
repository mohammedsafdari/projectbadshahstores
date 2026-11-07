"""Test factories for the order models (factory_boy)."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import factory

from apps.catalog.tests.factories import ProductVariantFactory
from apps.orders.models import Order, OrderItem
from tests.factories import UserFactory


def _pack_label(variant) -> str:
    """The same label rule the order service uses for a frozen line."""
    return variant.net_quantity_label or f"{variant.pack_size:g} {variant.unit.lower()}"


class OrderFactory(factory.django.DjangoModelFactory):
    """A live PLACED order with consistent money and a fresh checkout group."""

    class Meta:
        model = Order

    # The arithmetic sequence never runs out of digits, unlike a fixed-width
    # counter that would eventually repeat a padded number.
    order_number = factory.Sequence(lambda n: f"BS/26-27/{n + 1}")
    user = factory.SubFactory(UserFactory)
    # LazyFunction calls uuid4 for each new row, so two factories never share
    # the unique checkout group unless a test passes one.
    checkout_group = factory.LazyFunction(uuid4)
    status = Order.Status.PLACED
    payment_method = Order.PaymentMethod.COD

    ship_name = "Asha Sharma"
    ship_phone = "+919876543210"
    ship_line1 = "12 Test Road"
    ship_line2 = ""
    ship_landmark = ""
    ship_city = "Indore"
    ship_state = "Madhya Pradesh"
    # A six-digit sequence from 452000 stays valid far past 100 rows.
    ship_pincode = factory.Sequence(lambda n: f"{452000 + n:06d}")
    ship_label = "HOME"

    item_total = Decimal("200.00")
    delivery_fee = Decimal("30.00")
    grand_total = Decimal("230.00")
    savings_total = Decimal("40.00")
    amount_due_on_delivery = Decimal("230.00")

    promised_delivery_date = factory.LazyFunction(lambda: date(2026, 11, 7))
    delivery_is_same_day = True
    substitution_preference = Order.SubstitutionPreference.CALL_ME
    customer_note = ""
    cancel_reason = ""
    cancelled_at = None
    delivered_at = None


class OrderItemFactory(factory.django.DjangoModelFactory):
    """One PENDING line whose snapshots come from the variant it points at."""

    class Meta:
        model = OrderItem

    order = factory.SubFactory(OrderFactory)
    variant = factory.SubFactory(ProductVariantFactory)

    # LazyAttribute reads the already-built related object, so the snapshot
    # matches the variant unless the test overrides a single field.
    sku_code = factory.LazyAttribute(lambda item: item.variant.sku_code)
    product_name = factory.LazyAttribute(lambda item: item.variant.product.name_en)
    pack_label = factory.LazyAttribute(lambda item: _pack_label(item.variant))
    unit = factory.LazyAttribute(lambda item: item.variant.unit)
    hsn_code = factory.LazyAttribute(lambda item: item.variant.hsn_code)
    gst_rate_percent = factory.LazyAttribute(lambda item: item.variant.gst_rate_percent)
    mrp = factory.LazyAttribute(lambda item: item.variant.mrp)
    unit_price = factory.LazyAttribute(lambda item: item.variant.selling_price)

    quantity_ordered = Decimal("1.000")
    quantity_fulfilled = Decimal("0.000")
    line_total = factory.LazyAttribute(
        lambda item: (item.unit_price * item.quantity_ordered).quantize(Decimal("0.01"))
    )
    item_status = OrderItem.ItemStatus.PENDING
