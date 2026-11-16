"""The production stack files: image, Compose, nginx and the deploy script (D95).

Docker and nginx are not on the coding agent's command list, so every rule is
checked by reading the file as text and comparing it with the decision. A real
run of all of it happens on the staging server in Slice 55.

There is no YAML library in the project, so the Compose file is cut into service
blocks with a regular expression on the two-space service headers.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from apps.catalog.csv_import import MAX_FILE_BYTES
from apps.catalog.validators import MAX_IMAGE_BYTES

ROOT = Path(settings.BASE_DIR)

DEV_DOCKERFILE = ROOT / "docker" / "Dockerfile"
PROD_DOCKERFILE = ROOT / "docker" / "Dockerfile.prod"
PROD_COMPOSE = ROOT / "docker-compose.prod.yml"
NGINX_CONF = ROOT / "deploy" / "nginx" / "default.conf"
DEPLOY_SCRIPT = ROOT / "deploy" / "deploy.sh"
PROD_ENV_EXAMPLE = ROOT / "deploy" / "production.env.example"
DOCKERIGNORE = ROOT / ".dockerignore"

SETTINGS_FILES = (
    ROOT / "badshahstore" / "settings" / "base.py",
    ROOT / "badshahstore" / "settings" / "prod.py",
)

# The service names sit at exactly two spaces of indentation, so this matches
# them and nothing inside a service.
SERVICE_HEADER = re.compile(r"^  ([a-z][a-z0-9_-]*):$", re.MULTILINE)

# Every `env("NAME"` and `env.int("NAME"` style read in a settings module.
ENV_READ = re.compile(r"env(?:\.\w+)?\(\"([A-Z0-9_]+)\"")

IMMUTABLE_CACHE = 'add_header Cache-Control "public, max-age=31536000, immutable" always;'


def _read(path: Path) -> str:
    """Read one file as UTF-8 text; a missing file fails with its own path."""
    assert path.is_file(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def _services() -> dict[str, str]:
    """Cut the production Compose file into one text block per service."""
    text = _read(PROD_COMPOSE)
    # Only what lies between the top-level `services:` and the top-level
    # `volumes:`; the `x-` anchors above it have two-space keys of their own.
    service_section = text.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    parts = SERVICE_HEADER.split(service_section)
    # re.split with one group returns [before, name, block, name, block, ...].
    return {parts[index]: parts[index + 1] for index in range(1, len(parts), 2)}


def _nginx_servers() -> list[str]:
    """Return the vhost file's server blocks, port 80 first and port 443 second."""
    return _read(NGINX_CONF).split("server {")[1:]


def _location(block: str, name: str) -> str:
    """Return the body of one `location <name> { ... }` inside a server block."""
    opener = f"location {name} {{"
    start = block.index(opener) + len(opener)
    return block[start:].split("\n    }", 1)[0]


# ---------------------------------------------------------------------------
# docker-compose.prod.yml
# ---------------------------------------------------------------------------
def test_prod_compose_publishes_ports_only_for_nginx() -> None:
    services = _services()
    published = {
        name: block for name, block in services.items() if re.search(r"^    ports:$", block, re.M)
    }

    assert sorted(published) == ["nginx"]
    assert '- "80:80"' in published["nginx"]
    assert '- "443:443"' in published["nginx"]


def test_prod_compose_has_no_bind_mount_of_the_source() -> None:
    text = _read(PROD_COMPOSE)

    assert "- .:/app" not in text
    # The one host folder that is mounted on purpose is the certificate folder;
    # nothing else may come from the server's own filesystem.
    host_mounts = [line.strip() for line in text.splitlines() if re.search(r"^\s+- /", line)]
    assert host_mounts == ["- /etc/badshahstore/certs:/etc/nginx/certs:ro"]


def test_prod_compose_builds_from_the_prod_dockerfile_without_the_dev_group() -> None:
    text = _read(PROD_COMPOSE)

    assert "dockerfile: docker/Dockerfile.prod" in text
    assert "target: app" in text
    assert "target: nginx" in text
    assert "INSTALL_DEV_GROUP" not in text
    assert "docker/Dockerfile\n" not in text


def test_prod_compose_restarts_every_service_unless_stopped() -> None:
    services = _services()

    assert sorted(services) == ["beat", "db", "nginx", "redis", "web", "worker"]
    for name, block in services.items():
        assert "restart: unless-stopped" in block, name


def test_prod_compose_forces_the_production_settings_module() -> None:
    services = _services()

    for name in ("web", "worker", "beat"):
        assert "DJANGO_SETTINGS_MODULE: badshahstore.settings.prod" in services[name], name
    # The value is written out, never taken from .env.
    assert "${DJANGO_SETTINGS_MODULE" not in _read(PROD_COMPOSE)


def test_prod_compose_mounts_the_certificates_read_only() -> None:
    assert "/etc/badshahstore/certs:/etc/nginx/certs:ro" in _services()["nginx"]
    nginx_conf = _read(NGINX_CONF)
    assert "/etc/nginx/certs/origin.pem" in nginx_conf
    assert "/etc/nginx/certs/origin.key" in nginx_conf


