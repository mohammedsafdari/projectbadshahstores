"""Authentication backends for the accounts app.

The custom user model enforces username (phone) uniqueness only among active
users, via a conditional unique constraint. Django's auth system shows Warn
check W004 for that setup, which is why SILENCED_SYSTEM_CHECKS lists it in
settings and why this file exists: it keeps the conditional-uniqueness decision
in one place rather than scattering it across the model and settings.
"""

from django.contrib.auth.backends import ModelBackend


class ActiveUserModelBackend(ModelBackend):
    """Auth backend backed by Django's ModelBackend.

    This backend does not change lookup behavior; it exists so the conditional
    uniqueness of USERNAME_FIELD (phone is unique only among active users) is
    documented in code and paired with the corresponding SILENCED_SYSTEM_CHECKS
    entry rather than being a silent surprise in the model.
    """

    pass
