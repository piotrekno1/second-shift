"""Names shared across process and app boundaries.

The runner's settings must never import the control plane's settings, which
read its keys. Settings names, and env vars that one module writes and another
reads, live here. What the runner and the control plane agree on lives in
secondshift/runner_protocol. A name only one app uses belongs in that app.
"""

from enum import StrEnum

ENVIRONMENT_VAR = "SECONDSHIFT_ENV"
SETTINGS_MODULE_VAR = "DJANGO_SETTINGS_MODULE"
SERVICE_VAR = "SECONDSHIFT_SERVICE"


# `secondshift up` builds this tag from the installed package.
LOCAL_AGENT_TAG = "secondshift-agent:local"
# process-compose builds this one from the checkout.
DEV_AGENT_TAG = "secondshift-agent:dev"
LOCAL_GATEWAY_URL = "http://127.0.0.1:8001"


class ArtifactStorage(StrEnum):
    FILESYSTEM = "filesystem"
    S3 = "s3"


class AppEnv(StrEnum):
    AGENT_IMAGE = "SECONDSHIFT_AGENT_IMAGE"
    AGENT_NETWORK = "SECONDSHIFT_AGENT_NETWORK"
    AGENT_BROKER_URL = "SECONDSHIFT_AGENT_BROKER_URL"
    DATA_DIR = "SECONDSHIFT_DATA_DIR"
    RUNNER_RUNTIME = "SECONDSHIFT_RUNNER_RUNTIME"
    RUNNER_SERVICE_URL = "SECONDSHIFT_RUNNER_SERVICE_URL"
    RUNNER_SERVICE_TOKEN = "SECONDSHIFT_RUNNER_SERVICE_TOKEN"
    MODEL_GATEWAY_URL = "SECONDSHIFT_MODEL_GATEWAY_URL"
    RUNNER_WORK_ROOT = "SECONDSHIFT_RUNNER_WORK_ROOT"
    ARTIFACT_STORAGE = "SECONDSHIFT_ARTIFACT_STORAGE"
    RUN_OUTPUT_MAX_BYTES = "SECONDSHIFT_RUN_OUTPUT_MAX_BYTES"
    RUN_RETENTION_DAYS = "SECONDSHIFT_RUN_RETENTION_DAYS"
    REPOSITORY_SYNC_INTERVAL_SECONDS = "SECONDSHIFT_REPOSITORY_SYNC_INTERVAL_SECONDS"

    SECRET_KEY = "SECONDSHIFT_SECRET_KEY"
    ENCRYPTION_KEY = "SECONDSHIFT_ENCRYPTION_KEY"
    DATABASE_URL = "SECONDSHIFT_DATABASE_URL"
    CACHE_URL = "SECONDSHIFT_CACHE_URL"
    PUBLIC_URL = "SECONDSHIFT_PUBLIC_URL"
    DEBUG = "SECONDSHIFT_DEBUG"
    ALLOWED_HOSTS = "SECONDSHIFT_ALLOWED_HOSTS"
    CSRF_TRUSTED_ORIGINS = "SECONDSHIFT_CSRF_TRUSTED_ORIGINS"
    LOG_LEVEL = "SECONDSHIFT_LOG_LEVEL"
    RUN_CONCURRENCY = "SECONDSHIFT_RUN_CONCURRENCY"
    RUN_GLOBAL_CONCURRENCY = "SECONDSHIFT_RUN_GLOBAL_CONCURRENCY"
    TASK_QUEUE_LIMIT = "SECONDSHIFT_TASK_QUEUE_LIMIT"
    RUNNER_ALLOWED_HOSTS = "SECONDSHIFT_RUNNER_ALLOWED_HOSTS"

    HTTP_BIND = "SECONDSHIFT_HTTP_BIND"
    HTTP_PROCESSES = "SECONDSHIFT_HTTP_PROCESSES"
    HTTP_TIMEOUT_SECONDS = "SECONDSHIFT_HTTP_TIMEOUT_SECONDS"
    HTTP_GRACEFUL_TIMEOUT_SECONDS = "SECONDSHIFT_HTTP_GRACEFUL_TIMEOUT_SECONDS"
    HTTP_LOG_LEVEL = "SECONDSHIFT_HTTP_LOG_LEVEL"
    HTTP_ACCESS_LOG = "SECONDSHIFT_HTTP_ACCESS_LOG"


GITHUB_HOST = "github.com"
GITHUB_BASE_URL = f"https://{GITHUB_HOST}"


class Environment(StrEnum):
    """Where SecondShift runs. Each one has a settings module of the same name."""

    PRODUCTION = "production"
    LOCAL = "local"
    DEV = "dev"
    TEST = "test"


class Service(StrEnum):
    """The separately started parts of SecondShift."""

    WEB = "web"
    GATEWAY = "gateway"
    RUNNER = "runner"
    WORKER = "worker"


# Served over ASGI, since both hold long requests open (model streams, agent
# runs) while serving others. Gunicorn and the database settings read this.
ASGI_SERVICES = frozenset({Service.GATEWAY, Service.RUNNER})


def settings_module(name: str) -> str:
    return f"secondshift.config.settings.{name}"


def default_agent_image(environment: str | None) -> str:
    """The tag a single-machine mode builds. Servers set a digest instead."""
    return {Environment.LOCAL: LOCAL_AGENT_TAG, Environment.DEV: DEV_AGENT_TAG}.get(
        environment or "", ""
    )


class SandboxRuntime(StrEnum):
    """The Docker runtime that runs command sandboxes."""

    DOCKER = "docker"
    GVISOR = "runsc"


RUNNER_SETTINGS = settings_module(Service.RUNNER)
