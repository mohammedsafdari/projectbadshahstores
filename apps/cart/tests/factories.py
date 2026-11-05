"""Test factories for cart models (factory_boy)."""

from decimal import Decimal

import factory

from apps.cart.models import Cart, CartItem
from apps.catalog.tests.factories import ProductVariantFactory
from tests.factories import UserFactory


class CartFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Cart

    user = factory.SubFactory(UserFactory)


class CartItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CartItem

    cart = factory.SubFactory(CartFactory)
    variant = factory.SubFactory(ProductVariantFactory)
    quantity = Decimal("1.000")
