"""Send one Sentry message when a backup step failed (D96).

The host script calls this when ``pg_dump``, ``pg_restore --list``, ``age`` or
the upload failed, so a backup that stopped working is noticed within a day
instead of the day the shop needs a restore.
"""

from __future__ import annotations

from typing import Any

import sentry_sdk
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send one Sentry error saying that a backup step failed."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("message", help="What failed, in one short sentence.")

    def handle(self, *args: Any, **options: Any) -> str | None:
        message = str(options["message"])

        # ``capture_message`` only files an event; it never raises. With no DSN
        # set the SDK was never started, and both calls do nothing at all.
        sentry_sdk.capture_message(f"Backup failed: {message}", level="error")
        sentry_sdk.flush(timeout=5)

        self.stdout.write(f"Reported the backup failure: {message}")
        return None
