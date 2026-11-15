"""The Content-Security-Policy: one fresh nonce and one policy per response (D93).

REVIEW CAREFULLY: this header decides what a browser may run on the shop's
pages, so these tests pin the whole policy and not only its presence.
"""

from __future__ import annotations

import re

import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.core.csp import (
    PAYMENT_API_ORIGIN,
    PAYMENT_CHECKOUT_ORIGIN,
    PAYMENT_IMAGE_ORIGIN,
    PAYMENT_LOG_ORIGIN,
    PERMISSIONS_POLICY,
    POLICY_HEADER,
    REPORT_ONLY_HEADER,
    ContentSecurityPolicyMiddleware,
    csp_nonce,
)
from apps.core.models import ShopSettings
from apps.payments import razorpay_gateway
from apps.payments.tests.helpers import factory_online_order

pytestmark = pytest.mark.django_db

NONCE_PATTERN = re.compile(r"'nonce-([^']+)'")


@pytest.fixture(autouse=True)
def _shop_settings_row():
    """One settings row and an empty cache, so no test sees a stale cache."""
    cache.clear()
    ShopSettings.load()
    yield
    cache.clear()


def _directives(response: HttpResponse) -> dict[str, str]:
    """The policy of one response as {directive name: its sources as text}."""
    pairs: dict[str, str] = {}
    for part in response[POLICY_HEADER].split("; "):
        # partition splits at the first space only, so the sources stay whole.
        name, _separator, sources = part.partition(" ")
        pairs[name] = sources
    return pairs


def _sources(response: HttpResponse, name: str) -> list[str]:
    """The sources of one directive, as a list of single words."""
    return _directives(response).get(name, "").split()


def _header_nonce(response: HttpResponse) -> str:
    """The nonce this response's own header carries."""
    match = NONCE_PATTERN.search(response[POLICY_HEADER])
    assert match is not None
    return match.group(1)


def test_html_page_carries_the_policy_header(client: Client) -> None:
    response = client.get(reverse("home"))

    assert response.status_code == 200
    header = response[POLICY_HEADER]
    assert header.startswith("default-src 'self'")
    # One header, one policy: a second default-src would mean two policies.
    assert header.count("default-src") == 1


def test_policy_header_is_on_json_redirect_and_404_responses(client: Client) -> None:
    json_response = client.get("/health/")
    redirect_response = client.get(reverse("checkout"))
    missing_response = client.get("/definitely-not-a-page/")

    assert json_response["Content-Type"].startswith("application/json")
    assert json_response[POLICY_HEADER]
    assert redirect_response.status_code == 302
    assert redirect_response[POLICY_HEADER]
    assert missing_response.status_code == 404
    assert missing_response[POLICY_HEADER]


def test_script_src_allows_self_nonce_unsafe_eval_and_razorpay_only(client: Client) -> None:
    response = client.get(reverse("home"))
    sources = _sources(response, "script-src")
    nonce = _header_nonce(response)

    assert "'self'" in sources
    assert f"'nonce-{nonce}'" in sources
    assert "'unsafe-eval'" in sources
    assert PAYMENT_CHECKOUT_ORIGIN in sources
    # Nothing else off-site may be a script.
    assert [source for source in sources if source.startswith("http")] == [PAYMENT_CHECKOUT_ORIGIN]


def test_script_src_never_allows_unsafe_inline(client: Client) -> None:
    response = client.get(reverse("home"))

    assert "'unsafe-inline'" not in _sources(response, "script-src")
    # Styles are the one place it is allowed, for htmx and the gateway page.
    assert "'unsafe-inline'" in _sources(response, "style-src")


def test_connect_frame_and_img_sources_name_razorpay(client: Client) -> None:
    response = client.get(reverse("home"))
    img_sources = _sources(response, "img-src")

    assert _sources(response, "connect-src") == [
        "'self'",
        PAYMENT_API_ORIGIN,
        PAYMENT_LOG_ORIGIN,
    ]
    assert _sources(response, "frame-src") == [PAYMENT_API_ORIGIN, PAYMENT_CHECKOUT_ORIGIN]
    assert img_sources[0] == "'self'"
    assert "data:" in img_sources
    assert PAYMENT_IMAGE_ORIGIN in img_sources


