"""Catalog app models: brands, categories, products."""

from django.db import models

from apps.core.models import ArchivableModel, TimeStampedModel


class Brand(TimeStampedModel, ArchivableModel):
    """A product brand, e.g. a third-party brand or our own spice brand."""

    name = models.CharField(max_length=120, unique=True)
    # Slugs are romanised ASCII (e.g. "chana-dal"). Hindi text goes in name
    # fields, because Django's slug check rejects Devanagari vowel signs.
    slug = models.SlugField(max_length=140, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Category(TimeStampedModel, ArchivableModel):
    """A product category. Categories form a tree through ``parent``."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    # "self" means this model points at its own table. A top-level category
    # has no parent (null=True for the database, blank=True for forms).
    # PROTECT: a category that still has children cannot be deleted.
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name
