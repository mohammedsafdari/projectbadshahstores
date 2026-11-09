"""Shared setup and HTML readers for the credit checkout tests (D68).

These are small copies of the setup helpers used by the older checkout tests,
kept here so the new test files never import a private helper from another
module. Nothing here tests anything by itself.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Address, User
from apps.accounts.services import create_address
from apps.cart.services import add_item
from apps.cart.stores import DbCartStore
from apps.catalog.models import ProductVariant
from apps.catalog.tests.factories import ProductVariantFactory
from apps.core.models import ShopSettings
from apps.credit.models import CreditAccount
from apps.credit.tests.factories import CreditAccountFactory
from apps.delivery.tests.factories import DeliveryZoneFactory, PinCodeZoneFactory
from apps.inventory.services import receive_stock
from apps.orders.models import Order
from apps.orders.services import PlacedOrder, place_order
from tests.factories import UserFactory

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=IST)
PINCODE = "452012"


def reset_settings() -> None:
    """Create the settings row and empty the cache before a test."""
    cache.clear()
    ShopSettings.load()


def clear_settings() -> None:
    """Empty the settings cache after a test."""
    cache.clear()


def set_settings(**values: object) -> None:
    """Change the one settings row and save only the fields that changed."""
    row = ShopSettings.load()
    for name, value in values.items():
        setattr(row, name, value)
    # ``[*values]`` unpacks the dict's keys into the update_fields list.
    row.save(update_fields=[*values, "updated_at"])


def serve(pincode: str = PINCODE) -> None:
    """Make one pin code deliverable."""
    zone = DeliveryZoneFactory()
    PinCodeZoneFactory(zone=zone, pincode=pincode)


def address(user: User, *, pincode: str = PINCODE, label: str = "HOME") -> Address:
    """One saved address for this customer."""
    return create_address(
        user,
        recipient_name="Asha Sharma",
        recipient_phone="+919876543210",
        line1="12 Test Road",
        line2="Near the park",
        landmark="Opposite the school",
        city="Indore",
        state="Madhya Pradesh",
        pincode=pincode,
        label=label,
    )


def variant(
    *,
    stock: str = "10.000",
    price: str = "100.00",
    mrp: str = "120.00",
) -> ProductVariant:
    """One packet variant with stock, two of which make a 230.00 order."""
    row = ProductVariantFactory(
        is_sold_loose=False,
        unit=ProductVariant.Unit.PACKET,
        pack_size=Decimal("1.000"),
        quantity_step=Decimal("1"),
        selling_price=Decimal(price),
        mrp=Decimal(mrp),
    )
    receive_stock(variant=row, quantity=Decimal(stock))
    return row


def fill_cart(user: User, row: ProductVariant, quantity: str = "2") -> None:
    """Put this quantity of the variant in the customer's database cart."""
    add_item(DbCartStore(user), row, Decimal(quantity))


def credit_account(
    user: User,
    *,
    limit: str = "1000.00",
    balance: str = "0.00",
    active: bool = True,
) -> CreditAccount:
    """Give the customer a credit account with this limit and balance."""
    return CreditAccountFactory(
        user=user,
        credit_limit=Decimal(limit),
        balance=Decimal(balance),
        is_active=active,
    )


def credit_user(
    *,
    limit: str = "1000.00",
    balance: str = "0.00",
    active: bool = True,
) -> User:
    """A fresh customer with a credit account."""
    user = UserFactory()
    credit_account(user, limit=limit, balance=balance, active=active)
    return user


def place_credit(
    user: User,
    address_row: Address,
    *,
    group: UUID | None = None,
    now: datetime = NOW,
    **kwargs: object,
) -> PlacedOrder:
    """Place one credit order through the real service."""
    return place_order(
        user=user,
        address_id=address_row.pk,
        payment_method=Order.PaymentMethod.CREDIT,
        checkout_group=group if group is not None else uuid4(),
        now=now,
        **kwargs,
    )


