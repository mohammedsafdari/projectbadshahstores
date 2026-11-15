"""
Badshah Store — Django settings base.

Shared settings for all environments.
Timezone/locale are Django settings, not shop settings.

Environment variables are the source of truth. django-environ reads them
through env() and env.db(). There are no hardcoded credentials in base.

The settings tests force the environment to skip reading .env so a
developer's local .env can never change a test result.
"""

import os
from pathlib import Path

import environ
from celery.schedules import crontab

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
    # Django needs this app installed to compile OpClass expressions into a
    # valid index (the trigram indexes in catalog.Product.Meta). Without it a
    # migration writes (UPPER(col) opclass) instead of (UPPER(col)) opclass.
    "django.contrib.postgres",
    # Slice 4 app shells (all except accounts).
    "apps.core",
    "apps.catalog",
    "apps.inventory",
    "apps.cart",
    "apps.orders",
    "apps.credit",
    "apps.payments",
    "apps.delivery",
    "apps.notifications",
    "apps.promotions",
    "apps.reports",
    "apps.storefront",
    "apps.adminpanel",
    "apps.accounts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # A fresh nonce for every request and the Content-Security-Policy on every
    # response (D93). Right after SecurityMiddleware so no other middleware can
    # build a response before the nonce exists.
    "apps.core.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # The shop's language choice: Django's own cookie and header rule, except
    # that the staff and admin pages always stay English (D92). It reads the
    # session, so it sits after SessionMiddleware and before CommonMiddleware.
    "apps.core.middleware.ShopLocaleMiddleware",
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
                "django.template.context_processors.debug",
                "apps.cart.context_processors.cart_line_count",
                # Whether the Hindi switcher shows, and whether the page is
                # already Hindi. It reads no database row (D92).
                "apps.storefront.context_processors.language_switch",
                # The request's Content-Security-Policy nonce, for the one
                # inline script that needs it (D93).
                "apps.core.csp.csp_nonce",
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

# The one scheduled job so far (D77): cancel online orders whose payment never
# completed and give their stock back. Celery beat asks for it every 300
# seconds. The task itself is idempotent, so a missed or repeated run is safe.
CELERY_BEAT_SCHEDULE = {
    "expire-unpaid-online-orders": {
        "task": "apps.payments.tasks.expire_unpaid_online_orders",
        "schedule": 300.0,
    },
    # Refunds and payment reconciliation (D78). The task is idempotent, so a
    # missed or repeated run is safe.
    "send-pending-refunds": {
        "task": "apps.payments.tasks.send_pending_refunds",
        "schedule": 300.0,
    },
    # A retry whose queue call failed waits in RETRY_PENDING with no task; this
    # job queues it again (D89). Sending nothing twice is the row lock's job.
    "sweep-stuck-notification-retries": {
        "task": "apps.notifications.tasks.sweep_stuck_retries",
        "schedule": 600.0,
    },
    # The daily stock email at 08:00 Indian time, because CELERY_TIMEZONE is the
    # shop's own zone (D89).
    "send-stock-digest": {
        "task": "apps.notifications.tasks.send_stock_digest",
        "schedule": crontab(hour=8, minute=0),
    },
    # The search word log keeps 180 days, so this deletes what is older (D89).
    "delete-old-search-terms": {
        "task": "apps.storefront.tasks.delete_old_search_terms",
        "schedule": crontab(hour=3, minute=30),
    },
    # A saved cart nobody has touched for 30 days is deleted, its lines with it
    # (D94). A line changed inside the window keeps its cart.
    "delete-stale-saved-carts": {
        "task": "apps.cart.tasks.delete_stale_saved_carts",
        "schedule": crontab(hour=3, minute=45),
    },
    # Django's own clearsessions: the expired session rows go, and every guest
    # cart kept inside them goes too (D94).
    "clear-expired-sessions": {
        "task": "apps.core.tasks.clear_expired_sessions",
        "schedule": crontab(hour=3, minute=50),
    },
}


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

# The Content-Security-Policy (D93). It is on in development, in the tests and
# in production; the switch sends the same policy under the Report-Only header
# name, which a browser reports but does not enforce. Extra image hosts go into
# img-src only (Slice 53 puts the R2 media host here).
CSP_REPORT_ONLY = env.bool("CSP_REPORT_ONLY", default=False)
CSP_EXTRA_IMG_SOURCES = _clean_host_list(env("CSP_EXTRA_IMG_SOURCES", default=[]))


# ---------------------------------------------------------------------------
# Internationalisation / timezone — Django settings, not shop settings
# ---------------------------------------------------------------------------

TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
LANGUAGE_CODE = "en-IN"

