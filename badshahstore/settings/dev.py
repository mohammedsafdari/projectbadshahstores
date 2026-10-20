"""
Badshah Store — development settings.

Default environment for manage.py, wsgi.py and asgi.py. Docker and local
host-run commands use this unless DJANGO_SETTINGS_MODULE is changed.

This module intentionally supplies insecure development defaults so the app
can run locally without real credentials. Do not use this module in any
environment where real traffic or real secrets are involved.
"""

from badshahstore.settings.base import *  # noqa: F403

DEBUG = True

# Dev-only convenience defaults. These make local development usable, but
# they are not production values.
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "testserver",
]

SECRET_KEY = env(
    "SECRET_KEY",
    default="dev-only-insecure-secret-key-replace-in-real-env",
)

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

LOGIN_URL = "/accounts/login/"
