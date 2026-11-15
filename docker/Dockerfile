ARG UV_VERSION=0.9.21

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.12-slim

# ---------------------------------------------------------------------------
# Build behaviour
# ---------------------------------------------------------------------------
# Set UV_PROJECT_ENVIRONMENT outside the project folder so the bind-mounted
# source (which has its own local .venv on Windows/dev machines) never
# conflicts with the image's virtual environment.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENV PATH="/opt/venv/bin:$PATH"

# Pinned uv version — update here and in pyproject.toml together.
ARG UV_VERSION=0.9.21

# Whether to include the dev dependency group. Use for local debug images
# only; production images should leave this off.
ARG INSTALL_DEV_GROUP=0

# ---------------------------------------------------------------------------
# uv — copied from the official pinned image so we never download a stale
# or wrong-named release tarball. No curl, gcc, libpq-dev, or pkg-config.
# ---------------------------------------------------------------------------
COPY --from=uv /uv /uvx /usr/local/bin/

RUN uv --version

# ---------------------------------------------------------------------------
# Work directory lives OUTSIDE the virtual environment. The image is built
# from the project root (pyproject.toml + uv.lock are the build context's
# top-level files), so we can COPY them directly.
# ---------------------------------------------------------------------------
WORKDIR /app

# ---------------------------------------------------------------------------
# Dependencies are cached on their own layer. Only pyproject.toml and
# uv.lock change when deps change, so the pip install step is re-run only
# when those files change.
# ---------------------------------------------------------------------------
COPY pyproject.toml uv.lock ./

# Install into /opt/venv. --no-install-project keeps the project itself out
# of the image at build time; the source is bind-mounted at runtime.
RUN uv sync --frozen --no-install-project \
    $( [ "$INSTALL_DEV_GROUP" = "1" ] && echo "--group dev" )

# ---------------------------------------------------------------------------
# The Tailwind CLI (D93)
# ---------------------------------------------------------------------------
# The standalone CLI, pinned by version and by checksum, so the build can never
# download a different file. x64 only: the development PC and the VPS are both
# x64. The arm64 file's checksum is
# 69b1378b8133192d7d2feb12a116fa12d035594f58db3eff215879e4ad8cf39b.
# This image does not copy the source, so the production image that runs the
# build is Slice 53; the dev compose service uses this copy.
ADD --checksum=sha256:7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4 \
    https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64 \
    /usr/local/bin/tailwindcss
RUN chmod 755 /usr/local/bin/tailwindcss

# ---------------------------------------------------------------------------
# Non-root runtime
# ---------------------------------------------------------------------------
RUN useradd --system --create-home --home-dir /home/app app && \
    mkdir -p /app /app/media /app/staticfiles && \
    chown -R app:app /app

USER app