def test_prod_compose_limits_the_container_logs() -> None:
    text = _read(PROD_COMPOSE)

    assert "x-logging: &logging" in text
    assert "max-size: 10m" in text
    assert 'max-file: "3"' in text
    assert text.count("logging: *logging") == len(_services())


def test_prod_web_command_is_gunicorn_with_two_workers() -> None:
    dockerfile = _read(PROD_DOCKERFILE)

    assert '"gunicorn", "badshahstore.wsgi:application"' in dockerfile
    assert '"--bind", "0.0.0.0:8000"' in dockerfile
    assert '"--workers", "2"' in dockerfile
    assert '"--worker-tmp-dir", "/dev/shm"' in dockerfile
    # The image's own CMD is the command, so the Compose file must not override it.
    assert "command:" not in _services()["web"]


# ---------------------------------------------------------------------------
# deploy/nginx/default.conf
# ---------------------------------------------------------------------------
def test_nginx_static_and_media_are_cached_for_a_year_immutable() -> None:
    https_server = _nginx_servers()[1]

    static = _location(https_server, "/static/")
    assert "alias /srv/static/;" in static
    assert IMMUTABLE_CACHE in static

    media = _location(https_server, "/media/")
    assert "alias /srv/media/;" in media
    assert IMMUTABLE_CACHE in media


def test_nginx_serves_sw_manifest_and_offline_without_a_cache_header() -> None:
    https_server = _nginx_servers()[1]

    for name in ("= /sw.js", "= /manifest.webmanifest", "= /offline/"):
        location = _location(https_server, name)
        assert "proxy_pass http://web:8000;" in location, name
        assert "add_header" not in location, name
        assert "proxy_cache" not in location, name
        assert "expires" not in location, name


def test_nginx_never_touches_the_security_headers() -> None:
    text = _read(NGINX_CONF)

    # The policy carries a nonce per request, so a proxy that rewrote or hid it
    # would break the page's one inline script (D93).
    assert "proxy_hide_header" not in text
    for line in text.splitlines():
        if "add_header" in line:
            assert "Content-Security-Policy" not in line
            assert "Permissions-Policy" not in line


def test_nginx_gzip_types_cover_text_json_and_svg() -> None:
    text = _read(NGINX_CONF)
    gzip_block = text.split("gzip_types", 1)[1].split(";", 1)[0]
    types = set(gzip_block.split())

    assert "gzip on;" in text
    assert "gzip_vary on;" in text
    assert types == {
        "text/plain",
        "text/css",
        "text/xml",
        "application/javascript",
        "text/javascript",
        "application/json",
        "application/manifest+json",
        "application/xml",
        "image/svg+xml",
    }


def test_nginx_port_80_redirects_everything_except_health() -> None:
    port_80 = _nginx_servers()[0]

    assert "listen 80;" in port_80
    assert "proxy_pass http://web:8000;" in _location(port_80, "= /health/")
    assert "return 301 https://$host$request_uri;" in _location(port_80, "/")

    port_443 = _nginx_servers()[1]
    assert "listen 443 ssl;" in port_443
    assert "http2 on;" in port_443


def test_nginx_hides_dotfiles_and_the_server_version() -> None:
    text = _read(NGINX_CONF)

    assert "server_tokens off;" in text
    dotfiles = _location(_nginx_servers()[1], "~ /\\.")
    assert "return 404;" in dotfiles


def test_nginx_upload_limit_covers_the_photo_and_csv_limits() -> None:
    text = _read(NGINX_CONF)
    match = re.search(r"client_max_body_size (\d+)m;", text)

    assert match is not None
    limit = int(match.group(1)) * 1024 * 1024
    assert limit > MAX_IMAGE_BYTES
    assert limit > MAX_FILE_BYTES


def test_nginx_forwards_the_scheme_host_and_client_address() -> None:
    text = _read(NGINX_CONF)

    assert "proxy_set_header Host $host;" in text
    assert "proxy_set_header X-Real-IP $remote_addr;" in text
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in text
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in text


# ---------------------------------------------------------------------------
# docker/Dockerfile.prod
# ---------------------------------------------------------------------------
def test_prod_dockerfile_uses_the_same_tailwind_checksum_and_uv_version_as_dev() -> None:
    prod = _read(PROD_DOCKERFILE)
    dev = _read(DEV_DOCKERFILE)

    uv_version = re.compile(r"ARG UV_VERSION=([0-9.]+)")
    checksum = re.compile(r"ADD --checksum=sha256:([0-9a-f]{64})")

    prod_uv = uv_version.search(prod)
    dev_uv = uv_version.search(dev)
    prod_sum = checksum.search(prod)
    dev_sum = checksum.search(dev)

    assert prod_uv is not None and dev_uv is not None
    assert prod_uv.group(1) == dev_uv.group(1)
    assert prod_sum is not None and dev_sum is not None
    assert prod_sum.group(1) == dev_sum.group(1)
    assert "tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64" in prod


