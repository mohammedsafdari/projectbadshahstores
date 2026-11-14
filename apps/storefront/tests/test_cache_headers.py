"""Cache headers: two public files may be cached, no page may be (D90).

REVIEW CAREFULLY: a private page that answers with a cacheable header can be
stored by a proxy and shown to the next visitor. Each test therefore drives
real URLs with a user who may open them and checks the response really is 200.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.core.models import ShopSettings
from apps.orders.tests.checkout_helpers import clear_settings, ready_checkout, reset_settings
from apps.orders.tests.factories import OrderFactory
from apps.orders.tests.invoice_helpers import fill_shop_details, sent_out_order
from apps.orders.tests.staff_helpers import staff
from apps.payments.fake_gateway import FakeGateway
from apps.payments.tests.helpers import factory_online_order
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _cache_control(response) -> str:
    return response.get("Cache-Control", "")


def _assert_not_cacheable(response, label: str) -> None:
    header = _cache_control(response)
    assert "no-store" in header or "private" in header, f"{label}: {header!r}"


def _assert_all_not_cacheable(client: Client, urls: dict[str, str]) -> None:
    # A list that became empty would make the loop prove nothing.
    assert urls
    for label, url in urls.items():
        response = client.get(url)
        assert response.status_code == 200, (label, url)
        _assert_not_cacheable(response, label)


# ---------------------------------------------------------------------------
# The two public files
# ---------------------------------------------------------------------------
def test_robots_txt_is_cacheable_for_an_hour(client: Client) -> None:
    header = _cache_control(client.get(reverse("robots_txt")))

    assert "public" in header
    assert "max-age=3600" in header


def test_sitemap_is_cacheable_for_an_hour(client: Client) -> None:
    header = _cache_control(client.get(reverse("sitemap")))

    assert "public" in header
    assert "max-age=3600" in header


def test_robots_and_sitemap_set_no_cookie_dependent_vary(client: Client) -> None:
    robots = client.get(reverse("robots_txt"))
    sitemap = client.get(reverse("sitemap"))

    assert "Cookie" not in robots.get("Vary", "")
    assert "Cookie" not in sitemap.get("Vary", "")


# ---------------------------------------------------------------------------
# Private pages
# ---------------------------------------------------------------------------
def test_customer_pages_are_not_cacheable() -> None:
    user, _address_row, _variant_row, client = ready_checkout()
    order = OrderFactory(user=user)
    _assert_all_not_cacheable(
        client,
        {
            "cart": reverse("cart_detail"),
            "checkout": reverse("checkout"),
            "confirmation": reverse("order_confirmation", args=[order.checkout_group]),
            "order list": reverse("order_list"),
            "order detail": reverse("order_detail", args=[order.checkout_group]),
            "order cancel": reverse("order_cancel", args=[order.checkout_group]),
        },
    )


def test_account_and_address_pages_are_not_cacheable() -> None:
    user = UserFactory(phone_verified_at=None, email_verified_at=None)
    client = Client()
    client.force_login(user)
    _assert_all_not_cacheable(
        client,
        {
            "account home": reverse("account_home"),
            "password": reverse("account_password"),
            "email": reverse("account_email"),
            "address book": reverse("address_list"),
            "address add": reverse("address_add"),
            "verify phone": reverse("account_verify_phone"),
            "verify email": reverse("account_verify_email"),
        },
    )


def test_payment_pages_are_not_cacheable(settings) -> None:
    reset_settings()
    settings.PAYMENT_PROVIDER = "fake"
    FakeGateway.reset()
    order = factory_online_order()
    client = Client()
    client.force_login(order.user)
    try:
        _assert_all_not_cacheable(
            client,
            {
                "payment return": reverse("payment_return", args=[order.order_number]),
                "payment status": reverse("payment_status", args=[order.order_number]),
            },
        )
        # The start page is POST-only, so it is opened the way the customer does.
        started = client.post(reverse("payment_start", args=[order.order_number]))
        assert started.status_code == 200
        _assert_not_cacheable(started, "payment start")
    finally:
        FakeGateway.reset()
        clear_settings()


def test_staff_order_pages_are_not_cacheable() -> None:
    actor = staff()
    order = OrderFactory(user=UserFactory())
    client = Client()
    client.force_login(actor)
    _assert_all_not_cacheable(
        client,
        {
            "staff orders": reverse("staff_order_list"),
            "pick list": reverse("staff_pick_list"),
            "staff order detail": reverse("staff_order_detail", args=[order.order_number]),
            "delivery list": reverse("staff_delivery_list"),
            "cash page": reverse("staff_cash"),
        },
    )


def test_staff_report_pages_are_not_cacheable() -> None:
    client = Client()
    client.force_login(staff())
    _assert_all_not_cacheable(
        client,
        {
            "reports index": reverse("reports_index"),
            "sales": reverse("reports_sales"),
            "own brand": reverse("reports_own_brand"),
            "gst": reverse("reports_gst"),
            "cod and credit": reverse("reports_cod_credit"),
            "stock valuation": reverse("reports_stock_valuation"),
            "search words": reverse("reports_search_words"),
            "cost prices": reverse("reports_cost_prices"),
        },
    )


def test_credit_and_invoice_pages_are_not_cacheable() -> None:
    fill_shop_details()
    order, _invoice = sent_out_order()
    actor = staff()
    staff_client = Client()
    staff_client.force_login(actor)
    _assert_all_not_cacheable(
        staff_client,
        {
            "credit dues": reverse("staff_credit_dues"),
            "invoice page": reverse("staff_invoice", args=[order.order_number]),
            "invoice pdf": reverse("staff_invoice_pdf", args=[order.order_number]),
        },
    )
    customer_client = Client()
    customer_client.force_login(order.user)
    _assert_all_not_cacheable(
        customer_client,
        {"customer invoice pdf": reverse("order_invoice_pdf", args=[order.checkout_group])},
    )


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
def test_public_pages_set_no_shared_cache_header(client: Client) -> None:
    _user, _address_row, variant, _ready_client = ready_checkout()
    product = variant.product
    urls = [
        reverse("home"),
        reverse("product_list"),
        reverse("product_detail", args=[product.slug]),
        reverse("product_search") + "?q=packet",
    ]
    assert urls
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        header = _cache_control(response)
        assert "public" not in header, (url, header)
        assert "s-maxage" not in header, (url, header)


# ---------------------------------------------------------------------------
# Production static storage
# ---------------------------------------------------------------------------
def test_prod_settings_use_manifest_static_storage() -> None:
    path = Path(__file__).resolve().parents[3] / "badshahstore" / "settings" / "prod.py"
    text = path.read_text(encoding="utf-8")

    block = text.split("STORAGES = {", 1)[1]
    default_part = block.split('"default"', 1)[1].split('"staticfiles"', 1)[0]
    staticfiles_part = block.split('"staticfiles"', 1)[1].split("}", 1)[0]

    assert "FileSystemStorage" in default_part
    assert "ManifestStaticFilesStorage" in staticfiles_part
