"""Test factories for core models (factory_boy)."""

import factory

from apps.core.models import AuditLog


class AuditLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuditLog

    action = "PRICE_CHANGE"
    target_type = "catalog.productvariant"
    target_id = factory.Sequence(lambda n: str(n + 1))
    actor = None
    # ``LazyFunction`` calls the lambda each time a row is built or saved, so
    # every factory row gets its own ``dict``, not a shared one.
    changes = factory.LazyFunction(lambda: {"selling_price": ["10.00", "12.00"]})
    note = ""
