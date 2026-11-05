"""Factories for the Badshah Store test suite."""

import factory

from apps.accounts.models import User


class UserFactory(factory.django.DjangoModelFactory):
    """Factory for the phone-based User model."""

    class Meta:
        model = User

    # A fixed ten-digit base plus the sequence stays a valid Indian number far
    # beyond the first 100 users in one test run.
    phone = factory.Sequence(lambda n: f"+91{9876543200 + n}")
    email = factory.LazyFunction(lambda: f"user-{factory.Sequence(lambda n: n)}@example.com")
    full_name = factory.Faker("name")
    role = User.ROLE_MEMBER
    is_active = True
    is_staff = False
