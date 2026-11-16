#!/usr/bin/env bash
# The nightly database backup (D96, D97).
#
# It runs ON THE SERVER, from the repository folder, as a normal user:
#     bash deploy/backup.sh
# and normally from cron, once a night (see deploy/backup.cron).
#
# What it does, in this order:
#   1. dump the database inside the `db` container with pg_dump,
#   2. check that the dump can be read back,
#   3. check that it is not suspiciously small,
#   4. encrypt it with the age PUBLIC key,
#   5. pipe the encrypted file into `upload_backup`, which stores it in the
#      private R2 bucket and deletes what the retention rule no longer keeps.
#
# The PRIVATE age key is not on the server: this script can lock a backup but
# can never open one, so a stolen server still gives no readable database.
# The plain dump exists only inside a temporary folder, and that folder is
# deleted on every exit, whether the run succeeded or failed.
#
# `bash` is spelled out everywhere because Windows cannot set the execute bit
# on a committed file, so the file may arrive on the server without one.
set -euo pipefail

# Run from the repository root (this script's folder, one level up), so `.env`,
# the Compose file and the manage.py commands all resolve.
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml"

# Anything smaller than this is not a dump of this shop's database.
MIN_DUMP_BYTES=1024

if [ ! -f .env ]; then
  echo "ERROR: .env is missing; this must run from the repository folder on the server."
  echo "ERROR: cannot report this to Sentry, because that needs the stack."
  exit 1
fi

# env_value NAME reads one value out of .env and never runs the file.
# grep finds the one line that starts with NAME=, cut keeps everything after
# the first `=`, and the two tr calls drop the spaces and the quotes that
# Compose itself would strip. `|| true` keeps a missing line from stopping the
# script before it can say which value is missing.
env_value() {
  grep -E "^$1=" .env 2>/dev/null | head -n 1 | cut -d '=' -f 2- | tr -d '[:space:]' | tr -d "\"'" || true
}

POSTGRES_USER="$(env_value POSTGRES_USER)"
POSTGRES_DB="$(env_value POSTGRES_DB)"
AGE_RECIPIENT="$(env_value AGE_RECIPIENT)"

# fail says what went wrong in one line, tells Sentry through the web
# container, and stops with a non-zero status so cron records a failure.
# `|| true` on the Sentry call keeps a Sentry problem from hiding the real
# reason in the log.
fail() {
  echo "ERROR: $1"
  $COMPOSE exec -T web python manage.py report_backup_failure "$1" || true
  exit 1
}

[ -n "$POSTGRES_USER" ] || fail "POSTGRES_USER is missing from .env"
[ -n "$POSTGRES_DB" ] || fail "POSTGRES_DB is missing from .env"
[ -n "$AGE_RECIPIENT" ] || fail "AGE_RECIPIENT (the age public key) is missing from .env"

# A public age key always starts with age1. Catching a wrong value here is much
# cheaper than catching it on the night a restore is needed.
case "$AGE_RECIPIENT" in
  age1*) ;;
  *) fail "AGE_RECIPIENT does not look like an age public key (it must start with age1)" ;;
esac

# age does the encryption; without it there is no backup to make.
if ! command -v age > /dev/null 2>&1; then
  fail "the age program is not installed on this server"
fi

# One private temporary folder for the plain dump and its encrypted copy.
# mktemp -d makes a new folder that only this user can read, and the trap
# removes it however the script ends: a half-written dump is never left behind
# and is never uploaded (a failed step exits before the upload).
TMP="$(mktemp -d)"
chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

echo "backup: dumping $POSTGRES_DB"
$COMPOSE exec -T db pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$TMP/dump" \
  || fail "pg_dump failed"

# pg_restore --list reads the dump's own table of contents. A dump this cannot
# list is not a dump, and it is much better to learn that now than on the day
# the shop needs a restore.
echo "backup: checking the dump can be read"
$COMPOSE exec -T db pg_restore --list < "$TMP/dump" > /dev/null \
  || fail "the dump cannot be read (pg_restore --list failed)"

DUMP_BYTES="$(wc -c < "$TMP/dump" | tr -d '[:space:]')"
if [ "$DUMP_BYTES" -lt "$MIN_DUMP_BYTES" ]; then
  fail "the dump is only $DUMP_BYTES bytes; pg_dump probably failed"
fi
echo "backup: the dump is $DUMP_BYTES bytes"

# -r means "encrypt to this recipient", and the recipient is the PUBLIC key.
# There is no identity file here, on purpose: the private key never lives on
# the server (D96).
echo "backup: encrypting the dump"
age -r "$AGE_RECIPIENT" -o "$TMP/dump.age" "$TMP/dump" \
  || fail "age could not encrypt the dump"

# upload_backup reads the encrypted file from standard input, stores it under
# a UTC timestamp, prunes what the retention rule no longer keeps, and prints
# the key, the byte count and the number of old backups removed. That line is
# left alone: it is what a person reads in the system log.
echo "backup: uploading the encrypted backup"
$COMPOSE exec -T web python manage.py upload_backup < "$TMP/dump.age" \
  || fail "upload_backup failed"

echo "backup ok"
