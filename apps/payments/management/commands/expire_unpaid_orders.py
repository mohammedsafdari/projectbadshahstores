"""Cancel unpaid online orders by hand (D77).

The same service runs every 300 seconds through Celery beat; this command is
for a local check or a one-off run after a deployment.
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.payments.services import expire_unpaid_online_orders


class Command(BaseCommand):
    help = "Cancel online orders whose payment never completed and restore their carts."

    def handle(self, *args: Any, **options: Any) -> str | None:
        cancelled = expire_unpaid_online_orders()
        self.stdout.write(self.style.SUCCESS(f"Cancelled {cancelled} unpaid online order(s)."))
        return None
