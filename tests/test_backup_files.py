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
    # One private temporary folder, removed however the script ends. It is made
    # in memory (see the separate test) and falls back to an ordinary one.
    assert 'TMP="$(mktemp -d -p /dev/shm badshahstore-backup.XXXXXX)"' in script
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
    # not join the Compose network either. The port check reads the run command
    # itself, because "/dev/shm -p" in the temp-folder line is a different `-p`
    # (mktemp's --directory-prefix).
    run_at = script.index("docker run")
    run_command = script[run_at : script.index("> /dev/null", run_at)]
    assert " -p " not in run_command
    assert "--publish" not in run_command
    assert "--network" not in run_command
    assert "--publish" not in script
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
# The missed-backup alarm and the memory-backed folder (54f1)
# ---------------------------------------------------------------------------
def test_backup_script_pings_the_check_after_the_last_step() -> None:
    script = _read(BACKUP_SCRIPT)

    ping_at = script.index('ping ""')
    # The ping comes after the upload, which is the last step of the backup...
    assert script.index("manage.py upload_backup") < ping_at
    # ...and before the success line, so `backup ok` means the alarm was told.
    assert ping_at < script.index('echo "backup ok"')


def test_backup_script_pings_the_fail_address_in_the_failure_path() -> None:
    script = _read(BACKUP_SCRIPT)

    start = script.index("fail() {")
    body = script[start : script.index("\n}\n", start)]
    # healthchecks.io's /fail address alerts at once instead of after the grace
    # time, so a failed night is reported the way a missed one is.
    assert 'ping "/fail"' in body
    # It has to come before the exit, or it would never be attempted.
    assert body.index('ping "/fail"') < body.index("exit 1")


def test_backup_ping_never_fails_the_backup() -> None:
    script = _read(BACKUP_SCRIPT)

    # No address means no ping at all, so the check is skipped, not failed.
    assert 'if [ -z "$BACKUP_PING_URL" ]' in script
    # A bounded request, and a failure that is thrown away.
    curls = [line for line in script.splitlines() if "curl " in line]
    assert len(curls) == 1
    assert "-m 10" in curls[0]
    assert "--retry 3" in curls[0]
    assert curls[0].rstrip().endswith("|| true")


def test_backup_script_never_prints_the_ping_url() -> None:
    script = _read(BACKUP_SCRIPT)

    # The address is a secret: anyone holding it can silence the alarm.
    for line in script.splitlines():
        if "BACKUP_PING_URL" not in line:
            continue
        assert not line.lstrip().startswith(("echo", "printf")), line
    assert "ERROR: $BACKUP_PING_URL" not in script
    assert 'report_backup_failure "$BACKUP_PING_URL' not in script
    # The one command that does use it throws its output away.
    assert 'curl -fsS -m 10 --retry 3 -o /dev/null "$BACKUP_PING_URL$1"' in script


def test_backup_ping_url_is_optional() -> None:
    script = _read(BACKUP_SCRIPT)

    assert 'BACKUP_PING_URL="$(env_value BACKUP_PING_URL)"' in script
    # Unlike the three values below, a missing ping address is not a failure.
    assert 'fail "BACKUP_PING_URL' not in script
    assert '[ -n "$BACKUP_PING_URL" ]' not in script
    required = [line for line in script.splitlines() if line.startswith('[ -n "$')]
    assert len(required) == 3


def test_backup_and_restore_scripts_use_a_memory_backed_temp_folder() -> None:
    for script, template in (
        (_read(BACKUP_SCRIPT), "badshahstore-backup.XXXXXX"),
        (_read(RESTORE_SCRIPT), "badshahstore-restore.XXXXXX"),
    ):
        # /dev/shm is memory-backed on Ubuntu, and the dump is customer data.
        assert "[ -d /dev/shm ] && [ -w /dev/shm ]" in script
        assert f'TMP="$(mktemp -d -p /dev/shm {template})"' in script
        # A system without a writable /dev/shm still gets a private folder.
        assert 'TMP="$(mktemp -d)"' in script
        assert 'chmod 700 "$TMP"' in script
        assert 'rm -rf "$TMP"' in script


def test_production_env_example_names_the_ping_variable() -> None:
    example = _read(PROD_ENV_EXAMPLE)

    assert "\nBACKUP_PING_URL=\n" in example
    assert "optional healthchecks.io ping address" in example
    # Empty is the default, so nothing is pinged until the owner fills it in.
    assert "BACKUP_PING_URL=http" not in example


def test_runbook_explains_the_missed_backup_alarm() -> None:
    runbook = _read(RUNBOOK)

    # Section 5 names the three signals and what each one catches.
    section_five = runbook[runbook.index("## 5. Backups") : runbook.index("## 6. The restore test")]
    for signal in ("Sentry", "healthchecks.io", "bucket"):
        assert signal in section_five, signal
    # Section 9 is where the check itself is set up, with its period and grace.
    section_nine = runbook[
        runbook.index("## 9. Uptime and Sentry set-up") : runbook.index("## 10. Rotate a secret")
    ]
    assert "### The missed-backup alarm" in section_nine
    for word in ("healthchecks.io", "BACKUP_PING_URL", "period", "grace"):
        assert word in section_nine, word


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
