# Badshah Store

Online store for Badshah Store, a provision shop in Saifee Nagar, Indore, India.

Stack: Python 3.12, Django 5.2 LTS, PostgreSQL 16, Celery + Redis, HTMX + Alpine.js + Tailwind CSS.

This repository is a modular monolith. Business logic lives in per-app `services.py`; views stay thin.

## Repo status

This branch is the Phase 1 scaffold only: settings split, empty app shells, custom user model, health endpoint, Docker, linting/typing/testing tooling, and CI. No business models beyond the custom user yet.

## Python version

Python 3.12, pinned via `.python-version`. Uses `uv` for dependency management.

## Local development on Windows

Prerequisites:
- Git
- Docker Desktop (started first)
- `uv` (installed)

From a clean machine:

```bash
# 1. Clone
git clone <repo-url> badshahstore
cd badshahstore

# 2. Copy the env template
copy .env.example .env
# Edit .env if needed. Default dev settings use DJANGO_SETTINGS_MODULE=badshahstore.settings.dev

# 3. Install dependencies
uv sync

# 4. Start the stack with Docker Compose (Postgres 16 + Redis + web + Celery)
docker compose up -d

# 5. Run migrations
uv run python manage.py migrate

# 6. Create a superuser
uv run python manage.py createsuperuser

# 7. (Optional) load initial shop settings, seed data, etc. — added in later phases

# 8. Run the dev server (if not using docker-compose web)
uv run python manage.py runserver
```

Health endpoint: `http://127.0.0.1:8000/health/` returns `{"status": "ok"}` when the database is reachable.

## Tooling

- Format/lint: `ruff`
- Type check: `mypy` with `django-stubs`
- Tests: `pytest` + `pytest-django` against PostgreSQL 16
- Factories: `factory_boy`
- Secret scan: `gitleaks`

Example commands:

```bash
uv run ruff format .
uv run ruff check .
uv run mypy badshahstore
uv run pytest
```

## Docker

`docker-compose.yml` runs: `web`, `db` (PostgreSQL 16), `redis`, `worker` (Celery worker), `beat` (Celery beat).

Postgres and Redis are bound to `127.0.0.1` only and use passwords from `.env`. Inside containers, `DATABASE_URL` and `REDIS_URL` are built from the same `.env` values but use the Docker service names (`db`, `redis`) so the stack can talk to itself without exposing the database to the host network.

The image is built from `docker/Dockerfile` on `python:3.12-slim`, with a pinned `uv` version and dependencies installed into `/opt/venv` (outside the project folder) so the bind-mounted source never conflicts with a local `.venv`.

### Windows commands (Command Prompt)

```cmd
REM Build the images (first time, and after Dockerfile or dependencies change)
docker compose build

REM Start the stack in the background
docker compose up -d

REM Watch containers come up and confirm their state
docker compose ps

REM Follow logs for a single service (useful right after starting)
docker compose logs -f web
docker compose logs -f worker
docker compose logs -f beat

REM Stop and remove containers (keeps the Postgres data volume)
docker compose down

REM Stop and also remove the named Postgres data volume (destroys local data)
docker compose down --volumes
```

What to expect:

- `docker compose build` — builds the `web`, `worker` and `beat` images from the same Dockerfile. First build downloads Python and `uv` and installs dependencies; later builds reuse the dependency layer unless `pyproject.toml` or `uv.lock` change.
- `docker compose up -d` — starts all five services. `web`, `worker` and `beat` wait for `db` and `redis` to pass their healthchecks before starting.
- `docker compose ps` — shows all services with `Up` (healthy where applicable). `web`, `worker` and `beat` should be `Up`; `db` and `redis` should be `Up (healthy)`.
- `docker compose logs -f web` — Django dev server logs, including `Starting development server at http://0.0.0.0:8000/` once the database is reachable.
- `docker compose logs -f worker` — Celery worker startup logs, ending with a ready line once it has connected to Redis.
- `docker compose logs -f beat` — Celery beat startup logs, writing its schedule file under `/tmp` inside the container (nothing ends up in the project folder or Git).

The web service is available at `http://127.0.0.1:8000/`. The health endpoint is `http://127.0.0.1:8000/health/`.

Redis is not published to the host. Containers reach it through the Docker service name `redis`, and the healthcheck authenticates with `redis-cli` inside the container. If you need host-side `redis-cli` access, add a port mapping to the redis service, e.g. `ports: ["127.0.0.1:6380:6379"]` (use a different host port if 6379 is already in use).

Host commands (migrations, createsuperuser, etc.) still run against `127.0.0.1` and must be run after `docker compose up -d` has started the database:

```cmd
REM Run migrations against the running Postgres
uv run python manage.py migrate

REM Create a superuser
uv run python manage.py createsuperuser
```

## Environment and secrets

`.env` is in `.gitignore`. `.env.example` contains placeholders only. Never commit real secrets.

Settings are selected with `DJANGO_SETTINGS_MODULE`:
- dev: `badshahstore.settings.dev`
- prod: `badshahstore.settings.prod`
- test: `badshahstore.settings.test`

## Phases

See `docs/architecture.md` and `docs/decisions.md` for the overall design and decisions.

