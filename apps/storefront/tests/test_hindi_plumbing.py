"""The Hindi plumbing: Django's own catalogue, the staff area and the font (D92).

No shop string is translated yet, so the proof that the plumbing works is
Django's own Hindi catalogue. The staff and admin pages, the crawler files and
the PWA stay English and byte-identical whatever a visitor chose.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.core.models import ShopSettings
from apps.inventory.tests.factories import InventoryMovementFactory
from apps.orders.tests.staff_helpers import staff

pytestmark = pytest.mark.django_db

LANGUAGE_COOKIE = settings.LANGUAGE_COOKIE_NAME
BOTH_LANGUAGES = [("en-IN", "English"), ("hi", "हिन्दी")]
FONTS_DIRECTORY = Path(settings.BASE_DIR) / "static" / "fonts"
TRUE_TYPE_SIGNATURES = (b"\x00\x01\x00\x00", b"true")


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """One settings row and an empty cache, so no test sees a stale cache."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


@pytest.fixture
def hindi_on(settings):
    """The switch on and both languages offered, as a production run has it."""
    settings.HINDI_ENABLED = True
    settings.LANGUAGES = BOTH_LANGUAGES
    return settings


def _visible_product() -> None:
    """One product the storefront shows, so the sitemap is not empty."""
    brand = BrandFactory(is_active=True)
    category = CategoryFactory(is_active=True)
    product = ProductFactory(brand=brand, category=category, is_active=True)
    variant = ProductVariantFactory(
        product=product,
        is_active=True,
        pack_size=Decimal("1.000"),
        selling_price=Decimal("100.00"),
        mrp=Decimal("120.00"),
    )
    InventoryMovementFactory(variant=variant, quantity_change=Decimal("10.000"))


# ---------------------------------------------------------------------------
# Django's own translation plumbing
# ---------------------------------------------------------------------------
def test_django_own_hindi_catalogue_translates_a_message_when_hindi_is_on() -> None:
    english = translation.gettext("This field is required.")
    with translation.override("hi"):
        hindi = translation.gettext("This field is required.")

    # The shop wraps no string yet, so this is Django's own Hindi catalogue.
    assert hindi != english
    assert "क्ष" in hindi


# ---------------------------------------------------------------------------
# The staff and admin areas
# ---------------------------------------------------------------------------
def test_staff_pages_stay_english_with_a_hindi_cookie(hindi_on, client: Client) -> None:
    client.force_login(staff())
    client.cookies[LANGUAGE_COOKIE] = "hi"

    response = client.get(reverse("staff_order_list"))

    assert response.status_code == 200
    # The header comes from the middleware, so a Hindi cookie cannot reach it.
    # Django writes the region part of a language code in lower case.
    assert response.headers["Content-Language"] == "en-in"
    assert '<html lang="en-IN">' in response.content.decode()


def test_the_admin_login_stays_english_with_a_hindi_cookie(hindi_on, client: Client) -> None:
    client.cookies[LANGUAGE_COOKIE] = "hi"

    response = client.get("/admin/login/")

    assert response.status_code == 200
    assert response.headers["Content-Language"] == "en-in"
    page = response.content.decode()
    # The admin draws its own lang from the active language, so this is the
    # middleware's own result; Django writes a language code in lower case.
    assert 'lang="en-in"' in page
    assert 'lang="hi"' not in page
    assert "Log in" in page


# ---------------------------------------------------------------------------
# The files that are the same for every visitor
# ---------------------------------------------------------------------------
def test_the_sitemap_and_robots_are_identical_with_a_hindi_cookie(hindi_on, client: Client) -> None:
    _visible_product()
    english = (
        client.get(reverse("sitemap")).content,
        client.get(reverse("robots_txt")).content,
    )
    client.cookies[LANGUAGE_COOKIE] = "hi"
    hindi = (
        client.get(reverse("sitemap")).content,
        client.get(reverse("robots_txt")).content,
    )

    # The sitemap really holds a product, so the comparison means something.
    assert b"<url><loc>" in english[0]
    assert hindi == english


def test_the_manifest_is_identical_with_a_hindi_cookie(hindi_on, client: Client) -> None:
    english = client.get(reverse("pwa_manifest")).content
    client.cookies[LANGUAGE_COOKIE] = "hi"
    hindi = client.get(reverse("pwa_manifest")).content

    # The manifest really holds the shop, so the comparison means something.
    assert b'"start_url": "/"' in english
    assert hindi == english


def test_the_service_worker_is_identical_with_a_hindi_cookie(hindi_on, client: Client) -> None:
    english = client.get(reverse("pwa_service_worker")).content
    client.cookies[LANGUAGE_COOKIE] = "hi"
    hindi = client.get(reverse("pwa_service_worker")).content

    assert english
    assert hindi == english


# ---------------------------------------------------------------------------
# The web font
# ---------------------------------------------------------------------------
def test_the_font_stylesheet_links_from_the_base_template(client: Client) -> None:
    page = client.get(reverse("home")).content.decode()

    assert '<link rel="stylesheet" href="/static/css/fonts.css">' in page


def test_the_font_stylesheet_names_both_weights_and_the_devanagari_range() -> None:
    source = (Path(settings.BASE_DIR) / "static" / "css" / "fonts.css").read_text(encoding="utf-8")

    assert 'font-family: "Noto Sans Devanagari"' in source
    assert "NotoSansDevanagari-Regular.ttf" in source
    assert "NotoSansDevanagari-Bold.ttf" in source
    assert "font-weight: 400" in source
    assert "font-weight: 700" in source
    assert 'format("truetype")' in source
    # Both faces carry these two declarations, so each must appear twice: one
    # lost declaration means one face loads too late or draws too few
    # characters, and the other face would hide that.
    assert source.count("font-display: swap") == 2
    assert source.count("unicode-range: U+0900-097F, U+200C-200D, U+20B9;") == 2


def test_the_sans_font_stack_ends_with_noto_sans_devanagari() -> None:
    source = (Path(settings.BASE_DIR) / "templates" / "base.html").read_text(encoding="utf-8")

    start = source.index("sans: [")
    block = source[start : source.index("]", start)]
    # re.findall pulls the quoted entries out of the array in order.
    entries = re.findall(r'"[^"]*"', block)

    # The last entry is the Devanagari face; Tailwind's own stack stays first,
    # so Latin text is unchanged.
    assert entries[-1] == '"Noto Sans Devanagari"'
    assert entries[0] == '"ui-sans-serif"'


def test_the_three_font_files_exist_and_the_licence_is_kept() -> None:
    assert (FONTS_DIRECTORY / "OFL.txt").is_file()
    for filename in ("NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari-Bold.ttf"):
        path = FONTS_DIRECTORY / filename
        assert path.is_file()
        assert path.read_bytes()[:4] in TRUE_TYPE_SIGNATURES


def test_the_switcher_is_inside_the_header_nav(hindi_on, client: Client) -> None:
    page = client.get(reverse("home")).content.decode()

    # re.search takes the nav element, so the form is checked in that element.
    nav = re.search(r'<nav aria-label="Primary".*?</nav>', page, re.DOTALL)
    assert nav is not None
    assert 'action="/language/"' in nav.group(0)
