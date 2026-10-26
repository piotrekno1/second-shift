FROM node:24-alpine AS frontend
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS application
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    SECONDSHIFT_ENV=production
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
COPY --from=frontend /app/secondshift/web /app/secondshift/web
# collectstatic loads the models, which need an encryption key. It gets a
# throwaway one that exists only for this command.
RUN uv sync --frozen --no-dev && \
    SECONDSHIFT_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
    DJANGO_SETTINGS_MODULE=secondshift.config.settings.base python manage.py collectstatic --noinput && \
    addgroup --system secondshift && \
    adduser --system --ingroup secondshift --home /app secondshift && \
    chown -R secondshift:secondshift /app

USER secondshift

# Model gateway: serves only /gateway/ to the trusted agent orchestrator.
# Async, so long model streams don't hold a worker each.
# Build with: docker build --target gateway .
FROM application AS gateway
EXPOSE 8001
CMD ["gunicorn", "secondshift.config.app:asgi('gateway')", "--bind", "0.0.0.0:8001", "--worker-class", "uvicorn_worker.UvicornWorker", "--workers", "2", "--graceful-timeout", "600", "--access-logfile", "-"]

# Separately deployed command runner. It receives runner-only settings and DB
# credentials. The host runtime socket is never mounted into web or worker.
# Build with: docker build --target runner .
FROM application AS runner
USER root
ENV SECONDSHIFT_RUNNER_SANDBOX_UID=65534 \
    SECONDSHIFT_RUNNER_SANDBOX_GID=65534
RUN apt-get update && \
    apt-get install --no-install-recommends -y docker.io git && \
    rm -rf /var/lib/apt/lists/*
EXPOSE 8002
CMD ["gunicorn", "secondshift.config.app:wsgi('runner')", "--bind", "0.0.0.0:8002", "--workers", "1", "--access-logfile", "-"]

# Web app (the default target): dashboard, API and admin.
FROM application AS web
EXPOSE 8000
CMD ["gunicorn", "secondshift.config.app:wsgi('web')", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]
