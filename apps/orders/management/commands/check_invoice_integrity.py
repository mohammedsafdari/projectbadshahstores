"""Print the GST invoices' integrity problems (D80).

Read-only: it never fixes or changes anything. It exits with code 1 only when
there is at least one ERROR, exactly like ``check_inventory_integrity``.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.orders.invoices import SEVERITY_ERROR, find_invoice_problems


class Command(BaseCommand):
    help = "Check the GST invoices for broken rules. Read-only. Exit code 1 on any ERROR."

    def handle(self, *args: Any, **options: Any) -> str | None:
        problems = find_invoice_problems()
        if not problems:
            self.stdout.write(self.style.SUCCESS("Invoice integrity check: no problems found."))
            return None

        errors = 0
        for problem in problems:
            where = problem.invoice_number or "-"
            line = f"[{problem.severity}] {problem.code} {where}: {problem.message}"
            if problem.severity == SEVERITY_ERROR:
                errors += 1
                self.stdout.write(self.style.ERROR(line))
            else:
                self.stdout.write(self.style.WARNING(line))

        warnings = len(problems) - errors
        self.stdout.write(f"{errors} error(s), {warnings} warning(s).")
        if errors:
            # CommandError makes manage.py exit with code 1 and print the message.
            raise CommandError(f"Invoice integrity check failed with {errors} error(s).")
        return None
