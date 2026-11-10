"""Celery tasks for online payments (D77).

The task is a thin wrapper: the service owns the rules and the transaction, so
the same work can be run by hand with the management command.
"""

from __future__ import annotations

from celery import shared_task

from apps.payments.services import expire_unpaid_online_orders as expire_orders


@shared_task
def expire_unpaid_online_orders() -> int:
    """Cancel unpaid online orders and restore their carts; return the count."""
    return expire_orders()
