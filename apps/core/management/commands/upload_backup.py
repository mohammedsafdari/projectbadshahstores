"""Upload one encrypted backup from standard input to the private bucket (D96).

The host script pipes the ``age``-encrypted file in, so this command holds no
key: it cannot read the dump, and it never writes it to disk. Everything about
the file's name and the retention rule lives in ``apps.core.backups``.
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from typing import Any

import sentry_sdk
from django.core.management.base import BaseCommand, CommandError

from apps.core import backups

# ``delete_objects`` refuses a request that names more than 1000 keys, so the
# old backups are removed in chunks of at most this many.
DELETE_BATCH_SIZE = 1000


class Command(BaseCommand):
    help = "Read an encrypted database dump from standard input and upload it to R2."

    def handle(self, *args: Any, **options: Any) -> str | None:
        # ``sys.stdin.buffer`` is the raw byte stream behind the text one;
        # reading it gives bytes, which is what a dump file is.
        payload = sys.stdin.buffer.read()

        # Refused before anything is uploaded or deleted: a dump this small
        # means pg_dump failed, and a failed dump must not become a backup.
        if len(payload) < backups.MIN_BACKUP_BYTES:
            raise CommandError(
                f"Refusing to upload a {len(payload)}-byte backup: a dump shorter than "
                f"{backups.MIN_BACKUP_BYTES} bytes means pg_dump failed."
            )

        try:
            # Looked up through the module at call time, so a test can replace
            # it and the real boto3 client is never built there.
            client = backups.make_client()
            # The bucket name comes from ``backups``, so the upload, the
            # listing and the client all read one setting from one module.
            bucket = backups.backup_bucket()
            key = backups.backup_key(datetime.now(UTC))

            # ``io.BytesIO`` wraps bytes so they look like an open file, which
            # is what ``upload_fileobj`` wants to read from.
            client.upload_fileobj(io.BytesIO(payload), bucket, key)

            # Only now, after a successful upload, does anything get deleted:
            # an upload that fails leaves every old backup where it is.
            keys = backups.list_backup_keys(client, bucket)
            old_keys = backups.select_keys_to_delete(keys)
            removed = 0
            # ``range(0, len, size)`` walks the list in steps, so each turn
            # hands one batch to the API.
            for start in range(0, len(old_keys), DELETE_BATCH_SIZE):
                batch = old_keys[start : start + DELETE_BATCH_SIZE]
                client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": name} for name in batch]},
                )
                removed += len(batch)
        except Exception as exc:
            # ``capture_exception`` is a no-op when Sentry was never started,
            # so a development run with no DSN simply sends nothing.
            sentry_sdk.capture_exception(exc)
            sentry_sdk.flush(timeout=5)
            raise CommandError(f"The backup upload failed: {exc}") from exc

        self.stdout.write(
            f"Uploaded {key} ({len(payload)} bytes); removed {removed} old backup(s)."
        )
        return None
