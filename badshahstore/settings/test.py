"""
Badshah Store — test settings.

Uses PostgreSQL from the environment (same DATABASE_URL pattern as dev).
Do not fall back to SQLite; our locking/constraint tests need Postgres.

pytest-django adds the test_ prefix to the database name automatically, so
this module does not override the database name here.
"""

from badshahstore.settings.base import *  # noqa: F401,F403


DEBUG = False

SECRET_KEY = env(
    "SECRET_KEY",
    default="test-only-insecure-secret-key",
)

# Locmem cache is fine for tests and keeps Redis out of the test runner.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "badshahstore-test",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_STORE_EAGER_RESULT = False
CELERY_TASK_IGNOREResult = True

# Fast password hashing for tests.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
