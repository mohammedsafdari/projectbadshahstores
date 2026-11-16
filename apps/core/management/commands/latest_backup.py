"""Write the newest encrypted backup to standard output (D96).

The restore test in Slice 54b asks this command for the newest file, decrypts
it with the private age key (which stays on the owner's own machine) and loads
it into a scratch database. Standard output holds the bytes and nothing else,
so the stream can be piped straight into ``age -d``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core import backups


class Command(BaseCommand):
    help = "Write the newest stored backup to standard output and its name to standard error."

    def handle(self, *args: Any, **options: Any) -> str | None:
        # Looked up through the module at call time, so a test can replace it.
        client = backups.make_client()
        # The bucket name comes from ``backups``, the one module that reads it.
        bucket = backups.backup_bucket()

        newest: tuple[datetime, str] | None = None
        for key in backups.list_backup_keys(client, bucket):
            moment = backups.parse_backup_time(key)
            if moment is None:
                continue
            # The newest moment wins; ``(moment, key)`` is unpacked by index,
            # which is what the [0] and [1] below read.
            if newest is None or moment > newest[0]:
                newest = (moment, key)

        if newest is None:
            raise CommandError("There is no backup in the bucket yet.")

        key = newest[1]
        # The bytes go out as raw bytes; the name goes to standard error, so a
        # pipe reads a clean dump and the person still sees which one it was.
        client.download_fileobj(bucket, key, sys.stdout.buffer)
        self.stderr.write(key)
        return None
