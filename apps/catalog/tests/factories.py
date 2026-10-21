"""Test factories for catalog models (factory_boy)."""

import factory

from apps.catalog.models import Brand, Category


class BrandFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Brand

    # Sequence gives each new object a unique number n, so unique
    # constraints on name and slug never collide.
    name = factory.Sequence(lambda n: f"Brand {n}")
    slug = factory.Sequence(lambda n: f"brand-{n}")


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Category {n}")
    slug = factory.Sequence(lambda n: f"category-{n}")
    parent = None
