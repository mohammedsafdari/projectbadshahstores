"""The installable web app: constants and pure helpers (D91).

Nothing here reads the database or the request; the three views in
``apps.storefront.pwa_views`` call these functions and render the answers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from django.templatetags.static import static

from apps.core.models import ShopSettings

# Bumping the version gives every cache a new name, so the worker's activate
# step throws the old copies away with no extra code.
CACHE_VERSION = "1"
CACHE_PREFIX = "badshah-"

# The worker answers nothing at all for these: a staff page, an admin page and
# a payment page must never come from a stored copy.
NEVER_INTERCEPT_PREFIXES = ("/admin/", "/staff/", "/payments/")

MANIFEST_CONTENT_TYPE = "application/manifest+json"
SERVICE_WORKER_CONTENT_TYPE = "text/javascript"

# A standalone window has no address bar, so the shop's own colours fill it.
THEME_COLOR = "#ea580c"
BACKGROUND_COLOR = "#ffffff"
MANIFEST_LANG = "en-IN"

# A home-screen label is cut short by the phone anyway, so a long shop name
# gives its first word instead of a clipped one.
SHORT_NAME_LIMIT = 12

# The name used when the shop's own name field is blank, in the manifest and on
# the offline page alike.
FALLBACK_SHOP_NAME = "Badshah Store"

# One row per icon: (file under static/, declared size, purpose). The maskable
# copy keeps its artwork inside the circle a phone may crop it to.
ICON_SPECS: tuple[tuple[str, str, str], ...] = (
    ("img/pwa/icon-192.png", "192x192", "any"),
    ("img/pwa/icon-512.png", "512x512", "any"),
    ("img/pwa/icon-512-maskable.png", "512x512", "maskable"),
)

DIGITS = "0123456789"


def shop_name_or_fallback(shop_settings: ShopSettings) -> str:
    """The shop's name, or the agreed fallback when the field is blank."""
    name = (shop_settings.shop_name or "").strip()
    return name or FALLBACK_SHOP_NAME


def short_name(name: str) -> str:
    """The home-screen label: the whole name, or its first word when it is long."""
    if len(name) <= SHORT_NAME_LIMIT:
        return name
    # ``split()`` breaks on any run of whitespace and drops the empty pieces, so
    # the first item is the first word whatever spacing the name uses.
    return name.split()[0]


def manifest_icons() -> list[dict[str, str]]:
    """The three icons, at addresses that follow STATIC_URL and its hash."""
    # A list comprehension builds one dict per row of ICON_SPECS in one go.
    return [
        {"src": static(path), "sizes": sizes, "purpose": purpose}
        for path, sizes, purpose in ICON_SPECS
    ]


def cache_name(kind: str) -> str:
    """One cache name: the prefix, the kind and the version in use."""
    return f"{CACHE_PREFIX}{kind}-v{CACHE_VERSION}"


def js_literal(value: str) -> str:
    """One Python string as the JavaScript string literal that holds it.

    ``json.dumps`` writes the surrounding quotes and escapes the quotes,
    backslashes and control characters inside them, so a name the owner typed
    can never break out of the literal. The three characters that could end an
    enclosing HTML block are escaped as well.
    """
    encoded = json.dumps(value)
    return encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def js_array(values: Iterable[str]) -> str:
    """Several Python strings as one JavaScript array literal."""
    # A list comprehension turns each item into its own literal, and join glues
    # them with commas inside the brackets.
    return "[" + ", ".join(js_literal(value) for value in values) + "]"


def build_manifest(shop_settings: ShopSettings) -> dict[str, Any]:
    """The web app manifest as a plain dict, ready for ``json.dumps``."""
    name = shop_name_or_fallback(shop_settings)
    return {
        "id": "/",
        "name": name,
        "short_name": short_name(name),
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": THEME_COLOR,
        "background_color": BACKGROUND_COLOR,
        "lang": MANIFEST_LANG,
        "icons": manifest_icons(),
    }


def tel_link(phone: str) -> str:
    """A ``tel:`` address holding digits and one leading ``+`` only."""
    cleaned = ""
    # ``enumerate`` gives the position as well as the character, because only
    # the very first character may become the "+".
    for position, character in enumerate(phone.strip()):
        is_digit = character in DIGITS
        is_leading_plus = character == "+" and position == 0
        if is_digit or is_leading_plus:
            cleaned += character
    return f"tel:{cleaned}"
