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

# Order messages print to the console in development, never to a real phone or
# inbox (D69). base.py defaults both switches to off.
ORDER_SMS_ENABLED = True
ORDER_EMAIL_ENABLED = True

# A local development site has no Razorpay account, so it uses the fake
# gateway by default. The checkout offers the three common methods.
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", default="fake")
PAYMENT_ALLOW_FAKE_GATEWAY = True
# The star import above skips names starting with "_", so the cleanup helper
# is not available here; a plain list is enough for development.
PAYMENT_ENABLED_METHODS = env.list("PAYMENT_ENABLED_METHODS", default=["upi", "card", "netbanking"])

LOGIN_URL = "/accounts/login/"
