"""
Badshah Store — Django settings base.

Shared settings for all environments.
Timezone/locale are Django settings, not shop settings.

Environment variables are the source of truth. django-environ reads them
through env() and env.db(). There are no hardcoded credentials in base.

The settings tests force the environment to skip reading .env so a
developer's local .env can never change a test result.
"""

from pathlib import Path

import environ
import os

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env_skip_dot_env() -> bool:
    """Return True when the process has opted out of reading .env.

    This is set by the settings tests so a developer's local .env can never
    change a test result, even when a test removes the relevant variables.
    """
    return os.environ.get("BADSHAHSTORE_SKIP_DOT_ENV") == "1"


env = environ.Env(
    # Read booleans, ints, floats and lists from env as needed.
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    SECURE_SSL_REDIRECT=(bool, False),
    SECURE_HSTS_SECONDS=(int, 0),
    SESSION_COOKIE_SECURE=(bool, True),
    CSRF_COOKIE_SECURE=(bool, True),
    USE_X_FORWARDED_HOST=(bool, False),
    USE_X_FORWARDED_PROTO=(bool, False),
    SECURE_PROXY_SSL_HEADER=(tuple, ()),
)

# Only read .env when the environment says it is safe. The settings tests
# set BADSHAHSTORE_SKIP_DOT_ENV=1 so a local .env can never affect a test
# result, even when a test removes the relevant variables.
if not _env_skip_dot_env() and (BASE_DIR / ".env").is_file():
    env.read_env(BASE_DIR / ".env", overwrite=False)


def _clean_host_list(value):
    """Normalize ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS values.

    Any blank, whitespace-only, comma-only or '[]' value yields an empty
    list. Real entries are stripped; blanks among them are dropped. Any
    literal '[]' token anywhere in the value is treated as blank.
    """
    if value in ("", "[]"):
        return []

    if isinstance(value, str) and not value.strip():
        return []

    raw_items = (
        [item.strip() for item in value.split(",")]
        if isinstance(value, str)
        else [str(item).strip() for item in value]
    )

    cleaned = []
    for item in raw_items:
        if not item:
            continue
        if item == "[]":
            continue
        cleaned.append(item)

    return cleaned


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

DEBUG = env("DEBUG", default=False)
SECRET_KEY = env("SECRET_KEY", default="")
ALLOWED_HOSTS = _clean_host_list(env("ALLOWED_HOSTS", default=[]))
CSRF_TRUSTED_ORIGINS = _clean_host_list(env("CSRF_TRUSTED_ORIGINS", default=[]))

# NOTE: A trailing comma here is intentional to mirror the README's
# documented value layout. It does not change behavior.

# Do not block missing SECRET_KEY here. dev/test supply an explicit insecure
# default; prod.py is where empty or placeholder keys are refused.


# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "badshahstore.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "badshahstore.wsgi.application"
ASGI_APPLICATION = "badshahstore.asgi.application"


# ---------------------------------------------------------------------------
# Database — must be supplied by the environment, same pattern in dev/test
# ---------------------------------------------------------------------------

DATABASES = {
    "default": env.db("DATABASE_URL"),
}


# ---------------------------------------------------------------------------
# Cache / broker — read from environment, no hardcoded defaults
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
    }
}

CELERY_BROKER_URL = env("REDIS_URL")
CELERY_RESULT_BACKEND = env("REDIS_URL")


# ---------------------------------------------------------------------------
# Security — production controls, toggled by environment
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS", default=0)
SESSION_COOKIE_SECURE = env("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env("CSRF_COOKIE_SECURE", default=True)

if env("SECURE_PROXY_SSL_HEADER", default=()):
    SECURE_PROXY_SSL_HEADER = env("SECURE_PROXY_SSL_HEADER")
    USE_X_FORWARDED_HOST = env("USE_X_FORWARDED_HOST", default=True)
    USE_X_FORWARDED_PROTO = env("USE_X_FORWARDED_PROTO", default=True)


# ---------------------------------------------------------------------------
# Internationalisation / timezone — Django settings, not shop settings
# ---------------------------------------------------------------------------

TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
LANGUAGE_CODE = "en-IN"


# ---------------------------------------------------------------------------
# Static / media
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# NOTE: AUTH_USER_MODEL is set in Slice 5 once apps/accounts exists.
# Do not add it here, and do not add accounts to INSTALLED_APPS yet.

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
