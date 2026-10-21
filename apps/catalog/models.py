"""Catalog app models: brands, categories, products."""

from django.db import models

from apps.core.models import ArchivableModel, TimeStampedModel


class Brand(TimeStampedModel, ArchivableModel):
    """A product brand, e.g. a third-party brand or our own spice brand."""

    name = models.CharField(max_length=120, unique=True)
    # allow_unicode lets the URL slug contain Hindi (Devanagari) characters.
    slug = models.SlugField(max_length=140, unique=True, allow_unicode=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
