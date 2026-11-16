#!/usr/bin/env bash
# The one deploy script (D95).
#
# It runs ON THE SERVER, from the repository folder, as a normal user:
#     bash deploy/deploy.sh            # deploy origin/main
#     bash deploy/deploy.sh <git-ref>  # deploy a branch, a tag or a commit
#
# `bash` is spelled out because Windows cannot set the execute bit on a file it
# commits, so the file may arrive without one.
#
# Rollback:  bash deploy/deploy.sh "$(cat .deploy-previous)"
#
# The markers are written only at the very end of a deploy that passed its health
# check: `.deploy-good` is the commit now running and `.deploy-previous` is the
# one before it, so a failed deploy, or a repeat of the same commit, never
# becomes the rollback target. A rollback is itself a deploy, so after one
# `.deploy-previous` holds the version that was rolled back FROM, and a second
# rollback goes forward again.
#
# There is NO automatic rollback. A deploy runs migrations, and a migration is
# not undone by checking out the old code, so going back is a decision a person
# makes after reading what went wrong.
#
# Anything that deletes data is deliberately absent: no `down -v`, no
# `volume rm`, no `prune --volumes`. The database lives in a volume and is the
# shop's only copy of its orders.
set -euo pipefail

REF="${1:-origin/main}"

# 1. Run from the repository root (this script's folder, one level up).
cd "$(dirname "$0")/.."
echo "repository: $(pwd)"

if [ ! -f .env ]; then
  echo "ERROR: .env is missing. Copy deploy/production.env.example to .env and fill it in."
  exit 1
fi

# --untracked-files=no: a new, unstaged file is not a reason to refuse, but a
# changed tracked file is, because the server would then deploy different code
# from the commit it checks out.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ERROR: a tracked file is changed on the server; commit or discard it first."
  exit 1
fi

# 2. Fetch and check out the requested ref. Nothing is written to the rollback
# markers here: they may only ever name a deploy that passed its health check.
git fetch --prune origin
echo "fetched origin"

git checkout --detach "$REF"
echo "checked out $REF at $(git rev-parse HEAD)"

COMPOSE="docker compose -f docker-compose.prod.yml"

# 3. Build. This builds the stylesheet and runs collectstatic INSIDE the image,
# so neither step can be skipped by a person on a busy evening (D90, D93).
echo "building the images"
$COMPOSE build

# 4. Database and Redis first, then the schema, then Django's own checks.
echo "starting db and redis"
$COMPOSE up -d db redis

# Wait for a container's healthcheck. docker inspect reads the state Docker
# itself recorded, so this asks the same question the healthcheck asks.
wait_healthy() {
  service="$1"
  for _attempt in $(seq 1 60); do
    container="$($COMPOSE ps -q "$service")"
    if [ -n "$container" ]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")"
      if [ "$status" = "healthy" ]; then
        echo "$service is healthy"
        return 0
      fi
    fi
    sleep 2
  done
  echo "ERROR: $service did not become healthy in 120 seconds."
  return 1
}

wait_healthy db
wait_healthy redis

echo "applying database migrations"
$COMPOSE run --rm web python manage.py migrate --noinput

# Django prints its deployment warnings and exits non-zero only for an ERROR,
# so a warning here is information and an error stops the script.
echo "running Django's deployment checks"
$COMPOSE run --rm web python manage.py check --deploy

# 5. Start the whole stack, then ask it, through nginx, whether it is alive.
echo "starting the stack"
$COMPOSE up -d --remove-orphans

# Django refuses a Host it does not know, so the probe sends the first
# ALLOWED_HOSTS entry. The value is read with grep and cut, never by sourcing
# .env: a file of shell-looking lines must never be executed by a deploy script.
# Compose strips the quotes around a value in .env and this reads the file
# itself, so it strips them too: a quoted list would otherwise send
# `"shop.example.com` as the Host and fail a check that should have passed.
HOST="$(grep -E '^ALLOWED_HOSTS=' .env | head -n 1 | cut -d '=' -f 2- | cut -d ',' -f 1 | tr -d '[:space:]' | tr -d "\"'" || true)"
if [ -z "$HOST" ]; then
  echo "ERROR: ALLOWED_HOSTS in .env is empty; the health check has no Host to send."
  exit 1
fi

echo "waiting for web to become healthy (up to 90 seconds)"
web_healthy=0
for _attempt in $(seq 1 45); do
  container="$($COMPOSE ps -q web)"
  if [ -n "$container" ]; then
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")"
    if [ "$status" = "healthy" ]; then
      web_healthy=1
      break
    fi
  fi
  sleep 2
done

# nginx looks the name `web` up once, when it starts. A deploy that changes only
# Python code rebuilds the app image but leaves the nginx image identical, so
# Compose recreates `web` (possibly with a new address) and never touches nginx;
# without this reload it would proxy to the old address and every page would
# answer 502.
echo "reloading nginx so it resolves web again"
if ! $COMPOSE exec -T nginx nginx -s reload; then
  echo "ERROR: nginx could not be reloaded. The last log lines of nginx:"
  $COMPOSE logs --tail=40 nginx || true
  exit 1
fi

# /health/ is exempt from the HTTPS redirect, so this asks nginx over plain HTTP
# and needs no certificate (D94).
if [ "$web_healthy" = "1" ] && curl -fsS -H "Host: $HOST" http://127.0.0.1/health/; then
  echo "health check passed"
else
  echo "ERROR: the health check failed. The last log lines of web and nginx:"
  $COMPOSE logs --tail=40 web || true
  $COMPOSE logs --tail=40 nginx || true
  exit 1
fi

# 6. Tidy up the images this deploy replaced.
echo "pruning the unused images"
docker image prune -f

# 7. Record the deploy, now that the site has answered. `.deploy-good` is this
# commit and `.deploy-previous` keeps the last good one, copied only when it
# differs, so a failed deploy or a repeat of the same commit leaves both alone.
DEPLOYED="$(git rev-parse HEAD)"
if [ -f .deploy-good ] && [ "$(cat .deploy-good)" != "$DEPLOYED" ]; then
  cp .deploy-good .deploy-previous
fi
echo "$DEPLOYED" > .deploy-good

echo "deployed commit $DEPLOYED"
