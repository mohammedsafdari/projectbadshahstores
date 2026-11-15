"""Scheduled core jobs (D94).

One job so far: Django's own session clean-up, which deletes the expired rows
of the session table.
"""

from __future__ import annotations

from celery import shared_task
from django.core.management import call_command


@shared_task
def clear_expired_sessions() -> None:
    """Delete every session row whose expiry has passed (D94).

    The project keeps its sessions in the database (Django's default), and a
    guest cart lives inside its session, so without this job the table only
    grows and old guest carts are never forgotten.

    ``clearsessions`` is Django's own management command; ``call_command`` runs
    a management command as a plain function, which is what a task needs (a
    task cannot type a command into a terminal).
    """
    call_command("clearsessions")
