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

`docker-compose.yml` runs: `web`, `postgres` (16), `redis`, `celery_worker`, `celery_beat`.

Postgres and Redis are bound to `127.0.0.1` only and use passwords from `.env`.

## Environment and secrets

`.env` is in `.gitignore`. `.env.example` contains placeholders only. Never commit real secrets.

Settings are selected with `DJANGO_SETTINGS_MODULE`:
- dev: `badshahstore.settings.dev`
- prod: `badshahstore.settings.prod`
- test: `badshahstore.settings.test`

## Phases

See `docs/architecture.md` and `docs/decisions.md` for the overall design and decisions.

