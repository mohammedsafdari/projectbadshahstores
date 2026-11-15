"""The offline page: plain HTML that works with no network at all (D91).

REVIEW CAREFULLY: this page prints the shop's own phone number. It must load
nothing external, run no script and escape everything it prints.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.core.models import ShopSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _set_shop(**fields: object) -> None:
    """Change the one settings row; its save clears the settings cache."""
    row = ShopSettings.load()
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()


def _offline_html(client: Client) -> str:
    response = client.get(reverse("pwa_offline"))
    assert response.status_code == 200
    return response.content.decode()


def test_offline_page_returns_200_html(client: Client) -> None:
    response = client.get(reverse("pwa_offline"))

    assert response.status_code == 200
    assert "text/html" in response["Content-Type"]


def test_offline_page_does_not_load_tailwind_htmx_or_alpine(client: Client) -> None:
    html = _offline_html(client).lower()

    # The page is shown exactly when nothing can be downloaded, so it must not
    # extend base.html and its three script libraries.
    assert "tailwind" not in html
    assert "htmx" not in html
    assert "alpine" not in html


def test_offline_page_has_no_script_and_no_inline_handlers(client: Client) -> None:
    html = _offline_html(client)

    assert "<script" not in html.lower()
    assert "onclick=" not in html.lower()
    assert "javascript:" not in html.lower()


def test_offline_page_shows_shop_name(client: Client) -> None:
    _set_shop(shop_name="Gupta Kirana Store")

    assert "Gupta Kirana Store" in _offline_html(client)


def test_offline_page_blank_name_falls_back(client: Client) -> None:
    _set_shop(shop_name="")

    # The same fallback the manifest uses, so the two never disagree.
    assert "Badshah Store" in _offline_html(client)


def test_offline_page_shows_call_line_when_phone_is_set(client: Client) -> None:
    _set_shop(phone="+91 98765 43210")

    assert "Call us to order:" in _offline_html(client)


def test_offline_page_tel_link_keeps_digits_and_plus_only(client: Client) -> None:
    _set_shop(phone="+91 98765 43210")

    html = _offline_html(client)

    # A tel: address may not hold spaces, so only the digits and the one
    # leading plus survive.
    assert 'href="tel:+919876543210"' in html
    assert 'href="tel:+91 98765 43210"' not in html


def test_offline_page_hides_call_line_when_phone_is_blank(client: Client) -> None:
    _set_shop(phone="")

    html = _offline_html(client)

    assert "Call us to order:" not in html
    assert "tel:" not in html


def test_offline_page_escapes_shop_name_and_phone(client: Client) -> None:
    _set_shop(shop_name="Badshah & <b>Sons</b>", phone="<b>98765</b>")

    html = _offline_html(client)

    assert "Badshah &amp; &lt;b&gt;Sons&lt;/b&gt;" in html
    assert "<b>Sons</b>" not in html
    assert "&lt;b&gt;98765&lt;/b&gt;" in html
    assert "<b>98765</b>" not in html


def test_offline_page_try_again_link_has_empty_href(client: Client) -> None:
    html = _offline_html(client)

    # An empty href reloads the address the visitor asked for, query string and
    # all, which "/" would throw away.
    assert 'href=""' in html
    assert "Try again" in html


def test_offline_page_is_noindex(client: Client) -> None:
    assert '<meta name="robots" content="noindex">' in _offline_html(client)


def test_offline_page_is_not_publicly_cacheable(client: Client) -> None:
    header = client.get(reverse("pwa_offline")).get("Cache-Control", "")

    assert "no-cache" in header
    assert "public" not in header
    assert "max-age" not in header


def test_offline_page_sets_no_cookie_and_prints_no_csrf_token(client: Client) -> None:
    response = client.get(reverse("pwa_offline"))
    html = response.content.decode()

    assert response.cookies == {}
    assert "csrfmiddlewaretoken" not in html
    assert "csrftoken" not in html


def test_offline_page_is_not_in_the_sitemap(client: Client) -> None:
    sitemap = client.get(reverse("sitemap")).content.decode()

    assert "/offline/" not in sitemap


def test_offline_page_post_is_405(client: Client) -> None:
    assert client.post(reverse("pwa_offline")).status_code == 405