def test_worker_manifest_font_sources_are_self(client: Client) -> None:
    response = client.get(reverse("home"))

    assert _sources(response, "worker-src") == ["'self'"]
    assert _sources(response, "manifest-src") == ["'self'"]
    assert _sources(response, "font-src") == ["'self'"]


def test_object_base_form_and_frame_ancestors_are_locked(client: Client) -> None:
    response = client.get(reverse("home"))

    assert _sources(response, "object-src") == ["'none'"]
    assert _sources(response, "base-uri") == ["'self'"]
    assert _sources(response, "form-action") == ["'self'"]
    assert _sources(response, "frame-ancestors") == ["'none'"]


def test_nonce_changes_on_every_request(client: Client) -> None:
    first = _header_nonce(client.get("/health/"))
    second = _header_nonce(client.get("/health/"))

    assert first
    assert second
    assert first != second


def test_nonce_in_header_matches_the_context() -> None:
    request = RequestFactory().get("/")
    middleware = ContentSecurityPolicyMiddleware(lambda _request: HttpResponse("ok"))

    response = middleware(request)

    # The context processor and the header read the one nonce on this request.
    assert csp_nonce(request) == {"csp_nonce": _header_nonce(response)}
    assert csp_nonce(request)["csp_nonce"]


def test_razorpay_inline_script_carries_the_header_nonce(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway page is a full page render, so the context processor runs."""
    settings.PAYMENT_PROVIDER = "razorpay"
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "secret"
    # The gateway's one HTTP call is replaced, so no test touches the network.
    monkeypatch.setattr(razorpay_gateway, "_post_json", lambda url, payload: {"id": "order_test_1"})
    order = factory_online_order()
    client = Client()
    client.force_login(order.user)

    response = client.post(reverse("payment_start", args=[order.order_number]))
    html = response.content.decode()

    assert response.status_code == 200
    nonce = _header_nonce(response)
    assert nonce
    # The inline block carries exactly the nonce the header names.
    assert f'<script nonce="{nonce}">' in html
    assert html.count(f'nonce="{nonce}"') == 1


def test_report_only_setting_switches_the_header_name(settings, client: Client) -> None:
    settings.CSP_REPORT_ONLY = True

    response = client.get(reverse("home"))

    assert REPORT_ONLY_HEADER in response
    assert POLICY_HEADER not in response
    assert response[REPORT_ONLY_HEADER].startswith("default-src 'self'")


def test_extra_img_sources_setting_is_added(settings, client: Client) -> None:
    settings.CSP_EXTRA_IMG_SOURCES = ["https://media.example.com"]

    response = client.get(reverse("home"))

    assert "https://media.example.com" in _sources(response, "img-src")
    # The setting may never widen the script rule.
    assert "https://media.example.com" not in _sources(response, "script-src")


def test_existing_header_is_not_overwritten() -> None:
    existing = "default-src 'none'"
    middleware = ContentSecurityPolicyMiddleware(
        lambda _request: HttpResponse("ok", headers={POLICY_HEADER: existing})
    )

    response = middleware(RequestFactory().get("/"))

    assert response[POLICY_HEADER] == existing


def test_permissions_policy_header_is_set(client: Client) -> None:
    header = client.get(reverse("home"))["Permissions-Policy"]

    assert header == PERMISSIONS_POLICY
    assert "camera=()" in header
    # No "payment" entry: it could break a UPI app on a phone.
    assert "payment" not in header


def test_referrer_policy_stays_same_origin(client: Client) -> None:
    assert client.get(reverse("home"))["Referrer-Policy"] == "same-origin"


def test_service_worker_keeps_its_own_cache_control_with_the_policy(client: Client) -> None:
    response = client.get(reverse("pwa_service_worker"))

    assert response.status_code == 200
    assert "no-cache" in response["Cache-Control"]
    assert "public" not in response["Cache-Control"]
    assert _header_nonce(response)
