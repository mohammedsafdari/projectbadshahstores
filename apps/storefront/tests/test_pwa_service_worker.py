"""The service worker as text (D91).

There is no JavaScript engine in the tests, so every assertion names one
distinctive fragment of the rendered file and stands for one behaviour: the
comment above each test says which. A fragment that vanished, or a value that
stopped following its setting, is what these tests are for.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from apps.core.models import ShopSettings
from apps.storefront import pwa

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _shop_settings_row():
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _sw_text(client: Client) -> str:
    response = client.get(reverse("pwa_service_worker"))
    assert response.status_code == 200
    return response.content.decode()


def test_sw_returns_200_with_javascript_content_type(client: Client) -> None:
    response = client.get(reverse("pwa_service_worker"))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/javascript"


def test_sw_cache_control_is_no_cache_and_not_public(client: Client) -> None:
    header = client.get(reverse("pwa_service_worker")).get("Cache-Control", "")

    assert "no-cache" in header
    assert "public" not in header
    assert "max-age" not in header


def test_sw_sets_service_worker_allowed_root(client: Client) -> None:
    response = client.get(reverse("pwa_service_worker"))

    assert response["Service-Worker-Allowed"] == "/"


def test_sw_post_is_405(client: Client) -> None:
    assert client.post(reverse("pwa_service_worker")).status_code == 405


def test_sw_names_the_offline_url_and_both_cache_names(client: Client) -> None:
    text = _sw_text(client)

    # The install step needs the page, and activate needs both current names to
    # know which caches to keep.
    assert 'const OFFLINE_URL = "/offline/";' in text
    assert 'const OFFLINE_CACHE = "badshah-offline-v1";' in text
    assert 'const STATIC_CACHE = "badshah-static-v1";' in text


@override_settings(STATIC_URL="/assets/")
def test_sw_static_prefix_follows_static_url_setting(client: Client) -> None:
    text = _sw_text(client)

    # The worker's static rule is read from the setting at request time, so a
    # deploy that moves its static files moves the rule with them.
    assert 'const STATIC_PREFIX = "/assets/";' in text


def test_sw_cache_names_follow_the_cache_version_constant(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pwa, "CACHE_VERSION", "77")

    text = _sw_text(client)

    assert 'const OFFLINE_CACHE = "badshah-offline-v77";' in text
    assert 'const STATIC_CACHE = "badshah-static-v77";' in text


def test_sw_lists_admin_staff_and_payments_as_never_intercepted(client: Client) -> None:
    text = _sw_text(client)

    # Staff, admin and payment pages must never be answered from a stored copy.
    assert '"/admin/"' in text
    assert '"/staff/"' in text
    assert '"/payments/"' in text


def test_sw_does_not_list_shop_pages_as_never_intercepted(client: Client) -> None:
    text = _sw_text(client)

    # A shop page must still get its offline fallback, so it is not on that list.
    assert '"/products/"' not in text
    assert '"/cart/"' not in text
    assert '"/checkout/"' not in text


def test_sw_has_exactly_two_cache_write_sites_besides_install(client: Client) -> None:
    text = _sw_text(client)

    # One write for the offline refresh, one for the static refresh; install
    # uses cache.add() and nothing else may ever store anything.
    assert text.count("cache.put(") == 2
    assert text.count("cache.add(") == 1


def test_sw_skips_waiting_and_claims_clients(client: Client) -> None:
    text = _sw_text(client)

    assert "self.skipWaiting()" in text
    assert "self.clients.claim()" in text


def test_sw_deletes_old_caches_with_the_prefix(client: Client) -> None:
    text = _sw_text(client)

    # Only this app's own old caches go, so the filter needs the prefix and the
    # current names, and each survivor is deleted one by one.
    assert "name.startsWith(CACHE_PREFIX)" in text
    assert "!CURRENT_CACHES.includes(name)" in text
    assert "caches.delete(name)" in text


def test_sw_falls_back_only_when_the_fetch_fails(client: Client) -> None:
    text = _sw_text(client)

    # The network answer is returned untouched inside the try block, so a
    # redirect or an HTTP error passes through; the stored page is served from
    # the catch block, which only a failed fetch reaches.
    assert "const response = await fetch(event.request);" in text
    assert "return response;" in text
    assert "const offlineCache = await caches.open(OFFLINE_CACHE);" in text
    assert "const stored = await offlineCache.match(OFFLINE_URL);" in text
    assert "return stored || Response.error();" in text
    assert text.index("} catch (error) {") > text.index("return response;")


def test_sw_stores_a_static_copy_only_when_the_response_is_ok(client: Client) -> None:
    text = _sw_text(client)

    # A 404 or a 500 for a stylesheet must never replace the copy that works.
    assert 'if (!response.ok || response.type !== "basic") {' in text


def test_sw_stores_a_static_copy_only_when_the_response_is_basic(client: Client) -> None:
    text = _sw_text(client)

    # An opaque cross-origin reply has no readable body, so storing it would
    # hand the page a broken file.
    assert 'response.type !== "basic"' in text


def test_sw_leaves_non_get_requests_alone(client: Client) -> None:
    text = _sw_text(client)

    # A form post must reach Django, never the worker.
    assert 'if (request.method !== "GET") {' in text


def test_sw_leaves_other_origins_alone(client: Client) -> None:
    text = _sw_text(client)

    # The payment gateway's own page and the CDN are not this shop's business.
    assert "if (url.origin !== self.location.origin) {" in text


def test_sw_output_has_no_template_syntax_left(client: Client) -> None:
    text = _sw_text(client)

    # A stray {{ or {% would be a JavaScript syntax error in the browser.
    assert "{{" not in text
    assert "}}" not in text
    assert "{%" not in text


def test_sw_sets_no_cookie_and_no_vary_cookie(client: Client) -> None:
    response = client.get(reverse("pwa_service_worker"))

    assert response.cookies == {}
    assert "Cookie" not in response.get("Vary", "")
