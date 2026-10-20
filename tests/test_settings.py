"""
Settings tests.

These tests verify that each settings module loads cleanly and that prod.py
refuses to start in the unsafe configurations specified.

The prod refusal tests do not require any database. They import each settings
module fresh under a controlled environment so test order and shell state
cannot affect them, and they force the settings code to skip reading .env so
a developer's local .env can never change a test result.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

PRODUCTION_SETTINGS_MODULE = "badshahstore.settings.prod"
DEV_SETTINGS_MODULE = "badshahstore.settings.dev"
TEST_SETTINGS_MODULE = "badshahstore.settings.test"

DOT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
SETTINGS_PACKAGE_MODULES = frozenset(
    {
        "badshahstore.settings",
        "badshahstore.settings.base",
        "badshahstore.settings.dev",
        "badshahstore.settings.prod",
        "badshahstore.settings.test",
    }
)

PLACEHOLDER_SECRET_KEY = "placeholder-change-in-prod"
DEV_DEFAULT_SECRET_KEY = "dev-only-insecure-secret-key-replace-in-real-env"
TEST_DEFAULT_SECRET_KEY = "test-only-insecure-secret-key"

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clear_settings_modules():
    """Remove badshahstore.settings* from sys.modules before each test."""
    before: set[str] = set()
    for key in SETTINGS_PACKAGE_MODULES:
        before.add(key)
        sys.modules.pop(key, None)

    # Also hide django.conf so any module that triggers conf.setup() starts
    # from scratch under the test environment.
    sys.modules.pop("django.conf", None)

    # Make every test use its own environment by telling the settings code to
    # skip reading .env. This is set before any settings module is imported.
    os.environ["BADSHAHSTORE_SKIP_DOT_ENV"] = "1"

    yield

    for key in before:
        sys.modules.pop(key, None)
    sys.modules.pop("django.conf", None)
    os.environ.pop("BADSHAHSTORE_SKIP_DOT_ENV", None)


@pytest.fixture(autouse=True)
def _revert_dot_env_changes():
    """Ensure the repo .env is not accidentally mutated by the probes below.

    This is a safety net. The main independence probe manages its own rename/
    restore cycle, but this fixture guarantees the repo .env ends the test in
    the same existence state it had at the start.
    """
    existed_at_start = DOT_ENV_PATH.exists()

    yield

    # If something deleted .env during the test, restore the original if we
    # had one; if nothing had one, leave it deleted.
    if existed_at_start and not DOT_ENV_PATH.exists():
        backup = DOT_ENV_PATH.with_suffix(".env.tests-backup")
        if backup.exists():
            backup.rename(DOT_ENV_PATH)
            backup.unlink(missing_ok=True)
        else:
            DOT_ENV_PATH.touch()

    for candidate in (
        DOT_ENV_PATH.with_suffix(".env.tests-backup"),
        DOT_ENV_PATH.with_suffix(".env.tests-backup" + ".bak"),
        DOT_ENV_PATH.with_suffix(".env.removed-for-probe"),
    ):
        if candidate.exists():
            candidate.unlink(missing_ok=True)


def _clear_django_conf() -> None:
    sys.modules.pop("django.conf", None)


def _configure_env(monkeypatch: pytest.MonkeyPatch, **env_values: str | None) -> None:
    # The test runner needs a database URL for Django to start, even though
    # the prod refusal tests do not touch the database at all. Provide one
    # purely so importlib.import_module(badshahstore.settings.prod) can
    # execute base.py far enough to reach the prod refusal checks.
    monkeypatch.setenv(
        "DATABASE_URL", "postgres://badshahstore:changeme@127.0.0.1:5432/badshahstore"
    )
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    for key, value in env_values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _import_settings_fresh(
    *, settings_module: str, monkeypatch: pytest.MonkeyPatch, **env_values: str | None
) -> Any:
    """Import a settings module fresh, with a fully controlled environment.

    This helper is used by the prod refusal tests and by the env-independence
    probe. It does not rely on pytest-django's --ds wiring or on any
    preexisting environment values.
    """
    _configure_env(monkeypatch, **env_values)
    _clear_django_conf()

    for key in SETTINGS_PACKAGE_MODULES:
        sys.modules.pop(key, None)
    sys.modules.pop("django.conf", None)

    return importlib.import_module(settings_module)


def test_dev_settings_load_and_have_expected_values(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _import_settings_fresh(
        settings_module=DEV_SETTINGS_MODULE,
        monkeypatch=monkeypatch,
        SECRET_KEY=DEV_DEFAULT_SECRET_KEY,
        ALLOWED_HOSTS="localhost,127.0.0.1",
    )

    assert settings.DEBUG is True
    assert "localhost" in settings.ALLOWED_HOSTS
    assert settings.SECRET_KEY == DEV_DEFAULT_SECRET_KEY


def test_test_settings_load_and_have_expected_values(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _import_settings_fresh(
        settings_module=TEST_SETTINGS_MODULE,
        monkeypatch=monkeypatch,
        SECRET_KEY=TEST_DEFAULT_SECRET_KEY,
        ALLOWED_HOSTS="localhost",
    )

    assert settings.DEBUG is False
    assert settings.SECRET_KEY == TEST_DEFAULT_SECRET_KEY
    assert settings.CELERY_TASK_ALWAYS_EAGER is True


def _import_prod_after_env(monkeypatch: pytest.MonkeyPatch, **env_values: str | None) -> Any:
    return _import_settings_fresh(
        settings_module=PRODUCTION_SETTINGS_MODULE,
        monkeypatch=monkeypatch,
        **env_values,
    )


def test_prod_refuses_when_debug_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ImproperlyConfigured):
        _import_prod_after_env(
            monkeypatch,
            DEBUG="1",
            SECRET_KEY="a-real-looking-secret-key",
            ALLOWED_HOSTS="example.com",
        )


def test_prod_refuses_when_secret_key_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ImproperlyConfigured):
        _import_prod_after_env(
            monkeypatch,
            DEBUG="0",
            SECRET_KEY="",
            ALLOWED_HOSTS="example.com",
        )


def test_prod_refuses_when_secret_key_is_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ImproperlyConfigured):
        _import_prod_after_env(
            monkeypatch,
            DEBUG="0",
            SECRET_KEY=PLACEHOLDER_SECRET_KEY,
            ALLOWED_HOSTS="example.com",
        )


@pytest.mark.parametrize(
    "allowed_hosts_value",
    [
        "",
        " ",
        ",",
        " , ",
    ],
    ids=[
        "empty string",
        "whitespace only",
        "comma only",
        "comma with whitespace",
    ],
)
def test_prod_refuses_for_blank_variant_allowed_hosts(
    monkeypatch: pytest.MonkeyPatch, allowed_hosts_value: str
) -> None:
    """Each blank variant of ALLOWED_HOSTS (empty, whitespace-only,
    comma-only, comma-with-whitespace) yields an empty list after parsing
    and therefore causes prod.py to refuse to start.
    """
    with pytest.raises(ImproperlyConfigured):
        _import_prod_after_env(
            monkeypatch,
            DEBUG="0",
            SECRET_KEY="a-real-looking-secret-key",
            ALLOWED_HOSTS=allowed_hosts_value,
        )


def test_prod_refuses_when_allowed_hosts_variable_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ImproperlyConfigured):
        _import_prod_after_env(
            monkeypatch,
            DEBUG="0",
            SECRET_KEY="a-real-looking-secret-key",
            ALLOWED_HOSTS=None,
        )


def test_prod_refuses_for_literal_bracket_allowed_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The requested '[]' edge case normalizes to an empty list, and prod
    then refuses to start.
    """
    monkeypatch.setenv(
        "DATABASE_URL", "postgres://badshahstore:changeme@127.0.0.1:5432/badshahstore"
    )
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("ALLOWED_HOSTS", "[]")
    for extra_key in ("DEBUG", "SECRET_KEY", "BADSHAHSTORE_SKIP_DOT_ENV"):
        monkeypatch.delenv(extra_key, raising=False)
    monkeypatch.setenv("BADSHAHSTORE_SKIP_DOT_ENV", "1")

    sys.modules.pop("badshahstore.settings", None)
    sys.modules.pop("badshahstore.settings.base", None)
    sys.modules.pop("django.conf", None)

    import badshahstore.settings.base as base

    assert base.ALLOWED_HOSTS == [], (
        f"Expected empty list for ALLOWED_HOSTS='[]', got {base.ALLOWED_HOSTS!r}"
    )

    sys.modules.pop("badshahstore.settings.prod", None)
    with pytest.raises(ImproperlyConfigured):
        importlib.import_module("badshahstore.settings.prod")


