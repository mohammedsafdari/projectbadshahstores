"""Send pending refunds and check the ones in flight by hand (D78).

The same service runs every 300 seconds through Celery beat; this command is
for a local check or a one-off run after a deployment.
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.payments.refunds import reconcile_refunds


class Command(BaseCommand):
    help = "Send requested refunds and check the refunds already sent."

    def handle(self, *args: Any, **options: Any) -> str | None:
        handled = reconcile_refunds()
        self.stdout.write(self.style.SUCCESS(f"Handled {handled} refund action(s)."))
        return None
