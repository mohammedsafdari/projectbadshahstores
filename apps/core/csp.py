"""The Content-Security-Policy and the Permissions-Policy (D93).

The policy is built in one place, so a test can read the whole rule and a page
can print the same nonce the header carries. The middleware gives every request
a fresh nonce and puts the policy on every response; the context processor hands
that nonce to a template.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any, cast

from django.conf import settings
from django.http import HttpRequest, HttpResponse

# The payment gateway's own browser hosts. They are named here once, so the
# policy, a test and a template test all read the same string (D76 keeps the
# provider detail in one module; this is its browser-side part).
PAYMENT_CHECKOUT_ORIGIN = "https://checkout.razorpay.com"
PAYMENT_API_ORIGIN = "https://api.razorpay.com"
PAYMENT_IMAGE_ORIGIN = "https://*.razorpay.com"
PAYMENT_LOG_ORIGIN = "https://lumberjack.razorpay.com"

# The two header names. The Report-Only one makes a browser report a broken rule
# without blocking anything, which is the safe way to try a new rule.
POLICY_HEADER = "Content-Security-Policy"
REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"

# The browser features the shop never uses. There is deliberately no "payment"
# entry: the Payment Request API could stand in the way of a UPI app on a phone.
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), usb=()"

# How many random bytes one nonce holds. 16 bytes is 128 bits, which no browser
# and no attacker can guess, and it writes out as 22 URL-safe characters.
NONCE_BYTES = 16


def build_policy(nonce: str, extra_img_sources: list[str]) -> str:
    """The whole policy as one header value, directives joined by ``"; "``.

    A pure function: it reads nothing and changes nothing, so a test calls it
    with any nonce it likes.
    """
    # [*list, value] builds a new list from the items plus this one value.
    img_sources = ["'self'", "data:", PAYMENT_IMAGE_ORIGIN, *extra_img_sources]
    directives = [
        "default-src 'self'",
        # 'unsafe-eval' is there for Alpine's standard build, which evaluates
        # the expressions in x-data and @click at run time. The nonce lets the
        # shop's own one inline script (the gateway page) run and nothing else.
        f"script-src 'self' 'nonce-{nonce}' 'unsafe-eval' {PAYMENT_CHECKOUT_ORIGIN}",
        # Styles only: htmx and the gateway's checkout page add inline styles.
        # The script rule above never allows an inline script without a nonce.
        "style-src 'self' 'unsafe-inline'",
        "img-src " + " ".join(img_sources),
        "font-src 'self'",
        f"connect-src 'self' {PAYMENT_API_ORIGIN} {PAYMENT_LOG_ORIGIN}",
        f"frame-src {PAYMENT_API_ORIGIN} {PAYMENT_CHECKOUT_ORIGIN}",
        # The service worker and the web manifest are the shop's own files (D91).
        "worker-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    return "; ".join(directives)


def new_nonce() -> str:
    """A fresh random nonce, written as URL-safe text."""
    return secrets.token_urlsafe(NONCE_BYTES)


def request_nonce(request: HttpRequest) -> str:
    """The nonce this request carries, or ``""`` when the middleware never ran."""
    # getattr with a default keeps a request built by hand (a unit test's
    # RequestFactory request) working instead of raising AttributeError.
    return str(getattr(request, "csp_nonce", ""))


class ContentSecurityPolicyMiddleware:
    """A fresh nonce per request, and the policy on every response (D93)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        # New-style middleware: the object holds the next middleware or the
        # view, and __call__ runs it by hand. Django calls the object once per
        # request, so one instance serves many requests.
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # A new nonce for every request, set before the view runs, so a template
        # can print it and the header can carry the same text.
        nonce = new_nonce()
        # cast("Any", request) tells mypy to trust us: a Django request accepts
        # any attribute, but mypy only knows the ones Django declares.
        cast("Any", request).csp_nonce = nonce
        response = self.get_response(request)
        # The setting is read per request, so a test can switch it.
        header = REPORT_ONLY_HEADER if settings.CSP_REPORT_ONLY else POLICY_HEADER
        # A response that already carries this header keeps it: another layer
        # may know better than this one.
        if header not in response:
            response[header] = build_policy(nonce, list(settings.CSP_EXTRA_IMG_SOURCES))
        # Every response, whatever its status, gets the same permissions rule.
        response["Permissions-Policy"] = PERMISSIONS_POLICY
        return response


def csp_nonce(request: HttpRequest) -> dict[str, str]:
    """The template context processor: this request's own nonce (D93)."""
    return {"csp_nonce": request_nonce(request)}
