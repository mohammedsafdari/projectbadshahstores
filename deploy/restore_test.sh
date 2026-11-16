#!/usr/bin/env bash
# The restore test (D96, D97).
#
# It runs ON THE SERVER, by hand, once a month and once before the shop opens:
#     AGE_IDENTITY_FILE=/dev/shm/key.txt bash deploy/restore_test.sh
#
# It downloads the newest stored backup, decrypts it with the PRIVATE age key,
# and loads it into a THROWAWAY PostgreSQL container that is thrown away
# afterwards. The real `db` service is never touched: this proves the backup can
# be read, not that the site has been restored.
#
# The identity file is the private key. Copy it to the server only for this
# test, from your own machine, and delete it straight afterwards (`shred -u`).
# This script never prints its content and never copies it anywhere.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml"

# The throwaway database. `--rm` deletes it when it is stopped, and no port is
# published, so nothing outside this server can reach it.
CONTAINER="badshahstore-restore-test"
POSTGRES_IMAGE="postgres:16"

# Anything smaller than this is not a backup of this shop's database.
MIN_DUMP_BYTES=1024

fail() {
  echo "ERROR: $1"
  exit 1
}

if [ -z "${AGE_IDENTITY_FILE:-}" ]; then
  fail "AGE_IDENTITY_FILE is not set. Run: AGE_IDENTITY_FILE=/dev/shm/key.txt bash deploy/restore_test.sh"
fi
if [ ! -f "$AGE_IDENTITY_FILE" ]; then
  fail "AGE_IDENTITY_FILE does not point at a file: $AGE_IDENTITY_FILE"
fi
if ! command -v age > /dev/null 2>&1; then
  fail "the age program is not installed on this server"
fi

# One private temporary folder for the encrypted download and the decrypted
# dump. The trap stops the throwaway container (which also removes it, because
# it was started with --rm) and deletes the folder, however the script ends.
# The container's name is quoted so the trap reads it at the time it runs.
TMP="$(mktemp -d)"
chmod 700 "$TMP"
trap 'docker stop "$CONTAINER" > /dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

# latest_backup writes the dump itself to standard output and the key's name to
# standard error, so the name is kept in its own file and shown below.
echo "restore test: downloading the newest backup"
$COMPOSE exec -T web python manage.py latest_backup > "$TMP/dump.age" 2> "$TMP/key.txt" \
  || fail "latest_backup failed; is there a backup in the bucket yet?"

KEY="$(tail -n 1 "$TMP/key.txt" | tr -d '[:space:]')"
echo "restore test: the newest backup is $KEY"

AGE_BYTES="$(wc -c < "$TMP/dump.age" | tr -d '[:space:]')"
if [ "$AGE_BYTES" -lt "$MIN_DUMP_BYTES" ]; then
  fail "the downloaded backup is only $AGE_BYTES bytes; it is not a real dump"
fi

# -d means decrypt, and -i names the private key file. The file's content is
# read by age and never printed or copied by this script.
echo "restore test: decrypting the backup"
age -d -i "$AGE_IDENTITY_FILE" -o "$TMP/dump" "$TMP/dump.age" \
  || fail "age could not decrypt the backup; is AGE_IDENTITY_FILE the right key?"

# A throwaway database of the same major version as the real one. Plain
# `docker run` puts it on Docker's default network, never on the Compose
# project's, so it cannot even see the service called `db`; it publishes no
# port and mounts no volume either.
echo "restore test: starting the throwaway database"
docker run -d --rm --name "$CONTAINER" \
  -e POSTGRES_PASSWORD=restore-test-only \
  "$POSTGRES_IMAGE" > /dev/null \
  || fail "the throwaway database container could not be started"

echo "restore test: waiting for the throwaway database (up to 60 seconds)"
READY=0
for _attempt in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready -U postgres > /dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
if [ "$READY" != "1" ]; then
  fail "the throwaway database did not become ready in 60 seconds"
fi

# --no-owner and --no-privileges drop the roles of the original server, which do
# not exist in the throwaway one; --exit-on-error stops at the first bad object,
# so a partly-restored database can never pass as a good one.
echo "restore test: loading the dump into the throwaway database"
docker exec -i "$CONTAINER" pg_restore -U postgres -d postgres \
  --no-owner --no-privileges --exit-on-error < "$TMP/dump" \
  || fail "pg_restore failed; the backup in the bucket may be damaged"

# The number of rows in the three tables that matter. `psql -tA` prints the
# number alone, with no header and no padding, so it can be quoted in a line.
# The table names are the models' own (a model with no `db_table` in its Meta is
# named <app_label>_<model_name>):
#   orders.Order   -> orders_order
#   orders.Invoice -> orders_invoice
#   accounts.User  -> accounts_user
count_rows() {
  docker exec -i "$CONTAINER" psql -U postgres -d postgres -tA \
    -c "SELECT count(*) FROM $1;" | tr -d '[:space:]'
}

ORDERS="$(count_rows orders_order)" || fail "the orders_order count could not be read"
INVOICES="$(count_rows orders_invoice)" || fail "the orders_invoice count could not be read"
CUSTOMERS="$(count_rows accounts_user)" || fail "the accounts_user count could not be read"

echo "restore test: orders_order has $ORDERS row(s)"
echo "restore test: orders_invoice has $INVOICES row(s)"
echo "restore test: accounts_user has $CUSTOMERS row(s)"

echo "restore test passed: $KEY restored"
echo "REMEMBER: delete the private key from the server now (shred -u $AGE_IDENTITY_FILE)."
