# The runbook

This is the page to open when something is wrong, or when you are about to do
something to the live shop. Every command below is meant to be copied as it is.
Every code block is preceded by one line saying where it runs, and nothing here
is a suggestion to improvise: if a step does not look like the one below, stop
and read the failure before typing anything else.

The shop runs one paid VPS (D76). Nothing in this page needs a tool that is not
already on that server.

## 1. Where things are

| What | Where |
| --- | --- |
| The repository | `/srv/badshahstore` on the server (an example: use the folder you actually cloned into) |
| The real environment file | `/srv/badshahstore/.env`, which is never committed and holds every secret |
| The production Compose file | `/srv/badshahstore/docker-compose.prod.yml`, always passed with `-f` |
| The nginx vhost | `deploy/nginx/default.conf`, built into the nginx image |
| The certificate folder | `/etc/badshahstore/certs/origin.pem` and `origin.key`, read-only into nginx |
| The deploy markers | `.deploy-good` (the commit now running) and `.deploy-previous` (the one before it) in the repository folder |
| The backups | the **private** Cloudflare R2 bucket named in `R2_BACKUP_BUCKET`, under `backups/`, encrypted |
| The errors | the Sentry project whose DSN is in `.env` as `SENTRY_DSN` |
| The uptime check | UptimeRobot, watching `https://<the shop's host>/health/` |

## 2. Deploy

A deploy builds the images (the stylesheet and `collectstatic` happen inside the
image), starts `db` and `redis`, runs the migrations, runs Django's deployment
checks, starts the stack, waits for `web`, reloads nginx, asks nginx for
`/health/`, prunes the old images and only then records the commit in
`.deploy-good`.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
bash deploy/deploy.sh
```

To deploy a particular branch, tag or commit, pass it as the one argument.

On the server (Ubuntu, bash):

```
bash deploy/deploy.sh origin/main
bash deploy/deploy.sh <a tag or a commit>
```

## 3. Roll back

A rollback is the same script with the commit saved in `.deploy-previous`. It
runs a full deploy, so it is safe to repeat and safe to interrupt.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
cat .deploy-previous
bash deploy/deploy.sh "$(cat .deploy-previous)"
```

**The migration caveat.** A deploy may already have run a migration, and
checking out the earlier code does not undo it: the old code then runs against
a newer database schema. That is why the script never rolls back by itself. If
the failure happened after a migration, read it before rolling back; a rollback
that needs the schema undone is a decision, not a command.

## 4. Read the logs

Start with `web`: it shows every request and every Python traceback. `nginx`
shows what reached the server at all, which is how a proxy problem and an
application problem are told apart.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
docker compose -f docker-compose.prod.yml logs --tail=100 web
docker compose -f docker-compose.prod.yml logs --tail=100 nginx
docker compose -f docker-compose.prod.yml logs --tail=100 worker
docker compose -f docker-compose.prod.yml logs --tail=100 beat
```

The backup writes to the system log, not to a file of its own.

On the server (Ubuntu, bash):

```
journalctl -t badshahstore-backup --since yesterday
```

## 5. Backups

Every night at 02:30 the server's cron runs `deploy/backup.sh`. That script
dumps the database inside the `db` container, checks the dump with
`pg_restore --list`, refuses anything under 1 KiB, encrypts it with the age
**public** key, and pipes it into `upload_backup`. The upload stores it as
`backups/<UTC timestamp>.dump.age` and then deletes what the retention rule no
longer keeps: the newest backup of each of the last 7 days, 4 weeks and 3
months. The private key is not on the server, so nobody who takes the server can
read a backup.

Once a week, open the R2 dashboard and look at the bucket: there should be a new
file every day, and its size should be close to last week's. A backup that
suddenly halves is worth a look even when nothing failed.

If a night fails, the script calls `report_backup_failure`, which files one
Sentry message that begins `Backup failed:` and names the step that broke. That
message is the signal; the system log has the same line.

To run one backup by hand, for example after fixing a problem.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
bash deploy/backup.sh
```

The last line it prints on success is `backup ok`, and the upload's own line
names the key, the byte count and how many old backups it removed.

## 6. The restore test

A backup is only proven by restoring it. Do this once a month, and once before
launch. The test downloads the newest backup, decrypts it with the private key
and loads it into a throwaway database container, so the live database is not
touched.

First copy the private key to the server, only for the test. It is the file you
keep in two places on your own machines; never leave it on the server.

On your PC (Windows Command Prompt):

```
scp C:\path\to\backup-key.txt root@<the server's address>:/dev/shm/key.txt
```

Then run the test. `AGE_IDENTITY_FILE` is the path of that key. The script
prints `restore test passed: <key> restored` and the row counts of the orders,
invoices and customers tables when it works.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
AGE_IDENTITY_FILE=/dev/shm/key.txt bash deploy/restore_test.sh
```

Then delete the key from the server. `shred` overwrites the file before removing
it, which a plain `rm` does not.

On the server (Ubuntu, bash):

```
shred -u /dev/shm/key.txt
```

## 7. A real restore after a disaster

This replaces every row in the live database with the rows in the backup.
Anything the shop did after that backup — orders, payments, khata entries — is
gone. Take a copy of the current data first if the disk still works: a dump of
the broken database is often the only thing that explains what happened.

Stop everything that writes, so nothing reconnects halfway through.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
docker compose -f docker-compose.prod.yml stop web worker beat
```

