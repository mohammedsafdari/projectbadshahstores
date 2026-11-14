"""Search-engine output for the public pages (D87).

This module builds absolute addresses, the robots.txt text, the sitemap XML and
the JSON-LD blocks. It never copies a visibility rule: the sitemap asks the
same ``visible_products`` and ``CategoryTree`` the listing pages use (D50).
"""

from __future__ import annotations

import json
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse

from apps.catalog.models import Brand, Category
from apps.core.models import ShopSettings
from apps.storefront.services import CategoryTree, visible_products

# Every prefix a search engine must stay out of: private pages and search.
ROBOTS_PRIVATE_PREFIXES: tuple[str, ...] = (
    "/admin/",
    "/staff/",
    "/cart/",
    "/checkout/",
    "/orders/",
    "/payments/",
    "/account/",
    "/delivery/",
    "/search/",
)


def site_origin(request: HttpRequest) -> str:
    """The site's own address: ``SITE_BASE_URL`` when set, else this request's.

    A trailing slash is removed so one ``+ path`` always joins cleanly.
    """
    configured = str(getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    if configured:
        return configured
    return f"{request.scheme}://{request.get_host()}"


def absolute_url(request: HttpRequest, path: str) -> str:
    """A site path as the absolute address a search engine needs."""
    return site_origin(request) + path


def canonical_url(request: HttpRequest) -> str:
    """The page's one real address: filters dropped, ``page`` kept above one.

    Any query string other than ``page`` is a filter or a sort order, so such a
    page points back at its clean address, and only the second page onwards
    keeps its page number.
    """
    page = request.GET.get("page", "")
    # Iterating GET yields one entry per parameter name, so this sees a filter
    # even when its value is blank.
    filtered = any(key != "page" for key in request.GET)
    if not filtered and page.isdigit() and int(page) > 1:
        return absolute_url(request, request.path + "?page=" + page)
    return absolute_url(request, request.path)


def dump_json_ld(payload: dict[str, Any]) -> str:
    """One JSON-LD block as text that can never close its own ``<script>``.

    ``json.dumps`` alone leaves ``</script>`` intact, so a product name holding
    it would end the element early. Every ``<``, ``>`` and ``&`` becomes its
    six-character JSON escape, which JSON readers decode back unchanged.
    """
    dumped = json.dumps(payload)
    return dumped.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def breadcrumb_json_ld(request: HttpRequest, breadcrumbs: list[dict[str, str | None]]) -> str:
    """A BreadcrumbList built from the trail the page already shows.

    Positions start at 1, as schema.org requires; a crumb with no address (the
    page itself) carries only its name.
    """
    items: list[dict[str, Any]] = []
    for position, crumb in enumerate(breadcrumbs, start=1):
        item: dict[str, Any] = {
            "@type": "ListItem",
            "position": position,
            "name": crumb["label"],
        }
        url = crumb.get("url")
        if url:
            item["item"] = absolute_url(request, url)
        items.append(item)
    return dump_json_ld(
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": items,
        }
    )


def organization_json_ld(request: HttpRequest, shop: ShopSettings) -> str:
    """The shop itself: a GroceryStore with only the facts staff have filled."""
    payload: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "GroceryStore",
        "url": absolute_url(request, reverse("home")),
    }
    if shop.shop_name:
        payload["name"] = shop.shop_name
    if shop.address:
        payload["address"] = {"@type": "PostalAddress", "streetAddress": shop.address}
    if shop.phone:
        payload["telephone"] = shop.phone
    if shop.email:
        payload["email"] = shop.email
    return dump_json_ld(payload)


def website_json_ld(request: HttpRequest, shop_name: str) -> str:
    """The site and its search box, so a search engine can offer it directly."""
    search_template = absolute_url(request, reverse("product_search")) + "?q={search_term_string}"
    return dump_json_ld(
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": shop_name,
            "url": absolute_url(request, reverse("home")),
            "potentialAction": {
                "@type": "SearchAction",
                "target": {
                    "@type": "EntryPoint",
                    "urlTemplate": search_template,
                },
                "query-input": "required name=search_term_string",
            },
        }
    )


def sitemap_paths() -> list[str]:
    """Every public address, home first, then categories, brands and products.

    A category is listed only when it and all its ancestors are active, and a
    product only when it passes the listing pages' one rule, so a hidden row
    can never reach the sitemap.
    """
    tree = CategoryTree()
    paths = [reverse("home")]
    visible_category_ids = tree.visible_ids()
    category_slugs = (
        Category.objects.filter(pk__in=visible_category_ids)
        .order_by("name")
        .values_list("slug", flat=True)
    )
    for slug in category_slugs:
        paths.append(reverse("category_detail", args=[slug]))
    brand_slugs = (
        Brand.objects.filter(is_active=True).order_by("name").values_list("slug", flat=True)
    )
    for slug in brand_slugs:
        paths.append(reverse("brand_detail", args=[slug]))
    product_slugs = visible_products(tree=tree, sort="name").values_list("slug", flat=True)
    for slug in product_slugs:
        paths.append(reverse("product_detail", args=[slug]))
    return paths


def sitemap_xml_text(request: HttpRequest) -> str:
    """The sitemap document as text, one ``<url>`` per public address."""
    origin = site_origin(request)
    entries = "".join(
        f"<url><loc>{xml_escape(origin + path)}</loc></url>" for path in sitemap_paths()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


def robots_text(request: HttpRequest) -> str:
    """The robots.txt text: private prefixes out, the sitemap named."""
    lines = ["User-agent: *"]
    lines.extend(f"Disallow: {prefix}" for prefix in ROBOTS_PRIVATE_PREFIXES)
    lines.append("")
    lines.append(f"Sitemap: {absolute_url(request, reverse('sitemap'))}")
    return "\n".join(lines) + "\n"
