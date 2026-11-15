"""The web app manifest: the shop's own name, colours and three icons (D91).

REVIEW CAREFULLY: the manifest is public and identical for every visitor, so
its addresses must not depend on who is asking. Every test reads the real
response, never a helper that builds the dict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
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


def _manifest(client: Client) -> dict:
    response = client.get(reverse("pwa_manifest"))
    assert response.status_code == 200
    return json.loads(response.content)


def test_manifest_returns_200_with_manifest_content_type(client: Client) -> None:
    response = client.get(reverse("pwa_manifest"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/manifest+json"


def test_manifest_name_follows_shop_name(client: Client) -> None:
    _set_shop(shop_name="Gupta Kirana Store")

    assert _manifest(client)["name"] == "Gupta Kirana Store"


def test_manifest_blank_shop_name_falls_back_to_badshah_store(client: Client) -> None:
    _set_shop(shop_name="")

    assert _manifest(client)["name"] == "Badshah Store"


def test_short_name_is_whole_name_when_12_characters_or_fewer(client: Client) -> None:
    # Exactly twelve characters: the boundary the rule allows in full.
    assert len("BadshahStore") == 12
    _set_shop(shop_name="BadshahStore")

    assert _manifest(client)["short_name"] == "BadshahStore"


def test_short_name_is_first_word_when_name_is_longer_than_12(client: Client) -> None:
    # Thirteen characters: one over the boundary, so only the first word stays.
    assert len("Badshah Store") == 13
    _set_shop(shop_name="Badshah Store")

    assert _manifest(client)["short_name"] == "Badshah"


def test_manifest_start_url_scope_and_id_are_the_root(client: Client) -> None:
    manifest = _manifest(client)

    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["id"] == "/"


def test_manifest_display_and_colours(client: Client) -> None:
    manifest = _manifest(client)

    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] == "#ea580c"
    assert manifest["background_color"] == "#ffffff"


def test_manifest_lang_is_en_in(client: Client) -> None:
    assert _manifest(client)["lang"] == "en-IN"


def test_manifest_has_three_icons_with_sizes_and_purposes(client: Client) -> None:
    icons = _manifest(client)["icons"]

    assert [(icon["sizes"], icon["purpose"]) for icon in icons] == [
        ("192x192", "any"),
        ("512x512", "any"),
        ("512x512", "maskable"),
    ]


def test_manifest_icon_urls_resolve_to_real_static_files(client: Client) -> None:
    icons = _manifest(client)["icons"]

    for icon in icons:
        # The address is built with static(), so it starts with STATIC_URL; the
        # finder wants only the part under the static folder.
        relative = icon["src"].split(settings.STATIC_URL, 1)[1]
        found = finders.find(relative)
        assert found is not None, icon["src"]
        assert Path(found).is_file(), icon["src"]


def test_manifest_has_only_the_agreed_keys(client: Client) -> None:
    manifest = _manifest(client)

    assert set(manifest) == {
        "id",
        "name",
        "short_name",
        "start_url",
        "scope",
        "display",
        "theme_color",
        "background_color",
        "lang",
        "icons",
    }


def test_manifest_post_is_405(client: Client) -> None:
    assert client.post(reverse("pwa_manifest")).status_code == 405


def test_manifest_is_not_publicly_cacheable(client: Client) -> None:
    header = client.get(reverse("pwa_manifest")).get("Cache-Control", "")

    assert "no-cache" in header
    assert "public" not in header
    assert "max-age" not in header


def test_pwa_urls_resolve_to_the_expected_paths() -> None:
    assert reverse("pwa_manifest") == "/manifest.webmanifest"
    assert reverse("pwa_service_worker") == "/sw.js"
    assert reverse("pwa_offline") == "/offline/"
