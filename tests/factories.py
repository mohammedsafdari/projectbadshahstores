"""Factories for the Badshah Store test suite."""

import factory

from apps.accounts.models import User


class UserFactory(factory.django.DjangoModelFactory):
    """Factory for the phone-based User model."""

    class Meta:
        model = User

    phone = factory.Sequence(lambda n: f"+9198765432{n:02d}")
    email = factory.LazyFunction(lambda: f"user-{factory.Sequence(lambda n: n)}@example.com")
    full_name = factory.Faker("name")
    role = User.ROLE_MEMBER
    is_active = True
    is_staff = False
