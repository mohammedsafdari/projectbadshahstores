"""Concurrency test for the capability matrix service (D75).

The data is created and committed in the main thread first, each thread gets
its own database connection and closes it in a ``finally`` block, and the
assertions are on final counts, never on timing or on which thread won.
"""

from __future__ import annotations

import threading

import pytest
from django.db import connection

from apps.accounts.capabilities import Capability
from apps.accounts.capability_services import set_role_capabilities
from apps.accounts.models import RoleCapability, User
from tests.factories import UserFactory


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_edits_leave_one_row() -> None:
    actor = UserFactory(is_staff=True, role=User.ROLE_OWNER)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(allowed: bool) -> None:
        try:
            barrier.wait(timeout=10)
            set_role_capabilities(actor, User.ROLE_MEMBER, {Capability.STOCK_WRITE: allowed})
        except BaseException as error:  # Reported after the threads join.
            errors.append(error)
        finally:
            # Each thread has its own database connection; close it explicitly.
            connection.close()

    threads = [threading.Thread(target=worker, args=(value,)) for value in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    assert (
        RoleCapability.objects.filter(
            role=User.ROLE_MEMBER,
            capability=Capability.STOCK_WRITE,
        ).count()
        == 1
    )
