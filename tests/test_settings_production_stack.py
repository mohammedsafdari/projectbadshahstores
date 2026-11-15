"""The production stack: R2 media, HSTS, the health exemption and storage (D94).

The helpers and the two autouse cleanup fixtures are imported from
``tests/test_settings.py``, not copied: that file already knows how to import a
settings module fresh with a controlled environment and how to leave
``sys.modules`` as it found it, so a settings import here cannot change the
result of another test.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from tests.test_settings import (  # noqa: F401
    _clear_settings_modules,
    _import_prod_after_env,
    _revert_dot_env_changes,
)

# The four variables R2 needs once a bucket is named.
R2_REQUIRED_NAMES = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_MEDIA_HOST",
)

# A complete but fake R2 configuration. Read the values as placeholders: none of
# them is a real account id or credential.
R2_ENV = {
    "R2_BUCKET_NAME": "badshah-media",
    "R2_ACCOUNT_ID": "exampleaccountid0123456789",
    "R2_ACCESS_KEY_ID": "example-access-key",
    "R2_SECRET_ACCESS_KEY": "example-secret-key",
    "R2_MEDIA_HOST": "media.example.com",
}

MANIFEST_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"


def _prod(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> Any:
    """Import prod.py fresh with a valid environment plus the given overrides.

    Every variable this file cares about is set or deleted first, so a value in
    the machine's own environment can never leak into a result.
    """
    # base.py reads .env only when this is unset; the imported fixture sets it
    # too, and setting it here keeps this file safe on its own.
    monkeypatch.setenv("BADSHAHSTORE_SKIP_DOT_ENV", "1")

    env_values: dict[str, str | None] = {
        "DEBUG": "0",
        "SECRET_KEY": "a-real-looking-secret-key",
        "ALLOWED_HOSTS": "example.com",
        "SECURE_HSTS_SECONDS": None,
        "SECURE_SSL_REDIRECT": None,
        "CSP_EXTRA_IMG_SOURCES": None,
        "R2_BUCKET_NAME": None,
        "R2_ACCOUNT_ID": None,
        "R2_ACCESS_KEY_ID": None,
        "R2_SECRET_ACCESS_KEY": None,
        "R2_MEDIA_HOST": None,
    }
    env_values.update(overrides)
    return _import_prod_after_env(monkeypatch, **env_values)


# ---------------------------------------------------------------------------
# The media store
# ---------------------------------------------------------------------------
def test_prod_uses_file_storage_when_the_r2_bucket_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _prod(monkeypatch)

    assert settings.STORAGES["default"] == {
        "BACKEND": "django.core.files.storage.FileSystemStorage"
    }
    # The local media folder exactly as base.py has it.
    assert settings.MEDIA_URL == "media/"


def test_prod_uses_s3_storage_when_r2_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _prod(monkeypatch, **R2_ENV)

    entry = settings.STORAGES["default"]
    assert entry["BACKEND"] == "storages.backends.s3.S3Storage"
    # The dotted path must really import, so a missing dependency fails here
    # instead of on the first photo upload.
    assert import_string(entry["BACKEND"]).__name__ == "S3Storage"
    assert entry["OPTIONS"]["bucket_name"] == "badshah-media"
    assert entry["OPTIONS"]["access_key"] == "example-access-key"
    assert entry["OPTIONS"]["secret_key"] == "example-secret-key"


def test_r2_endpoint_is_built_from_the_account_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _prod(monkeypatch, **R2_ENV)
    options = settings.STORAGES["default"]["OPTIONS"]

    assert options["endpoint_url"] == (
        "https://exampleaccountid0123456789.r2.cloudflarestorage.com"
    )
    assert options["region_name"] == "auto"
    assert options["signature_version"] == "s3v4"


def test_r2_files_are_public_and_cached_for_a_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _prod(monkeypatch, **R2_ENV)
    options = settings.STORAGES["default"]["OPTIONS"]

    assert options["custom_domain"] == "media.example.com"
    assert options["url_protocol"] == "https:"
    # The photos are public, so no link is signed.
    assert options["querystring_auth"] is False
    # A new picture is a new name, so nothing is ever overwritten.
    assert options["file_overwrite"] is False
    # R2 has no object ACLs.
    assert options["default_acl"] is None
    assert options["object_parameters"] == {"CacheControl": "public, max-age=31536000, immutable"}


def test_media_url_points_at_the_media_host(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _prod(monkeypatch, **R2_ENV)

    assert settings.MEDIA_URL == "https://media.example.com/"


def test_media_origin_is_added_to_the_csp_image_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _prod(monkeypatch, **R2_ENV)

    assert settings.CSP_EXTRA_IMG_SOURCES == ["https://media.example.com"]


def test_media_origin_is_not_added_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _prod(
        monkeypatch,
        CSP_EXTRA_IMG_SOURCES="https://media.example.com",
        **R2_ENV,
    )

    assert settings.CSP_EXTRA_IMG_SOURCES == ["https://media.example.com"]


@pytest.mark.parametrize("missing_name", R2_REQUIRED_NAMES)
def test_prod_refuses_a_partial_r2_configuration(
    monkeypatch: pytest.MonkeyPatch, missing_name: str
) -> None:
    env_values: dict[str, str | None] = dict(R2_ENV)
    env_values[missing_name] = ""

    with pytest.raises(ImproperlyConfigured) as error:
        _prod(monkeypatch, **env_values)

    # The refusal names the variable that is missing, not just the bucket.
    assert missing_name in str(error.value)


@pytest.mark.parametrize(
    "media_host",
    ["https://media.example.com", "media.example.com/x"],
)
def test_prod_refuses_a_media_host_with_a_scheme_or_a_slash(
    monkeypatch: pytest.MonkeyPatch, media_host: str
) -> None:
    env_values: dict[str, str | None] = dict(R2_ENV)
    env_values["R2_MEDIA_HOST"] = media_host

    with pytest.raises(ImproperlyConfigured) as error:
        _prod(monkeypatch, **env_values)

    assert "R2_MEDIA_HOST" in str(error.value)


def test_static_files_keep_the_manifest_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    without_r2 = _prod(monkeypatch)
    assert without_r2.STORAGES["staticfiles"]["BACKEND"] == MANIFEST_STORAGE

    with_r2 = _prod(monkeypatch, **R2_ENV)
    assert with_r2.STORAGES["staticfiles"]["BACKEND"] == MANIFEST_STORAGE


# ---------------------------------------------------------------------------
# HSTS and the SSL redirect
# ---------------------------------------------------------------------------
def test_hsts_defaults_to_one_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _prod(monkeypatch)

    assert settings.SECURE_HSTS_SECONDS == 3600
    # No includeSubDomains and no preload: prod.py sets neither switch, so
    # Django's own defaults (both off) apply.
    assert "SECURE_HSTS_INCLUDE_SUBDOMAINS" not in vars(settings)
    assert "SECURE_HSTS_PRELOAD" not in vars(settings)


def test_hsts_seconds_can_be_raised_by_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _prod(monkeypatch, SECURE_HSTS_SECONDS="31536000")

    assert settings.SECURE_HSTS_SECONDS == 31536000


def test_health_address_is_exempt_from_the_ssl_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _prod(monkeypatch)

    assert settings.SECURE_REDIRECT_EXEMPT == [r"^health/$"]
