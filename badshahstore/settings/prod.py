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

from typing import Any

from django.core.exceptions import ImproperlyConfigured

from badshahstore.settings import base
from badshahstore.settings.base import *  # noqa: F403
from badshahstore.settings.base import env

if DEBUG:
    raise ImproperlyConfigured(
        "Production settings must have DEBUG=False. Set DEBUG=0 or remove it."
    )

if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("Production settings require non-empty ALLOWED_HOSTS.")

# Be explicit about the empty-string edge case requested in the spec.
if ALLOWED_HOSTS == []:
    raise ImproperlyConfigured("Production settings require non-empty ALLOWED_HOSTS.")

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
# HSTS starts at one hour, not a year (D94). The owner raises it with the
# SECURE_HSTS_SECONDS variable once the site is proven on the real server, so a
# mistake in the first days costs an hour of HTTPS pinning and not a year. The
# includeSubDomains and preload switches stay off: neither is needed here.
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS", default=3600)

# nginx and the deploy script check the app over plain HTTP, where no
# certificate is needed yet. Django matches this list against the path without
# its leading slash, so "health/" is the address /health/ (D94).
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Trust proxy headers only when the environment explicitly sets them.
if env("SECURE_PROXY_SSL_HEADER", default=()):
    SECURE_PROXY_SSL_HEADER = env("SECURE_PROXY_SSL_HEADER")
    USE_X_FORWARDED_HOST = env("USE_X_FORWARDED_HOST", default=True)

# Production collects static files with ManifestStaticFilesStorage, so every
# name carries a content hash (app.<hash>.css). That is what lets nginx and
# Cloudflare cache a static file for a year without ever serving a stale one
# (D90). Development and test keep Django's plain storage.
# The type is declared on its own line because the media options added below
# hold values that are not strings (false, None and a nested dict), and a plain
# literal here would fix the type from this first entry alone.
STORAGES: dict[str, dict[str, Any]]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Media on Cloudflare R2 — switched by the environment (D94)
# ---------------------------------------------------------------------------

# The bucket name is the switch. Empty means "keep the local media folder":
# staging and a first run behave exactly as base.py says, because nothing below
# this line runs at all.
R2_BUCKET_NAME = env("R2_BUCKET_NAME", default="")

if R2_BUCKET_NAME:
    R2_ACCOUNT_ID = env("R2_ACCOUNT_ID", default="")
    R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", default="")
    R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default="")
    R2_MEDIA_HOST = env("R2_MEDIA_HOST", default="")

    R2_REQUIRED = {
        "R2_ACCOUNT_ID": R2_ACCOUNT_ID,
        "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
        "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
        "R2_MEDIA_HOST": R2_MEDIA_HOST,
    }
    # A list comprehension with an `if` filters in one step: it walks the dict
    # and keeps only the names whose value is blank, so one refusal can name
    # every missing variable instead of the owner finding them one at a time.
    missing = [name for name, value in R2_REQUIRED.items() if not value]
    if missing:
        raise ImproperlyConfigured(
            "R2_BUCKET_NAME is set, so these variables must be non-empty: "
            + ", ".join(missing)
            + "."
        )

    # R2_MEDIA_HOST is a bare host name such as media.example.com. A scheme or a
    # slash would silently build the broken address below (`https://https://...`
    # or `https://media.example.com/x/`), so both are refused here, at start-up,
    # rather than on a customer's screen.
    if "://" in R2_MEDIA_HOST or "/" in R2_MEDIA_HOST:
        raise ImproperlyConfigured(
            "R2_MEDIA_HOST must be a bare host name such as media.example.com, "
            f"not {R2_MEDIA_HOST!r}."
        )

    # f"..." is a formatted string: the braces are replaced by the variable's
    # value, which is how the two addresses below are built.
    MEDIA_URL = f"https://{R2_MEDIA_HOST}/"

    # The photos are public files in a public bucket read through the custom
    # domain, so the default storage becomes django-storages' S3 backend with
    # R2's own endpoint. Every option has a reason (D94):
    # - endpoint_url: R2 speaks the S3 API at this per-account address.
    # - region_name "auto": R2 has one region and refuses a real one.
    # - signature_version "s3v4": what R2 accepts; the old v2 signing does not.
    # - custom_domain: the bucket is read through Cloudflare, not the API host.
    # - querystring_auth False: every photo is public, so no link is signed.
    # - file_overwrite False: two uploads never share one name by accident.
    # - default_acl None: R2 has no object ACLs, so one would be refused.
    # - object_parameters: a photo's name holds a random uuid, so one name never
    #   means new bytes and the file may be cached for a year (D90).
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": R2_BUCKET_NAME,
            "access_key": R2_ACCESS_KEY_ID,
            "secret_key": R2_SECRET_ACCESS_KEY,
            "endpoint_url": f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            "region_name": "auto",
            "signature_version": "s3v4",
            "custom_domain": R2_MEDIA_HOST,
            "url_protocol": "https:",
            "querystring_auth": False,
            "file_overwrite": False,
            "default_acl": None,
            "object_parameters": {
                "CacheControl": "public, max-age=31536000, immutable",
            },
        },
    }

    # Every product photo is an <img>, so the media origin must be allowed by
    # the Content-Security-Policy's img-src or a browser blocks all of them
    # (D93). [*old, new] copies the old list and appends the new item in one
    # expression; the origin is added only when it is not already there, so a
    # host listed in CSP_EXTRA_IMG_SOURCES too never appears twice.
    #
    # The old list is read from the base module, not from the copied name: this
    # file assigns that same setting name just below, and reading the plain name
    # first would be a name used before its own definition.
    media_origin = f"https://{R2_MEDIA_HOST}"
    if media_origin not in base.CSP_EXTRA_IMG_SOURCES:
        CSP_EXTRA_IMG_SOURCES = [*base.CSP_EXTRA_IMG_SOURCES, media_origin]
