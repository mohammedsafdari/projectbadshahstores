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

# 2. Fetch, remember where we came from, check out the requested ref.
git fetch --prune origin
echo "fetched origin"

git rev-parse HEAD > .deploy-previous
echo "previous commit saved to .deploy-previous: $(cat .deploy-previous)"

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
HOST="$(grep -E '^ALLOWED_HOSTS=' .env | head -n 1 | cut -d '=' -f 2- | cut -d ',' -f 1 | tr -d '[:space:]' || true)"
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

# 6. Tidy up the images this deploy replaced, and say what is running.
echo "pruning the unused images"
docker image prune -f

echo "deployed commit $(git rev-parse HEAD)"
