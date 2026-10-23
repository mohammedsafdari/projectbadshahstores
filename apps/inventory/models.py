"""Inventory app models."""

from django.db import models

from apps.core.models import ArchivableModel, TimeStampedModel


class Supplier(TimeStampedModel, ArchivableModel):
    """A supplier for purchases. Minimal record: name, phone, notes."""

    name = models.CharField(max_length=160, unique=True)
    # Plain text on purpose, no validation: landlines and STD formats vary.
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)

    # Text fields use blank=True without null=True, so "no value" is always
    # the empty string, not NULL.

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
