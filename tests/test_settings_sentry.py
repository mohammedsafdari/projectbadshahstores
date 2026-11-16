"""Sentry starts in production only, with a DSN, and never sends personal data (D96).

The helpers and the two autouse cleanup fixtures are imported from
``tests/test_settings.py``, exactly as ``tests/test_settings_production_stack.py``
does, so an import here cannot change another test's result. ``sentry_sdk.init``
is replaced with a recorder, so no real client ever starts and nothing is sent.
"""

from __future__ import annotations

from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

from tests.test_settings import (  # noqa: F401
    _clear_settings_modules,
    _import_prod_after_env,
    _revert_dot_env_changes,
)

# A fake DSN in the shape Sentry uses. It is not a real project.
FAKE_DSN = "https://examplekey@o0.ingest.sentry.io/0"


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every sentry_sdk.init call instead of starting a client."""
    calls: list[dict[str, Any]] = []

    def _record(**options: Any) -> None:
        calls.append(options)

    monkeypatch.setattr(sentry_sdk, "init", _record)
    return calls


def _prod(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> Any:
    """Import prod.py fresh with a valid environment plus the given overrides."""
    monkeypatch.setenv("BADSHAHSTORE_SKIP_DOT_ENV", "1")

    env_values: dict[str, str | None] = {
        "DEBUG": "0",
        "SECRET_KEY": "a-real-looking-secret-key",
        "ALLOWED_HOSTS": "example.com",
        "R2_BUCKET_NAME": None,
        "SENTRY_DSN": None,
        "SENTRY_ENVIRONMENT": None,
        "SENTRY_RELEASE": None,
        "SENTRY_TRACES_SAMPLE_RATE": None,
    }
    env_values.update(overrides)
    return _import_prod_after_env(monkeypatch, **env_values)


def _prod_with_sentry(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> Any:
    """Import prod.py with a DSN, so the Sentry block runs."""
    return _prod(monkeypatch, SENTRY_DSN=FAKE_DSN, **overrides)


def test_sentry_is_not_started_without_a_dsn(
    started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prod(monkeypatch)

    # No DSN means the SDK is never imported and init is never called.
    assert started == []


def test_sentry_init_gets_the_dsn_environment_and_release(
    started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prod_with_sentry(
        monkeypatch,
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="abc1234",
    )

    assert len(started) == 1
    options = started[0]
    assert options["dsn"] == FAKE_DSN
    assert options["environment"] == "staging"
    assert options["release"] == "abc1234"


def test_sentry_never_sends_personal_data(
    started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prod_with_sentry(monkeypatch)

    # Cookies and the client address are exactly the personal data the shop
    # holds (a phone number and a delivery address), so it stays on the server.
    assert started[0]["send_default_pii"] is False


def test_sentry_request_bodies_and_local_variables_are_off(
    started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prod_with_sentry(monkeypatch)

    assert started[0]["max_request_body_size"] == "never"
    assert started[0]["include_local_variables"] is False


def test_sentry_uses_the_django_and_celery_integrations(
    started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prod_with_sentry(monkeypatch)

    integrations = started[0]["integrations"]
    assert [type(integration) for integration in integrations] == [
        DjangoIntegration,
        CeleryIntegration,
    ]


def test_sentry_sample_rate_defaults_to_ten_percent_and_can_be_set(
    started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prod_with_sentry(monkeypatch)
    _prod_with_sentry(monkeypatch, SENTRY_TRACES_SAMPLE_RATE="0.5")

    assert started[0]["traces_sample_rate"] == 0.1
    # ``env.float`` reads the number rather than the text.
    assert started[1]["traces_sample_rate"] == 0.5


def test_an_empty_release_is_passed_as_none(
    started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prod_with_sentry(monkeypatch, SENTRY_RELEASE="")

    # The SDK wants None for "no release", not an empty string.
    assert started[0]["release"] is None
