"""The head tags every shop page prints for the installable app (D91).

The tags live in base.html once, so a cart page and a checkout page must carry
the same manifest link as the home page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.core.models import ShopSettings
from apps.orders.tests.checkout_helpers import ready_checkout

pytestmark = pytest.mark.django_db

MANIFEST_LINK = '<link rel="manifest" href="/manifest.webmanifest">'


@pytest.fixture(autouse=True)
def _shop_settings_row():
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def test_home_links_the_manifest() -> None:
    html = Client().get(reverse("home")).content.decode()

    assert MANIFEST_LINK in html


def test_home_has_theme_color_meta() -> None:
    html = Client().get(reverse("home")).content.decode()

    assert '<meta name="theme-color" content="#ea580c">' in html


def test_home_links_apple_touch_icon_and_favicon() -> None:
    html = Client().get(reverse("home")).content.decode()

    assert 'rel="apple-touch-icon"' in html
    assert "apple-touch-icon-180.png" in html
    assert 'type="image/png"' in html
    assert 'sizes="32x32"' in html
    assert "favicon-32.png" in html


def test_home_loads_the_register_script_with_defer() -> None:
    html = Client().get(reverse("home")).content.decode()

    # re.search pulls the one script tag out, so the defer is checked on that
    # tag and not on some other script in the page.
    tag = re.search(r"<script[^>]*pwa-register\.js[^>]*>", html)
    assert tag is not None
    assert "defer" in tag.group(0)
    assert "js/pwa-register.js" in tag.group(0)


def test_register_script_tag_carries_the_sw_url() -> None:
    html = Client().get(reverse("home")).content.decode()

    assert 'data-sw-url="/sw.js"' in html


def test_register_script_file_registers_a_service_worker() -> None:
    path = Path(settings.BASE_DIR) / "static" / "js" / "pwa-register.js"
    source = path.read_text(encoding="utf-8")

    assert '"serviceWorker" in navigator' in source
    assert "navigator.serviceWorker.register" in source
    assert 'getAttribute("data-sw-url")' in source
    assert '"/sw.js"' in source
    assert 'scope: "/"' in source
    assert 'addEventListener("load"' in source


def test_cart_and_product_pages_link_the_manifest() -> None:
    _user, _address, variant, client = ready_checkout()
    product_url = reverse("product_detail", args=[variant.product.slug])

    cart_html = client.get(reverse("cart_detail")).content.decode()
    product_html = client.get(product_url).content.decode()

    assert MANIFEST_LINK in cart_html
    assert MANIFEST_LINK in product_html


def test_checkout_page_links_the_manifest() -> None:
    _user, _address, _variant, client = ready_checkout()

    html = client.get(reverse("checkout")).content.decode()

    assert MANIFEST_LINK in html
