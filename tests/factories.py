"""Factories for the Badshah Store test suite."""

import factory
from django.utils import timezone

from apps.accounts.models import User


class UserFactory(factory.django.DjangoModelFactory):
    """Factory for the phone-based User model."""

    class Meta:
        model = User

    # A fixed ten-digit base plus the sequence stays a valid Indian number far
    # beyond the first 100 users in one test run.
    phone = factory.Sequence(lambda n: f"+91{9876543200 + n}")
    email = factory.Sequence(lambda n: f"user-{n}@example.com")
    full_name = factory.Faker("name")
    role = User.ROLE_MEMBER
    is_active = True
    is_staff = False
    # A factory user is a verified customer by default (D71), so an order test
    # that reaches delivery does not have to set the two timestamps itself.
    phone_verified_at = factory.LazyFunction(timezone.now)
    email_verified_at = factory.LazyFunction(timezone.now)