Copy the private key to the server (the `scp` line in section 6), then download
and decrypt the backup you chose. `<key-name>` is the file name printed by
`latest_backup`, or the one you read in the R2 dashboard.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
docker compose -f docker-compose.prod.yml exec -T web python manage.py latest_backup > /dev/shm/dump.age 2> /dev/shm/backup-name.txt
cat /dev/shm/backup-name.txt
age -d -i /dev/shm/key.txt -o /dev/shm/dump /dev/shm/dump.age
```

Recreate the empty database. Replace `<db_user>` and `<db_name>` with
`POSTGRES_USER` and `POSTGRES_DB` from `.env`: the values are not in your shell,
and this page never tells you to run `.env`.

On the server (Ubuntu, bash):

```
docker compose -f docker-compose.prod.yml exec -T db dropdb -U <db_user> --if-exists <db_name>
docker compose -f docker-compose.prod.yml exec -T db createdb -U <db_user> <db_name>
docker compose -f docker-compose.prod.yml exec -T db pg_restore -U <db_user> -d <db_name> --no-owner --no-privileges --exit-on-error < /dev/shm/dump
```

Start the shop again and check it.

On the server (Ubuntu, bash):

```
docker compose -f docker-compose.prod.yml up -d
bash deploy/deploy.sh
```

Then delete the decrypted dump and the key, and open the shop on a phone to make
sure the catalogue and one order page load.

On the server (Ubuntu, bash):

```
shred -u /dev/shm/dump /dev/shm/dump.age /dev/shm/backup-name.txt /dev/shm/key.txt
```

## 8. The site is down

The uptime monitor emails you when `/health/` stops answering. Work down this
list in order and stop at the first thing that is wrong.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=100 web
docker compose -f docker-compose.prod.yml logs --tail=100 nginx
df -h
free -m
```

- A service that is `Restarting` or `Exit 1` is the one to read the logs of.
- `df -h` at 100% is the usual way a small server dies: Docker logs and images
  are the things that grow. `docker image prune -f` is safe (section 11).
- `free -m` with almost no free memory means the box is swapping; restarting
  `worker` usually gives it back.
- If nginx answers but the app does not, reload nginx first; a code-only deploy
  is exactly the case it exists for.

On the server (Ubuntu, bash):

```
docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
```

- If that does not help, restart one service at a time, never all of them.

On the server (Ubuntu, bash):

```
docker compose -f docker-compose.prod.yml restart web
```

- If the deploy broke it, roll back (section 3) and then read the logs.

## 9. Uptime and Sentry set-up

**Uptime.** Create a free UptimeRobot account and one HTTPS monitor on
`https://<the shop's host>/health/`, checked every 5 minutes, alerting by email.
`/health/` is the one address that answers without a certificate and without
touching the database, which is why it is the one to watch (D94).

**Sentry.** Create one Django project on the free plan, copy its DSN into
`.env` as `SENTRY_DSN`, and restart the three processes that read the settings.
Nothing is sent while the DSN is empty, so the shop can run without it.

On the server (Ubuntu, bash):

```
cd /srv/badshahstore
docker compose -f docker-compose.prod.yml up -d web worker beat
docker compose -f docker-compose.prod.yml exec -T web python manage.py shell -c "import sentry_sdk; sentry_sdk.capture_message('badshahstore runbook test'); sentry_sdk.flush(timeout=5)"
```

The message should appear in the project within a minute, under the release the
deploy passed in (`SENTRY_RELEASE`, the short commit hash). If it does not, the
DSN is wrong or the settings module is not the production one.

## 10. Rotate a secret

Every secret lives in `.env` and nowhere else. Change it there, then restart the
services that read it. Restarting `web` alone is enough for anything only the
website uses; the worker and the beat send messages and run the jobs, so they
need the same value.

| Secret | What it protects | What to restart |
| --- | --- | --- |
| `SECRET_KEY` | Signing sessions and password-reset links | `web worker beat`. Every customer is signed out, so do it at a quiet hour |
| `POSTGRES_PASSWORD` | The database login | Change it in the database first, then in `.env`, then restart `db web worker beat` |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | The public product photos | `web`, on the next upload |
| `R2_BACKUP_ACCESS_KEY_ID` / `R2_BACKUP_SECRET_ACCESS_KEY` | The private backups | Nothing: the next backup run reads them |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET` | Online payments | `web worker beat` |
| `EMAIL_HOST_PASSWORD` | Order emails | `web worker beat` |
| `MSG91_AUTH_KEY` | Order and login SMS | `web worker beat` |

The age key pair is different: `AGE_RECIPIENT` is the **public** key, so it is
not a secret. If you ever replace the pair, new backups use the new key and the
old ones still need the old private key, so keep the old one until its backups
have aged out of the retention window.

## 11. Housekeeping

Old images pile up after every deploy, and the disk is the first thing a small
server runs out of. Pruning images is safe: a running container keeps the image
it started from.

On the server (Ubuntu, bash):

```
docker image prune -f
docker system df
```

Never run `docker volume prune`, `docker compose down -v` or any command with
`--volumes`: the database lives in a volume, and that volume is the shop's only
copy of its orders. The backups are the second copy, and they are a day old at
best.

## Before launch

The server checklist — the certificate, the firewall, the first backup, the
first restore test, the Sentry test message, the uptime monitor, the CSP header
through nginx and one rollback — is the Slice 55 list in
`docs/open_questions.md`. Nothing in this slice has been run yet.
