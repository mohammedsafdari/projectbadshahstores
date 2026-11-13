"""Shared setup for the coupon checkout tests (D82).

Nothing here tests anything by itself. It makes a fresh customer with a served
address and a cart of two packets at 100.00, so the shop's default 30.00 fee
applies and the order totals 230.00 before any coupon.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from apps.accounts.models import Address, User
from apps.catalog.models import ProductVariant
from apps.delivery.models import PinCodeZone
from apps.orders.models import Order
from apps.orders.services import PlacedOrder, place_order
from apps.orders.tests.checkout_helpers import (
    NOW,
    PINCODE,
    address,
    fill_cart,
    reset_settings,
    serve,
    variant,
)
from tests.factories import UserFactory

ZERO = Decimal("0.00")


def ready(
    *,
    price: str = "100.00",
    mrp: str = "120.00",
    quantity: str = "2",
    stock: str = "10.000",
) -> tuple[User, Address, ProductVariant]:
    """A new customer, a served address and a cart whose items total 200.00."""
    reset_settings()
    # A second customer in one test reuses the pin code the first one served.
    if not PinCodeZone.objects.filter(pincode=PINCODE).exists():
        serve()
    user = UserFactory()
    address_row = address(user)
    row = variant(stock=stock, price=price, mrp=mrp)
    fill_cart(user, row, quantity)
    return user, address_row, row


def place(
    user: User,
    address_row: Address,
    *,
    group: UUID | None = None,
    method: str = Order.PaymentMethod.COD,
    remainder: str = Order.PaymentMethod.COD,
    coupon_code: str = "",
    **kwargs: object,
) -> PlacedOrder:
    """Place one order through the real service, with a coupon code."""
    return place_order(
        user=user,
        address_id=address_row.pk,
        payment_method=method,
        remainder_method=remainder,
        checkout_group=group if group is not None else uuid4(),
        now=NOW,
        coupon_code=coupon_code,
        **kwargs,
    )
