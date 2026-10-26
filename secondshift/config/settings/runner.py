"""Settings for the separately deployed command runner.

This module deliberately does not import the control-plane settings. The runner
has its own apps, database credentials, authentication token, and network policy.
"""

import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from environ import Env

from secondshift.config.constants import (
    DEFAULT_SANDBOX_IMAGE,
    AppEnv,
    SandboxRuntime,
    Service,
)
from secondshift.config.logging import logging_config

env = Env()
# On a single host the runner shares the data folder, but reads only runner.env
# (its token), never the control plane's .env with the encryption key.
DATA_DIR = Path(env.str(AppEnv.DATA_DIR, default="~/.secondshift")).expanduser()
Env.read_env(DATA_DIR / "runner.env")

# Nothing in the runner signs data (no sessions, no auth cookies), so a
# per-process key is enough and there is nothing to configure.
SECRET_KEY = secrets.token_urlsafe(50)
DEBUG = False
ALLOWED_HOSTS = env.list(
    AppEnv.RUNNER_ALLOWED_HOSTS, default=["127.0.0.1", "localhost"]
)

INSTALLED_APPS = ["django.contrib.contenttypes", "secondshift.runner"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
ROOT_URLCONF = "secondshift.runner.urls"

USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024

RUNNER_WORK_ROOT = Path(
    env.str(AppEnv.RUNNER_WORK_ROOT, default=str(DATA_DIR / "runner"))
).resolve()
RUNNER_WORK_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
RUNNER_DATABASE_URL = env.str(
    AppEnv.RUNNER_DATABASE_URL,
    default=f"sqlite:///{RUNNER_WORK_ROOT / 'runner.sqlite3'}",
)
DATABASES = {
    "default": {
        **env.db_url_config(RUNNER_DATABASE_URL),
        "CONN_MAX_AGE": env.int(AppEnv.RUNNER_DB_CONN_MAX_AGE_SECONDS, default=60),
        "CONN_HEALTH_CHECKS": True,
    }
}

RUNNER_SERVICE_TOKEN = env.str(AppEnv.RUNNER_SERVICE_TOKEN, default="")
RUNNER_IMAGE = env.str(AppEnv.RUNNER_IMAGE, default=DEFAULT_SANDBOX_IMAGE)
RUNNER_RUNTIME = env.str(AppEnv.RUNNER_RUNTIME, default=SandboxRuntime.GVISOR)
RUNNER_REPOSITORY_ROOT = Path(
    env.str(
        AppEnv.RUNNER_REPOSITORY_ROOT,
        default=str(RUNNER_WORK_ROOT / "repositories"),
    )
).resolve()
RUNNER_COMMAND_TIMEOUT_SECONDS = env.int(
    "SECONDSHIFT_RUNNER_COMMAND_TIMEOUT_SECONDS", default=120
)
RUNNER_COMMAND_OUTPUT_MAX_BYTES = env.int(
    "SECONDSHIFT_RUNNER_COMMAND_OUTPUT_MAX_BYTES", default=1024 * 1024
)
RUNNER_MEMORY = env.str("SECONDSHIFT_RUNNER_MEMORY", default="2g")
RUNNER_CPUS = env.str("SECONDSHIFT_RUNNER_CPUS", default="2")
RUNNER_PIDS_LIMIT = env.int("SECONDSHIFT_RUNNER_PIDS_LIMIT", default=256)
RUNNER_SANDBOX_UID = env.int("SECONDSHIFT_RUNNER_SANDBOX_UID", default=os.getuid())
RUNNER_SANDBOX_GID = env.int("SECONDSHIFT_RUNNER_SANDBOX_GID", default=os.getgid())

if len(RUNNER_SERVICE_TOKEN) < 32:
    raise ImproperlyConfigured(
        "SECONDSHIFT_RUNNER_SERVICE_TOKEN must contain at least 32 characters. "
        "On a single host, start SecondShift once to generate it in runner.env."
    )
if RUNNER_RUNTIME not in set(SandboxRuntime):
    raise ImproperlyConfigured(
        f"SECONDSHIFT_RUNNER_RUNTIME must be one of {', '.join(SandboxRuntime)}"
    )
if RUNNER_COMMAND_TIMEOUT_SECONDS < 1:
    raise ImproperlyConfigured("Runner command timeout must be positive")
if RUNNER_COMMAND_OUTPUT_MAX_BYTES < 1024:
    raise ImproperlyConfigured("Runner output limit must be at least 1024 bytes")
if RUNNER_PIDS_LIMIT < 1:
    raise ImproperlyConfigured("Runner PID limit must be positive")
if RUNNER_SANDBOX_UID < 0 or RUNNER_SANDBOX_GID < 0:
    raise ImproperlyConfigured("Runner sandbox UID and GID cannot be negative")
# Each workspace is chowned to the sandbox user. Without root that only works
# for the runner's own user and groups, so fail at startup, not on the first run.
if os.geteuid() != 0 and (
    os.geteuid() != RUNNER_SANDBOX_UID
    or RUNNER_SANDBOX_GID not in {os.getegid(), *os.getgroups()}
):
    raise ImproperlyConfigured(
        "SECONDSHIFT_RUNNER_SANDBOX_UID/GID name another user, which needs a "
        "runner running as root. Unset them to use the runner's own user."
    )

LOGGING = logging_config(
    env.str(AppEnv.LOG_LEVEL, default="INFO").upper(), Service.RUNNER
)

# Internal credential-free repository bundle imports (64 MiB before base64).
DATA_UPLOAD_MAX_MEMORY_SIZE = 90 * 1024 * 1024
