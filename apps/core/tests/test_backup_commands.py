"""The three backup commands, against a fake S3 client (D96).

No test here touches the network: ``backups.make_client`` is replaced with a
function that returns a small recording stand-in, which is exactly why the
commands look the client up at call time instead of at import time.
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from typing import Any

import pytest
import sentry_sdk
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core import backups

# A complete but fake backup configuration. None of it is a real credential.
BACKUP_SETTINGS = {
    "BACKUP_R2_BUCKET": "badshah-backups",
    "BACKUP_R2_ACCOUNT_ID": "exampleaccountid0123456789",
    "BACKUP_R2_ACCESS_KEY_ID": "example-backup-key",
    "BACKUP_R2_SECRET_ACCESS_KEY": "example-backup-secret",
}


class _FakePaginator:
    """Stands in for boto3's paginator, which yields one page per call."""

    def __init__(self, client: FakeS3Client) -> None:
        self._client = client

    def paginate(self, *, Bucket: str, Prefix: str) -> list[dict[str, Any]]:
        # One page is enough: the command only ever lists a handful of files.
        contents = [{"Key": key} for key in self._client.objects if key.startswith(Prefix)]
        return [{"Contents": contents}]


class FakeS3Client:
    """A recording stand-in for the boto3 S3 client.

    It keeps the same call shapes the commands use, and records everything, so
    a test can ask what was uploaded, what was deleted and in which order.
    """

    def __init__(self, objects: list[str] | None = None, *, fail_upload: bool = False) -> None:
        self.objects = list(objects or [])
        self.fail_upload = fail_upload
        self.uploads: list[tuple[str, str, bytes]] = []
        self.downloads: list[tuple[str, str]] = []
        self.delete_batches: list[list[str]] = []

    def upload_fileobj(self, fileobj: Any, bucket: str, key: str) -> None:
        if self.fail_upload:
            raise OSError("the bucket refused the upload")
        # ``read()`` on a file object returns every remaining byte.
        self.uploads.append((bucket, key, fileobj.read()))
        self.objects.append(key)

    def download_fileobj(self, bucket: str, key: str, fileobj: Any) -> None:
        self.downloads.append((bucket, key))
        fileobj.write(b"the dump")

    def get_paginator(self, name: str) -> _FakePaginator:
        assert name == "list_objects_v2"
        return _FakePaginator(self)

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> None:
        keys = [row["Key"] for row in Delete["Objects"]]
        self.delete_batches.append(keys)
        for key in keys:
            self.objects.remove(key)

    @property
    def deleted(self) -> list[str]:
        """Every key deleted, in the order the batches named them."""
        # A comprehension with two ``for`` parts walks a list of lists and
        # flattens it into one list.
        return [key for batch in self.delete_batches for key in batch]


class _FakeStdin:
    """A stand-in for sys.stdin, with the raw byte stream the command reads."""

    def __init__(self, payload: bytes) -> None:
        self.buffer = io.BytesIO(payload)


