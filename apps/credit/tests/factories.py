"""Test factories for the credit models (factory_boy)."""

from decimal import Decimal

import factory

from apps.credit.models import CreditAccount
from tests.factories import UserFactory


class CreditAccountFactory(factory.django.DjangoModelFactory):
    """An active credit account with a 1000.00 limit and no balance."""

    class Meta:
        model = CreditAccount

    user = factory.SubFactory(UserFactory)
    credit_limit = Decimal("1000.00")
    balance = Decimal("0.00")
    is_active = True