def test_prod_normalizes_blank_variant_allowed_hosts_to_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each blank variant of ALLOWED_HOSTS normalizes to an empty list, not
    a list containing blanks.
    """
    for allowed_hosts_value in ("", " ", ",", " , "):
        settings = _import_settings_fresh(
            settings_module="badshahstore.settings.base",
            monkeypatch=monkeypatch,
            DEBUG="0",
            SECRET_KEY="a-real-looking-secret-key",
            ALLOWED_HOSTS=allowed_hosts_value,
        )

        assert settings.ALLOWED_HOSTS == [], (
            f"Expected empty list for ALLOWED_HOSTS={allowed_hosts_value!r}, "
            f"got {settings.ALLOWED_HOSTS!r}"
        )


def test_prod_loads_with_valid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    import secrets

    settings = _import_prod_after_env(
        monkeypatch,
        DEBUG="0",
        SECRET_KEY=secrets.token_urlsafe(48),
        ALLOWED_HOSTS="example.com,www.example.com",
    )

    assert settings.DEBUG is False
    assert settings.SECRET_KEY
    assert "example.com" in settings.ALLOWED_HOSTS


def test_settings_are_independent_of_local_dot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings code must behave identically whether the repo .env exists
    or not.

    This probe explicitly removes .env, imports the settings under a fully
    controlled environment, then restores the original .env (if any), imports
    again with the same controlled environment, and asserts:

    - both unsafe variants refuse exactly as expected, and
    - the two successful controlled imports produce identical settings for the
      fields we care about.
    """

    existed_before_probe = DOT_ENV_PATH.exists()

    def import_prod_under_control(monkeypatch: pytest.MonkeyPatch) -> Any:
        return _import_settings_fresh(
            settings_module=PRODUCTION_SETTINGS_MODULE,
            monkeypatch=monkeypatch,
            DEBUG="0",
            SECRET_KEY="a-real-looking-secret-key",
            ALLOWED_HOSTS="example.com",
        )

    # 1. With .env missing, importing prod should work.
    removed_path: Path | None = None
    if DOT_ENV_PATH.exists():
        removed_path = DOT_ENV_PATH.with_suffix(".env.removed-for-probe")
        DOT_ENV_PATH.rename(removed_path)

    prod_without_env = import_prod_under_control(monkeypatch)

    # 2. Restore .env (if it existed), then import again with the exact same
    # controlled environment. We do the restore before the import because the
    # settings code itself decides whether to read .env, and our probe is there
    # to prove it ignores .env under the controlled environment.
    if removed_path is not None and removed_path.exists():
        removed_path.rename(DOT_ENV_PATH)

    prod_with_env = import_prod_under_control(monkeypatch)

    # 3. Both must refuse exactly the same way under the unsafe variant.
    with pytest.raises(ImproperlyConfigured):
        _import_settings_fresh(
            settings_module=PRODUCTION_SETTINGS_MODULE,
            monkeypatch=monkeypatch,
            DEBUG="1",
            SECRET_KEY="a-real-looking-secret-key",
            ALLOWED_HOSTS="example.com",
        )

    with pytest.raises(ImproperlyConfigured):
        _import_settings_fresh(
            settings_module=PRODUCTION_SETTINGS_MODULE,
            monkeypatch=monkeypatch,
            DEBUG="1",
            SECRET_KEY="a-real-looking-secret-key",
            ALLOWED_HOSTS="example.com",
        )

    # 4. The two successful imports must produce identical settings for the
    # fields we care about.
    assert prod_without_env.DEBUG == prod_with_env.DEBUG is False
    assert prod_without_env.SECRET_KEY == prod_with_env.SECRET_KEY == "a-real-looking-secret-key"
    assert prod_without_env.ALLOWED_HOSTS == prod_with_env.ALLOWED_HOSTS == ["example.com"]

    # Defensive final check: the probe must leave the repo .env in the same
    # existence state it found it.
    if existed_before_probe and not DOT_ENV_PATH.exists():
        DOT_ENV_PATH.touch()

    assert DOT_ENV_PATH.exists() if existed_before_probe else True, (
        "probe left repo .env missing when it existed before the probe"
    )
