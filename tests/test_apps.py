"""
Check that every Slice 4 app (except accounts) is installed and loads without
error. The accounts app is intentionally not installed yet; Slice 5 adds it
together with AUTH_USER_MODEL. Adding it earlier would make Django create the
default User table, which we do not want because we will use a custom user model.
"""

from django.apps import apps
from django.conf import settings


def test_slice_4_apps_are_installed():
    """Every Slice 4 app shell must be in INSTALLED_APPS and load cleanly."""
    installed_labels = {app_config.label for app_config in apps.get_app_configs()}

    expected_labels = set(settings.SLICE_4_INSTALLED_APP_LABELS)
    assert expected_labels <= installed_labels, (
        f"Expected Slice 4 apps to be installed, but missing: {expected_labels - installed_labels}"
    )


def test_accounts_app_is_installed():
    """accounts is installed together with AUTH_USER_MODEL in Slice 5."""
    installed_labels = {app_config.label for app_config in apps.get_app_configs()}
    assert "accounts" in installed_labels
