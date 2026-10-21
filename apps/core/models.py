"""Core app models: small abstract base classes shared by other apps.

Abstract models (``abstract = True``) create no database table of their
own. Their fields are copied into every model that inherits from them.
"""

from django.db import models


class TimeStampedModel(models.Model):
    """Adds created_at / updated_at to any model that inherits from it."""

    # auto_now_add: set once, when the row is first created.
    created_at = models.DateTimeField(auto_now_add=True)
    # auto_now: refreshed on every save().
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ArchivableModel(models.Model):
    """Adds an is_active flag so rows are archived instead of deleted."""

    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
