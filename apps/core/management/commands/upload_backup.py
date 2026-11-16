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
            undeleted: list[str] = []
            # ``range(0, len, size)`` walks the list in steps, so each turn
            # hands one batch to the API.
            for start in range(0, len(old_keys), DELETE_BATCH_SIZE):
                batch = old_keys[start : start + DELETE_BATCH_SIZE]
                answer = client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": name} for name in batch]},
                )
                # A batch delete answers success even when single files could
                # not be removed, and names those under ``Errors``. So a key
                # counts as removed only when the answer lists it under
                # ``Deleted``; an answer with no such list (a stand-in may send
                # none) is read as the whole batch, but only when it reports no
                # errors either. ``answer`` is a mapping, and ``.get`` is how a
                # mapping is read without knowing whether the key is there.
                deleted = (answer or {}).get("Deleted")
                errors = (answer or {}).get("Errors") or []
                if deleted is not None:
                    removed += len(deleted)
                elif not errors:
                    removed += len(batch)
                # An error entry names its key; one without a key is still a
                # failure, and "?" keeps it counted rather than dropped.
                undeleted.extend(str(entry.get("Key", "?")) for entry in errors)
        except Exception as exc:
            # ``capture_exception`` is a no-op when Sentry was never started,
            # so a development run with no DSN simply sends nothing.
            sentry_sdk.capture_exception(exc)
            sentry_sdk.flush(timeout=5)
            raise CommandError(f"The backup upload failed: {exc}") from exc

        # A clean-up that could not delete every old backup is a warning, never a
        # failed backup: the new backup is safe in the bucket, and reporting this
        # as a failure would say the opposite. It still goes to Sentry, at the
        # level that does not page anyone, because it is what a bucket filling up
        # starts with (D97, amended in 54f1).
        if undeleted:
            shown = ", ".join(undeleted[:3]) + (", ..." if len(undeleted) > 3 else "")
            # Standard error, so the success line on standard output stays one
            # clean line for the person reading the system log.
            self.stderr.write(f"Warning: could not delete {len(undeleted)} old backup(s): {shown}")
            sentry_sdk.capture_message(
                f"The backup clean-up could not delete {len(undeleted)} old backup(s).",
                level="warning",
            )
            sentry_sdk.flush(timeout=5)

        self.stdout.write(
            f"Uploaded {key} ({len(payload)} bytes); removed {removed} old backup(s)."
        )
        return None
