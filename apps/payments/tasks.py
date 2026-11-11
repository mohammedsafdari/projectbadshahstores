"""Celery tasks for online payments and refunds (D77, D78).

The tasks are thin wrappers: the services own the rules and the transactions,
so the same work can be run by hand with a management command.
"""

from __future__ import annotations

from celery import shared_task

from apps.payments.refunds import reconcile_refunds as reconcile
from apps.payments.refunds import send_refund as send_one
from apps.payments.services import expire_unpaid_online_orders as expire_orders


@shared_task
def expire_unpaid_online_orders() -> int:
    """Cancel unpaid online orders and restore their carts; return the count."""
    return expire_orders()


@shared_task
def send_refunded_payment(refund_id: int) -> str:
    """Send one requested refund now; return the status stored afterwards."""
    return send_one(refund_id).status


@shared_task
def send_pending_refunds() -> int:
    """Send every requested refund and check every sent one; return a count."""
    return reconcile()