# The Hindi switch (D92). Off by default: while it is off the shop offers
# English only, a Hindi cookie is ignored and the language form answers 404.
# Turning it on adds Hindi to the list below; no string is translated until
# the Hindi content slice wraps the customer text.
HINDI_ENABLED = env.bool("HINDI_ENABLED", default=False)

# English is the only language until the switch is on, so a cookie can never
# select a language the shop has not offered.
LANGUAGES = [("en-IN", "English")]
if HINDI_ENABLED:
    LANGUAGES.append(("hi", "हिन्दी"))

# Celery reads its own clock setting, so a crontab hour means Indian time
# rather than the server's own zone (D89). Interval jobs are not affected.
CELERY_TIMEZONE = TIME_ZONE


# ---------------------------------------------------------------------------
# Static / media
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

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

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.ActiveUserModelBackend"]

# The SMS backend for phone login codes (D59). A dotted path to a class with
# send_otp(phone, code); the console backend is for development only.
SMS_BACKEND = env("SMS_BACKEND", default="apps.notifications.sms.ConsoleSmsBackend")

# Order messages (D69): off until a real SMS or SMTP backend is set up.
ORDER_SMS_ENABLED = env.bool("ORDER_SMS_ENABLED", default=False)
ORDER_EMAIL_ENABLED = env.bool("ORDER_EMAIL_ENABLED", default=False)
# The From address on every order email; the console backend prints it in dev.
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Badshah Stores <noreply@localhost>")

# Email (D88). The console backend is the development default in dev.py; these
# values are the real SMTP settings a deployment supplies.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

# MSG91 SMS (D88). An empty auth key leaves every send logged as failed and
# never breaks an order. The template ids are one per event, written as
# OTP=abc,ORDER_PLACED=def. The timeout applies to one HTTP call.
MSG91_AUTH_KEY = env("MSG91_AUTH_KEY", default="")
MSG91_TEMPLATE_IDS = env.dict("MSG91_TEMPLATE_IDS", default={})
MSG91_TIMEOUT_SECONDS = env.int("MSG91_TIMEOUT_SECONDS", default=10)

# Online payments (D77). The provider name is empty until the owner turns it
# on, so checkout offers cash only until then.
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", default="")
# The fake gateway never touches the network. It is refused unless this switch
# is on, so a production deployment cannot fall into it by accident.
PAYMENT_ALLOW_FAKE_GATEWAY = env.bool("PAYMENT_ALLOW_FAKE_GATEWAY", default=False)
# Which methods the checkout may show, copied from the gateway dashboard. The
# project never decides that UPI, cards or anything else is available itself.
PAYMENT_ENABLED_METHODS = _clean_host_list(env("PAYMENT_ENABLED_METHODS", default=[]))
# How long an unpaid online order keeps its stock after the normal reservation
# window before it is cancelled and the cart is restored.
PAYMENT_GRACE_MINUTES = env.int("PAYMENT_GRACE_MINUTES", default=30)
# Refunds (D78). A refund is sent at most this many times before it is marked
# FAILED and left for a person. A SENT refund is checked again after this many
# minutes, and reconciliation only touches money captured before the minimum
# age, so an in-flight capture is never raced.
REFUND_MAX_ATTEMPTS = env.int("REFUND_MAX_ATTEMPTS", default=3)
REFUND_STALE_MINUTES = env.int("REFUND_STALE_MINUTES", default=30)
PAYMENT_RECONCILE_MIN_AGE_MINUTES = env.int("PAYMENT_RECONCILE_MIN_AGE_MINUTES", default=15)
# The absolute site address used in messages (for example the cart link after
# a failed payment). Blank means the link is left relative.
SITE_BASE_URL = env("SITE_BASE_URL", default="")

# The header that carries the visitor's real address behind a proxy (D89).
# Blank means every limit counts REMOTE_ADDR, which is the proxy itself when
# one stands in front of the site.
CLIENT_IP_HEADER = env("CLIENT_IP_HEADER", default="")

# Razorpay test-mode credentials. They are read from the environment only and
# never stored in the repository (D76).
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", default="")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", default="")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", default="")

# auth.W004 warns when USERNAME_FIELD is not unique in the database. For this
# project the phone username is unique only among active users (deactivated
# accounts may be re-activated with their original phone), so the conditional
# unique constraint is intentional and W004 is silenced here.
SILENCED_SYSTEM_CHECKS = ["auth.W004"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# List of app labels that are installed app shells in Slice 4.
# Used by tests to verify the new apps are installed without importing accounts.
SLICE_4_INSTALLED_APP_LABELS = [
    "core",
    "catalog",
    "inventory",
    "cart",
    "orders",
    "payments",
    "delivery",
    "notifications",
    "promotions",
    "storefront",
    "adminpanel",
]

SHOP_FSSAI_LICENCE = env("SHOP_FSSAI_LICENCE", default="")
