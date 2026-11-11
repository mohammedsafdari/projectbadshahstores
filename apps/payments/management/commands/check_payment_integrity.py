"""Print the online money integrity problems (D78).

Read-only: it never fixes or changes anything. It exits with code 1 when at
least one problem is found.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.payments.integrity import check_payment_integrity


class Command(BaseCommand):
    help = "Check payments and refunds for broken rules. Read-only. Exit code 1 on any problem."

    def handle(self, *args: Any, **options: Any) -> str | None:
        problems = check_payment_integrity()
        if not problems:
            self.stdout.write(self.style.SUCCESS("Payment integrity check: no problems found."))
            return None

        for problem in problems:
            self.stdout.write(
                self.style.ERROR(f"{problem.code} payment={problem.payment_id}: {problem.detail}")
            )
        # CommandError makes manage.py exit with code 1 and print the message.
        raise CommandError(f"Payment integrity check failed with {len(problems)} problem(s).")
