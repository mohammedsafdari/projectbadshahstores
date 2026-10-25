"""Print the stock ledger's integrity problems (ADR D43, point 8).

Read-only: it never fixes or changes anything. It exits with code 1 only when
there is at least one ERROR; warnings are printed but do not fail the command.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.inventory.integrity import SEVERITY_ERROR, find_integrity_problems


class Command(BaseCommand):
    help = "Check the stock ledger for broken rules. Read-only. Exit code 1 on any ERROR."

    def handle(self, *args: Any, **options: Any) -> str | None:
        problems = find_integrity_problems()
        if not problems:
            self.stdout.write(self.style.SUCCESS("Inventory integrity check: no problems found."))
            return None

        errors = 0
        for problem in problems:
            line = f"[{problem.severity}] {problem.code} {problem.variant_sku}: {problem.message}"
            if problem.severity == SEVERITY_ERROR:
                errors += 1
                self.stdout.write(self.style.ERROR(line))
            else:
                self.stdout.write(self.style.WARNING(line))

        warnings = len(problems) - errors
        self.stdout.write(f"{errors} error(s), {warnings} warning(s).")
        if errors:
            # CommandError makes manage.py exit with code 1 and print the message.
            raise CommandError(f"Inventory integrity check failed with {errors} error(s).")
        return None
