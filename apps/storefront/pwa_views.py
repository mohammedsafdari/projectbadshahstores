"""The three views that make the shop installable (D91).

Each one is a GET only, reads the shop settings at most once and sets no
cookie: a manifest, a service worker and the offline page are the same for
every visitor.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from apps.core.services import get_shop_settings
from apps.storefront import pwa


@require_GET
@cache_control(no_cache=True)
def pwa_manifest(request: HttpRequest) -> HttpResponse:
    """The web app manifest, built from the shop's own settings."""
    manifest = pwa.build_manifest(get_shop_settings())
    return HttpResponse(
        json.dumps(manifest, ensure_ascii=False),
        content_type=pwa.MANIFEST_CONTENT_TYPE,
    )


@require_GET
@cache_control(no_cache=True)
def pwa_service_worker(request: HttpRequest) -> HttpResponse:
    """The service worker, with every address and name filled in here."""
    # Django hands STATIC_URL over as an absolute path ("/static/"), which is
    # the shape the worker compares with a URL's own pathname.
    static_prefix = settings.STATIC_URL
    # Every value arrives in the template as a finished JavaScript literal
    # (quotes included), so the template holds no quoting of its own.
    context = {
        "offline_url": pwa.js_literal(reverse("pwa_offline")),
        "static_prefix": pwa.js_literal(static_prefix),
        "offline_cache": pwa.js_literal(pwa.cache_name("offline")),
        "static_cache": pwa.js_literal(pwa.cache_name("static")),
        "cache_prefix": pwa.js_literal(pwa.CACHE_PREFIX),
        "never_intercept_prefixes": pwa.js_array(pwa.NEVER_INTERCEPT_PREFIXES),
    }
    response = HttpResponse(
        render_to_string("pwa/sw.js", context),
        content_type=pwa.SERVICE_WORKER_CONTENT_TYPE,
    )
    # Without this header a worker at /sw.js may only answer URLs under /sw.js.
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
@cache_control(no_cache=True)
def pwa_offline(request: HttpRequest) -> HttpResponse:
    """The page the worker shows when the phone has no connection."""
    shop = get_shop_settings()
    phone = (shop.phone or "").strip()
    context = {
        "shop_name": pwa.shop_name_or_fallback(shop),
        "phone": phone,
        "phone_link": pwa.tel_link(phone) if phone else "",
    }
    return render(request, "pwa/offline.html", context)
