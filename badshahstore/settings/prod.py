"""
Badshah Store — production settings.

Refuses to start if:
- DEBUG is on
- SECRET_KEY is empty or the known placeholder value
- ALLOWED_HOSTS is empty

Production controls for secure cookies, HSTS and SSL redirect are turned on
by environment toggles so they can be enabled only behind a real reverse proxy
with HTTPS termination.
"""

from django.core.exceptions import ImproperlyConfigured

from badshahstore.settings.base import env
from badshahstore.settings.base import *  # noqa: F401,F403


if DEBUG:
    raise ImproperlyConfigured(
        "Production settings must have DEBUG=False. "
        "Set DEBUG=0 or remove it."
    )

if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "Production settings require non-empty ALLOWED_HOSTS."
    )

# Be explicit about the empty-string edge case requested in the spec.
if ALLOWED_HOSTS == []:
    raise ImproperlyConfigured(
        "Production settings require non-empty ALLOWED_HOSTS."
    )

KNOWN_PLACEHOLDER_SECRET_KEYS = {
    "placeholder-change-in-prod",
    "dev-only-insecure-secret-key-replace-in-real-env",
}

if not SECRET_KEY or SECRET_KEY in KNOWN_PLACEHOLDER_SECRET_KEYS:
    raise ImproperlyConfigured(
        "Production settings require a real SECRET_KEY. "
        "Empty and known placeholder values are not allowed."
    )

# Secure defaults for production, enabled via environment toggles.
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS", default=31536000)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Trust proxy headers only when the environment explicitly sets them.
if env("SECURE_PROXY_SSL_HEADER", default=()):
    SECURE_PROXY_SSL_HEADER = env("SECURE_PROXY_SSL_HEADER")
    USE_X_FORWARDED_HOST = env("USE_X_FORWARDED_HOST", default=True)
    USE_X_FORWARDED_PROTO = env("USE_X_FORWARDED_PROTO", default=True)