def test_prod_dockerfile_builds_the_css_before_collectstatic() -> None:
    prod = _read(PROD_DOCKERFILE)

    assert prod.index("static/css/app.css --minify") < prod.index("collectstatic --noinput")
    # collectstatic needs the production settings, which refuse to start without
    # these; the dummy values must not be left in an ENV line.
    assert "DJANGO_SETTINGS_MODULE=badshahstore.settings.prod" in prod
    assert "ENV SECRET_KEY" not in prod


def test_prod_dockerfile_installs_without_the_dev_group() -> None:
    prod = _read(PROD_DOCKERFILE)

    assert "uv sync --frozen --no-install-project --no-dev" in prod
    assert "--group dev" not in prod


def test_prod_dockerfile_runs_as_a_non_root_user() -> None:
    prod = _read(PROD_DOCKERFILE)

    assert "useradd" in prod
    assert "USER app" in prod
    assert prod.index("USER app") > prod.index("useradd")
    assert "EXPOSE 8000" in prod


def test_prod_dockerfile_copies_the_static_files_into_the_nginx_image() -> None:
    prod = _read(PROD_DOCKERFILE)
    nginx_stage = prod.split("FROM nginx:", 1)[1]

    assert "COPY --from=app /app/staticfiles /srv/static" in nginx_stage
    assert "deploy/nginx/default.conf" in nginx_stage


def test_dockerignore_keeps_secrets_and_junk_out_of_the_image() -> None:
    entries = [line.strip() for line in _read(DOCKERIGNORE).splitlines() if line.strip()]

    for entry in (
        "media",
        "staticfiles",
        "static/css/app.css",
        "node_modules",
        "docs",
        "tools",
        "tests",
        "**/tests",
    ):
        assert entry in entries, entry
    # The secret rules the file already had are still there.
    assert ".env" in entries
    assert "!.env.example" in entries


# ---------------------------------------------------------------------------
# deploy/deploy.sh
# ---------------------------------------------------------------------------
def test_deploy_script_stops_on_error_and_never_deletes_volumes() -> None:
    script = _read(DEPLOY_SCRIPT)

    assert "set -euo pipefail" in script
    # Comments are read out; only a real command line can delete data.
    commands = [
        line for line in script.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    for forbidden in ("down -v", "volume rm", "prune --volumes", "--volumes"):
        assert not [line for line in commands if forbidden in line], forbidden
    # No automatic rollback either: a migration may already have run.
    assert "deploy-previous" in script


def test_deploy_script_runs_build_migrate_up_and_health_in_that_order() -> None:
    script = _read(DEPLOY_SCRIPT)

    steps = [
        "$COMPOSE build",
        "migrate --noinput",
        "check --deploy",
        "up -d --remove-orphans",
        "curl -fsS",
    ]
    positions = [script.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "http://127.0.0.1/health/" in script
    assert '-H "Host: $HOST"' in script


def test_deploy_script_does_not_source_the_env_file() -> None:
    script = _read(DEPLOY_SCRIPT)

    assert "source .env" not in script
    assert ". .env" not in script
    # The one value it needs is read with plain shell.
    assert "grep -E '^ALLOWED_HOSTS='" in script


# ---------------------------------------------------------------------------
# deploy/production.env.example
# ---------------------------------------------------------------------------
def _env_names() -> set[str]:
    """Collect every environment variable the settings modules read."""
    names: set[str] = set()
    for path in SETTINGS_FILES:
        names.update(ENV_READ.findall(_read(path)))
    return names


# Values an example file may hold: the placeholders, the flags, a plain lowercase
# word or host, a number, and the few settings paths and header pairs below.
SAFE_EXACT_VALUES = {
    "",
    "CHANGE_ME",
    "https://shop.example.com",
    "badshahstore.settings.prod",
    "HTTP_CF_CONNECTING_IP",
    "HTTP_X_FORWARDED_PROTO,https",
    "django.core.mail.backends.smtp.EmailBackend",
    "apps.notifications.sms.Msg91SmsBackend",
    "Badshah Stores <orders@shop.example.com>",
}


def _is_safe_value(value: str) -> bool:
    """True for a placeholder, a flag, a plain word or host, or a number."""
    if value in SAFE_EXACT_VALUES:
        return True
    if re.fullmatch(r"[a-z0-9.-]+", value):
        return True
    return re.fullmatch(r"\d+", value) is not None


def test_production_env_example_names_every_variable_prod_py_reads() -> None:
    names = _env_names()
    example = _read(PROD_ENV_EXAMPLE)

    assert {"SECRET_KEY", "DATABASE_URL", "R2_BUCKET_NAME"} <= names
    missing = sorted(name for name in names if f"\n{name}=" not in example)
    assert missing == []


def test_production_env_example_holds_no_real_secret() -> None:
    for line in _read(PROD_ENV_EXAMPLE).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        assert separator == "=", stripped
        assert _is_safe_value(value), (name, value)


def test_deploy_files_use_lf_line_endings() -> None:
    for path in (PROD_DOCKERFILE, PROD_COMPOSE, NGINX_CONF, DEPLOY_SCRIPT, PROD_ENV_EXAMPLE):
        assert b"\r\n" not in path.read_bytes(), path
