# DBOS needs SQLite subsecond timestamps (3.42+). Share one Python/OS base.
FROM python:3.13.11-slim-trixie@sha256:2b9c9803c6a287cafa0a8c917211dddd23dcd2016f049690ee5219f5d3f1636e AS python-base
RUN python -c "import sqlite3; assert sqlite3.sqlite_version_info >= (3, 42, 0), 'DBOS requires SQLite 3.42 or newer'"

FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS frontend
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/index.html dashboard/vite.config.ts dashboard/tsconfig*.json ./
COPY dashboard/src/ ./src/
COPY dashboard/public/ ./public/
RUN npm run build

# Build dependencies and static files without retaining uv or its build cache.
FROM python-base AS builder
COPY --from=ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca /usr/local/bin/uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy SECONDSHIFT_ENV=production PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE manage.py ./
COPY secondshift/ ./secondshift/
COPY --from=frontend /app/secondshift/web /app/secondshift/web
# Install non-editably. Collect from /tmp to use the installed package rather
# than the source checkout. Static files then live inside the copied venv.
# Keep the SPA HTML. Its assets live only in STATIC_ROOT after collection.
# API documentation still ships, without its development source maps.
# Refresh the local wheel when application source or bundled frontend changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --reinstall-package secondshift && \
    cd /tmp && \
    SECONDSHIFT_ENCRYPTION_KEY="$(/app/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
    DJANGO_SETTINGS_MODULE=secondshift.config.settings.base /app/.venv/bin/python -m django collectstatic --noinput && \
    find /app/.venv/lib -path '*/ninja/static/*.map' -delete && \
    rm -r /app/.venv/lib/python3.13/site-packages/secondshift/web/assets

FROM docker:28-cli@sha256:625d9431a9f54c5a2bc90f24f0e1c3d55b1349fd857dd85035f98c2c9acbdd4d AS docker-cli

# One image. Select the web, gateway, worker or runner command at deployment.
# Match the builder's Python version and Debian release for native wheels.
FROM python-base AS application
ARG VERSION=unknown
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/piotrekno1/second-shift" \
    org.opencontainers.image.version=$VERSION \
    org.opencontainers.image.revision=$REVISION \
    org.opencontainers.image.licenses="Apache-2.0"
ENV PATH="/app/.venv/bin:$PATH" \
    SECONDSHIFT_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SECONDSHIFT_DATA_DIR=/var/lib/secondshift \
    SECONDSHIFT_RUNNER_SANDBOX_UID=10001 \
    SECONDSHIFT_RUNNER_SANDBOX_GID=10001
WORKDIR /app

# Docker talks to a host daemon. No daemon is started inside this container.
RUN apt-get update && \
    apt-get install --no-install-recommends -y git && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 10001 secondshift && \
    useradd --uid 10001 --gid secondshift --home-dir /var/lib/secondshift --no-create-home secondshift && \
    install -d -o secondshift -g secondshift -m 0700 /var/lib/secondshift
# The official CLI binary is self-contained. Omit dockerd, containerd and plugins.
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=builder /app/.venv /app/.venv
COPY manage.py ./

# Only the runner should override this user and receive the host Docker socket.
# Mount persistent state at SECONDSHIFT_DATA_DIR. Migrations are a separate job.
USER 10001:10001
EXPOSE 8000 8001 8002
CMD ["gunicorn", "--config", "python:secondshift.config.gunicorn"]
