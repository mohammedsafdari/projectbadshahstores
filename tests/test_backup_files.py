"""The backup scripts, the cron file and the runbook, read as text (D97).

Bash, `age`, Docker and cron are not on the coding agent's command list, so
every rule here is checked by reading the file and comparing it with the
decision. The first real run of all of it is on the staging server in Slice 55.

The helpers of ``tests/test_production_stack_files.py`` are private, so this
file reads its own files with the two-line helper below and keeps its own paths.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from apps.accounts.models import User
from apps.orders.models import Invoice, Order

ROOT = Path(settings.BASE_DIR)

BACKUP_SCRIPT = ROOT / "deploy" / "backup.sh"
RESTORE_SCRIPT = ROOT / "deploy" / "restore_test.sh"
CRON_FILE = ROOT / "deploy" / "backup.cron"
RUNBOOK = ROOT / "docs" / "runbook.md"
DEPLOY_SCRIPT = ROOT / "deploy" / "deploy.sh"
PROD_COMPOSE = ROOT / "docker-compose.prod.yml"
PROD_ENV_EXAMPLE = ROOT / "deploy" / "production.env.example"
ENV_EXAMPLE = ROOT / ".env.example"

# The new files, in the order the runbook mentions them.
NEW_FILES = (BACKUP_SCRIPT, RESTORE_SCRIPT, CRON_FILE, RUNBOOK)

# The script's own name for the throwaway container, used by its trap.
RESTORE_CONTAINER = "badshahstore-restore-test"


def _read(path: Path) -> str:
    """Read one file as UTF-8 text; a missing file fails with its own path."""
    assert path.is_file(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def _command_lines(text: str) -> list[str]:
    """Return the real command lines, with the comments and blanks removed."""
    return [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


# ---------------------------------------------------------------------------
# deploy/backup.sh
# ---------------------------------------------------------------------------
def test_backup_script_stops_on_error_and_cleans_its_temp_directory() -> None:
    script = _read(BACKUP_SCRIPT)

    # A failure anywhere stops the run, and an unset variable is an error
    # rather than an empty string quietly used as a password or a database.
    assert "set -euo pipefail" in script
    # One private temporary folder, removed however the script ends.
    assert 'TMP="$(mktemp -d)"' in script
    assert 'chmod 700 "$TMP"' in script
    assert "trap 'rm -rf \"$TMP\"' EXIT" in script


def test_backup_script_runs_dump_check_size_encrypt_upload_in_that_order() -> None:
    script = _read(BACKUP_SCRIPT)

    steps = [
        'pg_dump -U "$POSTGRES_USER"',
        "pg_restore --list",
        'wc -c < "$TMP/dump"',
        'age -r "$AGE_RECIPIENT"',
        "manage.py upload_backup",
    ]
    positions = [script.index(step) for step in steps]

    assert positions == sorted(positions), "the backup steps are out of order"
    # The dump is checked and encrypted before it is uploaded, and the upload is
    # the step that talks to R2.
    assert "backup ok" in script


def test_backup_script_encrypts_with_the_public_recipient_only() -> None:
    script = _read(BACKUP_SCRIPT)

    # -r means "encrypt to this public recipient"; the private key is not here.
    assert 'age -r "$AGE_RECIPIENT" -o "$TMP/dump.age" "$TMP/dump"' in script
    assert " -i " not in script
    assert "--identity" not in script
    assert "AGE_IDENTITY" not in script
    # The recipient is checked to look like a public key before it is used.
    assert "age1*)" in script


def test_backup_script_reports_every_failure_to_sentry() -> None:
    script = _read(BACKUP_SCRIPT)

    # One function says the reason, calls the command, and stops: every failing
    # step goes through it, so no failure can be silent.
    assert 'report_backup_failure "$1"' in script
    # A Sentry problem must never hide the real reason in the log.
    assert "|| true" in script

    for step in (
        '|| fail "pg_dump failed"',
        '|| fail "the dump cannot be read',
        'fail "the dump is only',
        '|| fail "age could not encrypt the dump"',
        '|| fail "upload_backup failed"',
    ):
        assert step in script, step


def test_backup_script_never_sources_the_env_file() -> None:
    script = _read(BACKUP_SCRIPT)

    # A file of shell-looking lines must never be executed by a backup script.
    assert "source .env" not in script
    assert ". .env" not in script
    # The values are read with plain tools instead.
    assert 'grep -E "^$1=" .env' in script
    assert "cut -d '=' -f 2-" in script


def test_backup_script_writes_nothing_outside_its_temp_directory() -> None:
    script = _read(BACKUP_SCRIPT)

    # Every quoted write target is inside the temporary folder. The only other
    # destination is /dev/null, which is not a file on disk.
    targets = re.findall(r'>\s*"([^"]+)"', script)

    assert targets
    for target in targets:
        assert target.startswith("$TMP/"), target
    # An append (>>) would leave yesterday's bytes in today's file.
    assert ">>" not in script


def test_backup_script_refuses_a_missing_age_program() -> None:
    script = _read(BACKUP_SCRIPT)

    assert "command -v age" in script
    assert 'fail "the age program is not installed on this server"' in script
    # The check comes before the dump, so a missing program costs no work.
    assert script.index("command -v age") < script.index('pg_dump -U "$POSTGRES_USER"')


# ---------------------------------------------------------------------------
# deploy/restore_test.sh
# ---------------------------------------------------------------------------
def test_restore_script_needs_an_identity_file_and_never_prints_it() -> None:
    script = _read(RESTORE_SCRIPT)

    # Both the variable and the file are required, and the message names the
    # way to run it.
    assert '[ -z "${AGE_IDENTITY_FILE:-}" ]' in script
    assert '[ ! -f "$AGE_IDENTITY_FILE" ]' in script
    assert "AGE_IDENTITY_FILE=/dev/shm/key.txt" in script
    # The private key is used by age and by nothing else: it is never read into
    # a variable, printed or copied.
    assert 'age -d -i "$AGE_IDENTITY_FILE"' in script
    assert 'cat "$AGE_IDENTITY_FILE"' not in script
    assert "base64" not in script


def test_restore_script_uses_a_throwaway_container_and_publishes_no_port() -> None:
    script = _read(RESTORE_SCRIPT)

    assert 'docker run -d --rm --name "$CONTAINER"' in script
    assert "pg_isready" in script
    # Nothing outside the server may reach the throwaway database, and it must
    # not join the Compose network either.
    assert "--publish" not in script
    assert " -p " not in script
    assert "--network host" not in script


def test_restore_script_cleans_up_its_container_and_files_on_exit() -> None:
    script = _read(RESTORE_SCRIPT)

    trap = next(line for line in script.splitlines() if line.startswith("trap "))
    # The container is stopped (and, started with --rm, removed with it) and the
    # temporary folder is deleted, however the script ends.
    assert 'docker stop "$CONTAINER"' in trap
    assert 'rm -rf "$TMP"' in trap
    # A throwaway container that cannot be stopped is not a reason to fail.
    assert "|| true" in trap


def test_restore_script_counts_rows_in_the_real_table_names() -> None:
    script = _read(RESTORE_SCRIPT)

    # The names come from the models themselves, not from a guess at Django's
    # default table names.
    for model in (Order, Invoice, User):
        assert model._meta.db_table in script, model
    assert "psql -U postgres -d postgres -tA" in script
    # -tA is what makes the count a bare number: no header, no padding.
    assert "SELECT count(*) FROM" in script


def test_restore_script_uses_the_same_postgres_major_version_as_compose() -> None:
    compose = _read(PROD_COMPOSE)
    script = _read(RESTORE_SCRIPT)

    image = re.search(r"image: (postgres:\d+)", compose)
    assert image is not None
    assert f'POSTGRES_IMAGE="{image.group(1)}"' in script


def test_restore_script_never_touches_the_real_db_service() -> None:
    script = _read(RESTORE_SCRIPT)

    # The restore goes into the throwaway container only: no command of this
    # script is run inside the `db` service.
    assert "$COMPOSE exec -T db" not in script
    assert "--exit-on-error" in script
    assert "$COMPOSE exec -T web python manage.py latest_backup" in script


# ---------------------------------------------------------------------------
# deploy/backup.cron
# ---------------------------------------------------------------------------
def test_cron_file_runs_at_0230_and_logs_to_the_system_log() -> None:
    cron = _read(CRON_FILE)

    line = (
        "30 2 * * * cd /srv/badshahstore && /usr/bin/bash deploy/backup.sh "
        "2>&1 | /usr/bin/logger -t badshahstore-backup"
    )
    assert line in cron
    assert _command_lines(cron) == [line]
    # The output goes to the system log, which rotates itself.
    assert "journalctl -t badshahstore-backup" in cron
    # The retention rule counts UTC days, so the note says so.
    assert "UTC" in cron


def test_cron_file_does_not_suggest_replacing_the_whole_crontab() -> None:
    cron = _read(CRON_FILE)

    # `crontab <file>` replaces every existing entry, so the file must never
    # offer it as a way to install this line.
    assert "crontab -e" in cron
    assert "REPLACES" in cron
    for line in _command_lines(cron):
        assert "crontab " not in line, line


# ---------------------------------------------------------------------------
# The release marker
# ---------------------------------------------------------------------------
def test_compose_passes_the_sentry_release_through_the_common_environment() -> None:
    compose = _read(PROD_COMPOSE)

    # The shared block above `services:` is what web, worker and beat all
    # inherit, so the marker is written once and reaches all three.
    above_services = compose.split("\nservices:\n", 1)[0]
    assert "SENTRY_RELEASE: ${SENTRY_RELEASE:-}" in above_services
    # An empty value is allowed: the stack may be started by hand.
    assert compose.count("SENTRY_RELEASE: ${SENTRY_RELEASE:-}") == 1


def test_deploy_script_exports_the_short_commit_as_the_sentry_release_before_the_build() -> None:
    script = _read(DEPLOY_SCRIPT)

    export = 'export SENTRY_RELEASE="$(git rev-parse --short HEAD)"'
    assert script.count(export) == 1
    # The export must come after the checkout (so it names the commit now on
    # disk) and before the build (so the image's processes inherit it).
    assert script.index("git checkout --detach") < script.index(export)
    assert script.index(export) < script.index("$COMPOSE build")


# ---------------------------------------------------------------------------
# docs/runbook.md
# ---------------------------------------------------------------------------
RUNBOOK_SECTIONS = (
    "## 1. Where things are",
    "## 2. Deploy",
    "## 3. Roll back",
    "## 4. Read the logs",
    "## 5. Backups",
    "## 6. The restore test",
    "## 7. A real restore after a disaster",
    "## 8. The site is down",
    "## 9. Uptime and Sentry set-up",
    "## 10. Rotate a secret",
    "## 11. Housekeeping",
)


def test_runbook_has_every_section_in_order() -> None:
    runbook = _read(RUNBOOK)

    positions = [runbook.index(section) for section in RUNBOOK_SECTIONS]

    assert positions == sorted(positions)
    assert len(set(positions)) == len(RUNBOOK_SECTIONS)
    # The last word points at what is still unrun.
    assert "docs/open_questions.md" in runbook


def test_runbook_names_the_real_scripts_and_commands() -> None:
    runbook = _read(RUNBOOK)

    for name in (
        "deploy/deploy.sh",
        "deploy/backup.sh",
        "deploy/restore_test.sh",
        "latest_backup",
        "report_backup_failure",
    ):
        assert name in runbook, name


def test_runbook_says_where_every_command_block_runs() -> None:
    runbook = _read(RUNBOOK)
    lines = runbook.splitlines()
    in_block = False
    checked = 0

    for index, line in enumerate(lines):
        if not line.startswith("```"):
            continue
        if in_block:
            in_block = False
            continue
        in_block = True
        previous = next(lines[at] for at in range(index - 1, -1, -1) if lines[at].strip())
        assert previous.startswith(("On the server", "On your PC")), (index, previous)
        checked += 1

    assert not in_block, "a code fence is never closed"
    # Every section that shows a command uses this rule, so a few blocks exist.
    assert checked >= 12


# ---------------------------------------------------------------------------
# The files themselves
# ---------------------------------------------------------------------------
def test_backup_files_use_lf_line_endings() -> None:
    for path in (*NEW_FILES, DEPLOY_SCRIPT, PROD_COMPOSE, PROD_ENV_EXAMPLE):
        assert b"\r\n" not in path.read_bytes(), path


def test_backup_files_hold_no_age_secret_key() -> None:
    # The private age key starts with these words when it is written down. It
    # must exist nowhere in the repository: not in the scripts, not in a doc,
    # not in an example environment file.
    secret_prefix = "AGE-SECRET-KEY-"
    candidates = [
        *(path for path in (ROOT / "deploy").rglob("*") if path.is_file()),
        *(ROOT / "docs").rglob("*.md"),
        ENV_EXAMPLE,
        PROD_ENV_EXAMPLE,
    ]

    assert candidates
    for path in candidates:
        assert secret_prefix not in _read(path), path