def place_cod(
    user: User,
    address_row: Address,
    *,
    group: UUID | None = None,
    now: datetime = NOW,
    **kwargs: object,
) -> PlacedOrder:
    """Place one cash-on-delivery order through the real service."""
    return place_order(
        user=user,
        address_id=address_row.pk,
        payment_method=Order.PaymentMethod.COD,
        checkout_group=group if group is not None else uuid4(),
        now=now,
        **kwargs,
    )


def ready_checkout(
    *,
    credit_limit: str | None = None,
    credit_balance: str = "0.00",
    credit_active: bool = True,
) -> tuple[User, Address, ProductVariant, Client]:
    """A logged-in customer with a served address, stock and a ready cart.

    ``credit_limit=None`` means no credit account at all.
    """
    user = UserFactory()
    if credit_limit is not None:
        credit_account(
            user,
            limit=credit_limit,
            balance=credit_balance,
            active=credit_active,
        )
    serve()
    address_row = address(user)
    row = variant()
    fill_cart(user, row)
    client = Client()
    client.force_login(user)
    return user, address_row, row, client


def strip_tags(fragment: str) -> str:
    """Remove inner tags so a container can be compared as plain text."""
    return re.sub(r"<[^>]+>", "", fragment)


def element_text(html: str, marker: str) -> str:
    """The text inside the first element that carries this data marker."""
    # (?P=tag) makes the closing tag match the opening one.
    pattern = (
        rf"<(?P<tag>[a-z0-9]+)\b[^>]*\b{re.escape(marker)}\b[^>]*>"
        rf"(?P<body>.*?)</(?P=tag)>"
    )
    match = re.search(pattern, html, re.DOTALL)
    assert match is not None, f"No element with {marker}."
    return strip_tags(match.group("body")).strip()


def has_element(html: str, marker: str) -> bool:
    """Whether any opening tag carries this data marker."""
    return re.search(rf"<[a-z0-9]+\b[^>]*\b{re.escape(marker)}\b", html) is not None


def inputs(html: str, name: str) -> list[dict[str, object]]:
    """Every input with this name, its attributes and its boolean flags."""
    rows: list[dict[str, object]] = []
    for tag in re.findall(r"<input\b[^>]*>", html):
        attrs = dict(re.findall(r'([a-zA-Z0-9_:-]+)="([^"]*)"', tag))
        if attrs.get("name") != name:
            continue
        attrs["checked"] = " checked" in tag
        attrs["disabled"] = " disabled" in tag
        rows.append(attrs)
    return rows


def form_with_action(html: str, action: str) -> str:
    """Return the first complete <form> whose action is ``action``."""
    pattern = rf'<form[^>]*action="{re.escape(action)}"[^>]*>.*?</form>'
    match = re.search(pattern, html, re.DOTALL)
    assert match is not None, f"No form with action {action}."
    return match.group(0)


def csrf_token_from(form: str) -> str:
    """Read the CSRF token out of one form block."""
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form)
    assert match is not None, "The form has no CSRF token."
    return match.group(1)


def open_checkout(client: Client) -> tuple[str, str]:
    """GET the checkout page and return its hidden group and CSRF token."""
    html = client.get(reverse("checkout")).content.decode()
    form = form_with_action(html, reverse("checkout"))
    groups = inputs(html, "checkout_group")
    assert len(groups) == 1
    return str(groups[0]["value"]), csrf_token_from(form)


def post_data(
    group: str,
    token: str,
    address_row: Address,
    *,
    use_credit: bool = False,
    substitution: str = Order.SubstitutionPreference.CALL_ME,
    note: str = "",
) -> dict[str, str]:
    """The fields one checkout POST needs; ``use_credit`` ticks the khata box."""
    data = {
        "csrfmiddlewaretoken": token,
        "checkout_group": group,
        "address": str(address_row.pk),
        "substitution_preference": substitution,
        "customer_note": note,
    }
    if use_credit:
        data["use_credit"] = "on"
    return data
