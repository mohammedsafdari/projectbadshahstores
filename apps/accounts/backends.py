"""Authentication backends for the accounts app.

The custom user model enforces username (phone) uniqueness only among active
accounts in the same space, and Django's auth system shows Warn check W004 for
that setup. SILENCED_SYSTEM_CHECKS lists it in settings and this file exists so
the conditional-uniqueness decision stays in one place rather than being
scattered across the model and settings.

Since Slice 38f (D72) the places matter: `get_by_natural_key` finds active
staff accounts only, because it is what the /admin/ login uses. A customer
account with the same phone as a staff account can therefore never open the
admin, and an admin password is never tried on the shop.
"""

from django.contrib.auth.backends import ModelBackend


class ActiveUserModelBackend(ModelBackend):
    """Auth backend backed by Django's ModelBackend.

    This backend does not change lookup behavior; it exists so the conditional
    uniqueness of USERNAME_FIELD (phone is unique only among active accounts in
    one space: staff or customer) is documented in code and paired with the
    corresponding SILENCED_SYSTEM_CHECKS entry rather than being a silent
    surprise in the model.
    """

    pass