class _FakeStdout:
    """A stand-in for sys.stdout: one byte stream and one text stream."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()
        self.text = io.StringIO()

    def write(self, text: str) -> int:
        return self.text.write(text)

    def flush(self) -> None:
        # Nothing is buffered anywhere, so there is nothing to flush.
        pass


@pytest.fixture(autouse=True)
def _backup_bucket(settings: Any) -> None:
    """Give every command a bucket name, so no test depends on the environment."""
    settings.BACKUP_R2_BUCKET = BACKUP_SETTINGS["BACKUP_R2_BUCKET"]


def _run(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    payload: bytes | None = None,
    args: tuple[str, ...] = (),
) -> tuple[_FakeStdout, str]:
    """Run one command with the fake streams, and return what it wrote."""
    fake_stdout = _FakeStdout()
    if payload is not None:
        monkeypatch.setattr(sys, "stdin", _FakeStdin(payload))
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    errors = io.StringIO()
    call_command(name, *args, stderr=errors)
    return fake_stdout, errors.getvalue()


def _key(year: int, month: int, day: int) -> str:
    """The key a backup taken at 02:30 UTC that day would have."""
    return backups.backup_key(datetime(year, month, day, 2, 30, tzinfo=UTC))


def _past_keys() -> list[str]:
    """Eight daily backups from January 2020, old enough to be pruned."""
    return [_key(2020, 1, day) for day in range(1, 9)]


# ---------------------------------------------------------------------------
# upload_backup
# ---------------------------------------------------------------------------
def test_upload_sends_the_input_under_a_backups_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeS3Client()
    monkeypatch.setattr(backups, "make_client", lambda: client)
    payload = b"d" * (backups.MIN_BACKUP_BYTES + 1)

    _run(monkeypatch, "upload_backup", payload=payload)

    assert len(client.uploads) == 1
    bucket, key, data = client.uploads[0]
    assert bucket == "badshah-backups"
    assert key.startswith(backups.BACKUP_PREFIX)
    assert key.endswith(".dump.age")
    # The bytes that arrived are the bytes that went up, unchanged.
    assert data == payload


def test_upload_refuses_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeS3Client()
    monkeypatch.setattr(backups, "make_client", lambda: client)

    with pytest.raises(CommandError):
        _run(monkeypatch, "upload_backup", payload=b"")

    assert client.uploads == []
    assert client.delete_batches == []


def test_upload_refuses_input_below_the_minimum_size(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeS3Client()
    monkeypatch.setattr(backups, "make_client", lambda: client)

    with pytest.raises(CommandError):
        _run(monkeypatch, "upload_backup", payload=b"d" * (backups.MIN_BACKUP_BYTES - 1))

    assert client.uploads == []
    assert client.delete_batches == []


def test_upload_accepts_input_of_exactly_the_minimum_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client()
    monkeypatch.setattr(backups, "make_client", lambda: client)

    _run(monkeypatch, "upload_backup", payload=b"d" * backups.MIN_BACKUP_BYTES)

    # The limit is a refusal *below* the minimum, not at it.
    assert len(client.uploads) == 1


def test_upload_deletes_the_keys_the_rule_names_after_the_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client(objects=_past_keys())
    monkeypatch.setattr(backups, "make_client", lambda: client)

    _run(monkeypatch, "upload_backup", payload=b"d" * (backups.MIN_BACKUP_BYTES + 1))

    # The daily window keeps the newest seven days, and the weekly window keeps
    # 5 January (the newest of that week), so only the first two days go.
    assert client.deleted == [_key(2020, 1, 1), _key(2020, 1, 2)]


def test_upload_deletes_nothing_when_the_upload_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    past = _past_keys()
    client = FakeS3Client(objects=past, fail_upload=True)
    monkeypatch.setattr(backups, "make_client", lambda: client)

    with pytest.raises(CommandError):
        _run(monkeypatch, "upload_backup", payload=b"d" * (backups.MIN_BACKUP_BYTES + 1))

    # A failed upload deletes nothing: every old backup is still there.
    assert client.delete_batches == []
    assert client.objects == past


def test_upload_reports_a_failure_to_sentry_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client(fail_upload=True)
    monkeypatch.setattr(backups, "make_client", lambda: client)
    captured: list[BaseException] = []
    flushed: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", captured.append)
    monkeypatch.setattr(sentry_sdk, "flush", lambda **options: flushed.append(options))

    with pytest.raises(CommandError):
        _run(monkeypatch, "upload_backup", payload=b"d" * (backups.MIN_BACKUP_BYTES + 1))

    assert len(captured) == 1
    assert str(captured[0]) == "the bucket refused the upload"
    # The flush is what actually sends the event before the process exits.
    assert flushed == [{"timeout": 5}]


def test_upload_prints_key_bytes_and_removed_count(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeS3Client(objects=_past_keys())
    monkeypatch.setattr(backups, "make_client", lambda: client)
    payload = b"d" * (backups.MIN_BACKUP_BYTES + 1)

    fake_stdout, _errors = _run(monkeypatch, "upload_backup", payload=payload)

    uploaded_key = client.uploads[0][1]
    line = fake_stdout.text.getvalue()
    assert uploaded_key in line
    assert f"({len(payload)} bytes)" in line
    assert "removed 2 old backup(s)" in line


# ---------------------------------------------------------------------------
# latest_backup
# ---------------------------------------------------------------------------
def test_latest_backup_streams_the_newest_key_and_names_it_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    older = _key(2026, 5, 1)
    newest = _key(2026, 5, 2)
    client = FakeS3Client(objects=[older, newest])
    monkeypatch.setattr(backups, "make_client", lambda: client)

    fake_stdout, errors = _run(monkeypatch, "latest_backup")

    assert client.downloads == [("badshah-backups", newest)]
    assert fake_stdout.buffer.getvalue() == b"the dump"
    # Standard output stays a clean dump; the name goes to standard error.
    assert fake_stdout.text.getvalue() == ""
    assert errors.strip() == newest


def test_latest_backup_fails_when_there_is_no_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeS3Client()
    monkeypatch.setattr(backups, "make_client", lambda: client)

    with pytest.raises(CommandError):
        _run(monkeypatch, "latest_backup")

    assert client.downloads == []


# ---------------------------------------------------------------------------
# report_backup_failure
# ---------------------------------------------------------------------------
def test_report_backup_failure_sends_an_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[tuple[str, dict[str, Any]]] = []
    flushed: list[dict[str, Any]] = []

    def _record(message: str, **options: Any) -> None:
        messages.append((message, options))

    monkeypatch.setattr(sentry_sdk, "capture_message", _record)
    monkeypatch.setattr(sentry_sdk, "flush", lambda **options: flushed.append(options))

    fake_stdout, _errors = _run(
        monkeypatch, "report_backup_failure", args=("pg_dump wrote nothing",)
    )

    assert messages == [("Backup failed: pg_dump wrote nothing", {"level": "error"})]
    assert flushed == [{"timeout": 5}]
    assert "pg_dump wrote nothing" in fake_stdout.text.getvalue()


def test_report_backup_failure_works_without_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing is initialised here: both SDK calls are no-ops, and the command
    # still says what it would have reported instead of raising.
    fake_stdout, _errors = _run(monkeypatch, "report_backup_failure", args=("age failed",))

    assert "age failed" in fake_stdout.text.getvalue()


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("missing_name", sorted(BACKUP_SETTINGS))
def test_make_client_refuses_each_missing_setting(settings: Any, missing_name: str) -> None:
    for name, value in BACKUP_SETTINGS.items():
        setattr(settings, name, value)
    setattr(settings, missing_name, "")

    with pytest.raises(ImproperlyConfigured) as error:
        backups.make_client()

    # The refusal names the setting the owner has to fill in.
    assert missing_name in str(error.value)


def test_make_client_refuses_the_media_bucket(settings: Any) -> None:
    for name, value in BACKUP_SETTINGS.items():
        setattr(settings, name, value)
    settings.R2_BUCKET_NAME = BACKUP_SETTINGS["BACKUP_R2_BUCKET"]

    with pytest.raises(ImproperlyConfigured) as error:
        backups.make_client()

    assert "R2_BUCKET_NAME" in str(error.value)


def test_make_client_uses_the_backup_credentials_and_the_r2_endpoint(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    for name, value in BACKUP_SETTINGS.items():
        setattr(settings, name, value)
    calls: list[tuple[str, dict[str, Any]]] = []

    def _client(service: str, **options: Any) -> str:
        calls.append((service, options))
        return "the client"

    monkeypatch.setattr(backups.boto3, "client", _client)

    assert backups.make_client() == "the client"

    service, options = calls[0]
    assert service == "s3"
    assert options["endpoint_url"] == (
        "https://exampleaccountid0123456789.r2.cloudflarestorage.com"
    )
    assert options["region_name"] == "auto"
    # The backup token, never the media one.
    assert options["aws_access_key_id"] == BACKUP_SETTINGS["BACKUP_R2_ACCESS_KEY_ID"]
    assert options["aws_secret_access_key"] == BACKUP_SETTINGS["BACKUP_R2_SECRET_ACCESS_KEY"]
    assert options["config"].signature_version == "s3v4"
