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
#      private R2 bucket and deletes what the retention rule no longer keeps,
#   6. ping the missed-backup alarm, so a night that never runs is noticed too.
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
# The optional healthchecks.io ping address (D97, amended in 54f1). Empty means
# no ping at all, so a first run and a developer's run work without it.
BACKUP_PING_URL="$(env_value BACKUP_PING_URL)"

# ping tells the missed-backup alarm that this run happened. The address is a
# healthchecks.io check set up for a period of one day with a grace time of a few
# hours: the check expects one ping a day, and healthchecks.io emails the owner
# when the ping is missing. That is the one failure Sentry cannot report, because
# a run that never started (cron removed, Docker down, the server off) has no
# process left to send anything: only a message that does not arrive can say it.
# The address is a secret — anyone holding it can silence the alarm — so this
# script never prints it and no message names it. `|| true` because the alarm is
# a monitor: it must never turn a good backup into a failed one. The "/fail"
# suffix is healthchecks.io's address for an explicit failure, which alerts at
# once instead of waiting a day for a ping that is not coming.
ping() {
  if [ -z "$BACKUP_PING_URL" ]; then
    return 0
  fi
  curl -fsS -m 10 --retry 3 -o /dev/null "$BACKUP_PING_URL$1" || true
}

# fail says what went wrong in one line, tells Sentry through the web
# container, and stops with a non-zero status so cron records a failure.
# `|| true` on the Sentry call keeps a Sentry problem from hiding the real
# reason in the log.
fail() {
  echo "ERROR: $1"
  $COMPOSE exec -T web python manage.py report_backup_failure "$1" || true
  # The alarm is told this run failed, so it emails now rather than a day later.
  ping "/fail"
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
# The plain dump is customer data, so it lives in memory: /dev/shm is a
# memory-backed folder on Ubuntu and the folder is deleted on every exit, so the
# dump vanishes with it and never touches the disk. The fallback is for a system
# where /dev/shm is missing or not writable; the dump is still deleted either way.
if [ -d /dev/shm ] && [ -w /dev/shm ]; then
  TMP="$(mktemp -d -p /dev/shm badshahstore-backup.XXXXXX)"
else
  TMP="$(mktemp -d)"
fi
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

# The last step of the dead-man's switch: this run happened. It is the last thing
# the script does, so a run that dies before this line stays unpinged and the
# alarm fires — which is the whole point of putting it here rather than early.
ping ""

echo "backup ok"
