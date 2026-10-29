"""Settings shared by every environment.

SECONDSHIFT_ENV picks the settings module. It is read from the process
environment, never from .env.

- production: a server. production.py requires every key and adds HTTPS.
- local: `secondshift up` on one machine for one person (local.py).
- dev: a checkout with the Vite dev server (dev.py).
- test: the test suite (test.py).

Defaults are safe for production. Values that differ on a single machine use
`default=SINGLE_HOST` or `default=DEV`.
"""

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP
from environ import Env

from secondshift.config.constants import (
    DEFAULT_SANDBOX_IMAGE,
    ENVIRONMENT_VAR,
    SERVICE_VAR,
    AppEnv,
    Environment,
    SandboxRuntime,
    Service,
)
from secondshift.config.first_run import write_generated_env
from secondshift.config.logging import logging_config
from secondshift.core.static_storage import immutable_static_file

# The secondshift package, and the checkout that contains it.
PACKAGE_DIR = Path(__file__).resolve().parents[2]
BASE_DIR = PACKAGE_DIR.parent
# The built dashboard (npm --prefix dashboard run build).
WEB_DIR = PACKAGE_DIR / "web"

ENVIRONMENT = Environment(os.environ.get(ENVIRONMENT_VAR, Environment.PRODUCTION))
DEV = ENVIRONMENT == Environment.DEV
# One trusted person on one machine. Plain Docker, local repositories and
# generated keys are fine there.
SINGLE_HOST = ENVIRONMENT in {Environment.DEV, Environment.LOCAL}

env = Env()


# Real environment wins, then a checkout's .env, then the data folder's .env.
# A checkout's .env holds development settings. Local mode skips it so it runs
# the same as an installed copy.
if ENVIRONMENT != Environment.LOCAL:
    Env.read_env(BASE_DIR / ".env")
# Everything a single-host install writes: database, run files and runner
# workspaces. On a single host its .env also holds keys made on first start.
DATA_DIR = Path(env.str(AppEnv.DATA_DIR, default="~/.secondshift")).expanduser()
if SINGLE_HOST:
    write_generated_env(DATA_DIR)
Env.read_env(DATA_DIR / ".env")


def sqlalchemy_url(database_url: str) -> str:
    """DBOS uses SQLAlchemy, which needs the psycopg driver named explicitly."""
    for scheme in ("postgres://", "postgresql://"):
        if database_url.startswith(scheme):
            return "postgresql+psycopg://" + database_url.removeprefix(scheme)
    return database_url


# --- Core --------------------------------------------------------------------

# Required in production. Generated into DATA_DIR/.env on a single host.
SECRET_KEY = env.str(AppEnv.SECRET_KEY, default="")
FIELD_ENCRYPTION_KEY = env.str(AppEnv.ENCRYPTION_KEY, default="")
# Off unless asked for, on by default in development. production.py refuses it.
DEBUG = env.bool(AppEnv.DEBUG, default=DEV)
FRONTEND_URL = env.str(AppEnv.PUBLIC_URL, default="http://localhost:5173")
try:
    PUBLIC_HOST = urlsplit(FRONTEND_URL).hostname
except ValueError:
    PUBLIC_HOST = None
ALLOWED_HOSTS = env.list(
    AppEnv.ALLOWED_HOSTS,
    default=["localhost", "127.0.0.1"] if SINGLE_HOST else [PUBLIC_HOST or "localhost"],
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "corsheaders",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.github",
    "axes",
    "health_check",
    "secondshift.core",
    "secondshift.workspaces",
    "secondshift.connections",
    "secondshift.repositories",
    "secondshift.jobs",
    "secondshift.runs",
    "secondshift.gateway",
    "secondshift.mcp_server",
]

MIDDLEWARE = [
    "secondshift.core.middleware.LivenessMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

# Set by secondshift.config.app. The gateway serves only model routes to the
# trusted agent orchestrator, never the command sandbox.
SERVICE = Service(env.str(SERVICE_VAR, default=Service.WEB))
ROOT_URLCONF = (
    "secondshift.gateway.urls"
    if SERVICE == Service.GATEWAY
    else "secondshift.config.urls"
)

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [PACKAGE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "secondshift.workspaces.context_processors.invite",
            ]
        },
    }
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SITE_ID = 1

LOGGING = logging_config(env.str(AppEnv.LOG_LEVEL, default="INFO").upper(), SERVICE)

# --- Database and durable workflows ------------------------------------------

DATABASE_URL = env.str(
    AppEnv.DATABASE_URL, default=f"sqlite:///{DATA_DIR / 'secondshift.sqlite3'}"
)
DATABASES = {
    "default": {
        **env.db_url_config(DATABASE_URL),
        "CONN_MAX_AGE": env.int(AppEnv.DB_CONN_MAX_AGE_SECONDS, default=60),
        "CONN_HEALTH_CHECKS": True,
    }
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    # Web, gateway and worker share one file. WAL lets reads run during a
    # write, IMMEDIATE transactions and a timeout make writers wait their turn
    # instead of failing with "database is locked".
    DATABASES["default"]["OPTIONS"] = {
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        "transaction_mode": "IMMEDIATE",
        "timeout": 20,
    }
# Sign-in rate limits and the health check use the cache. Every process must
# see the same counts, so the default is a table in the application database.
CACHES = {
    "default": env.cache_url(AppEnv.CACHE_URL, default="dbcache://secondshift_cache")
}

DBOS_SYSTEM_DATABASE_URL = sqlalchemy_url(
    env.str(AppEnv.WORKFLOW_DATABASE_URL, default=DATABASE_URL)
)
DBOS_APPLICATION_NAME = "secondshift"
# Fixed, so a deploy still recovers workflows started by the previous version.
DBOS_APPLICATION_VERSION = "0.1.0"
DBOS_QUEUE_NAME = "agent-runs"
DBOS_WORKER_CONCURRENCY = env.int(AppEnv.RUN_CONCURRENCY, default=2)

# --- Static files and run artifacts ------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_IMMUTABLE_FILE_TEST = immutable_static_file
# The built dashboard. In a checkout also its source assets, so admin has its
# icon before the first build.
STATICFILES_DIRS = [
    path for path in (WEB_DIR, BASE_DIR / "dashboard" / "public") if path.is_dir()
]

# Private run output, never served over HTTP. "filesystem" needs a persistent
# volume at SECONDSHIFT_ARTIFACT_ROOT in production, "s3" works with any
# S3-compatible service (AWS, R2, Hetzner, RustFS locally).
ARTIFACT_STORAGE = env.str("SECONDSHIFT_ARTIFACT_STORAGE", default="filesystem")
ARTIFACT_STORAGES = {
    "filesystem": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": env.path(
                "SECONDSHIFT_ARTIFACT_ROOT", default=DATA_DIR / "artifacts"
            ),
            "base_url": None,
        },
    },
    "s3": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env.str("SECONDSHIFT_ARTIFACT_BUCKET", default=""),
            "endpoint_url": env.str("SECONDSHIFT_ARTIFACT_ENDPOINT_URL", default=None),
            "region_name": env.str("SECONDSHIFT_ARTIFACT_REGION", default=None),
            # Unset keys fall back to boto3's credential chain (IAM roles etc.).
            "access_key": env.str("SECONDSHIFT_ARTIFACT_ACCESS_KEY_ID", default=None),
            "secret_key": env.str(
                "SECONDSHIFT_ARTIFACT_SECRET_ACCESS_KEY", default=None
            ),
            "location": env.str("SECONDSHIFT_ARTIFACT_PREFIX", default=""),
            "default_acl": "private",
            "querystring_auth": True,
            "file_overwrite": True,
            "addressing_style": env.str(
                "SECONDSHIFT_ARTIFACT_ADDRESSING_STYLE", default=None
            ),
        },
    },
}
if ARTIFACT_STORAGE not in ARTIFACT_STORAGES:
    raise ImproperlyConfigured(
        f"SECONDSHIFT_ARTIFACT_STORAGE must be one of {sorted(ARTIFACT_STORAGES)}"
    )
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "run_artifacts": ARTIFACT_STORAGES[ARTIFACT_STORAGE],
    "staticfiles": {"BACKEND": "secondshift.core.static_storage.StaticFilesStorage"},
}

# --- Accounts and login ------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Email goes out only through EMAIL_URL, e.g. smtp://user:pass@host:587.
EMAIL_URL = env.str(AppEnv.EMAIL_URL, default="")
DEFAULT_FROM_EMAIL = env.str(AppEnv.FROM_EMAIL, default="webmaster@localhost")
if EMAIL_URL:
    globals().update(env.email_url(AppEnv.EMAIL_URL))
elif SINGLE_HOST:
    # One person at the terminal reads them there.
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    # Nowhere to send them. They are dropped so reset links never reach logs.
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
# Whether an email reaches anyone. Password resets are offered only then.
EMAIL_DELIVERED = bool(EMAIL_URL) or SINGLE_HOST
# Ask people to confirm their address only when an email can reach them.
ACCOUNT_EMAIL_VERIFICATION = "optional" if EMAIL_URL or DEV else "none"

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_ADAPTER = "secondshift.core.adapters.AccountAdapter"
SOCIALACCOUNT_ADAPTER = "secondshift.core.adapters.SocialAccountAdapter"
# New accounts (email or GitHub) are refused unless enabled, open in dev.
SIGNUP_ENABLED = env.bool("SECONDSHIFT_SIGNUP_ENABLED", default=DEV)
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
SOCIALACCOUNT_PROVIDERS = {
    "github": {
        "APP": {
            "client_id": env.str("GITHUB_CLIENT_ID", default=""),
            "secret": env.str("GITHUB_CLIENT_SECRET", default=""),
            "key": "",
        },
        "SCOPE": ["read:user", "user:email"],
    }
}

# Lock out an IP after repeated failed logins (admin and allauth alike).
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("AXES_COOLOFF_MINUTES", default=30))
AXES_LOCKOUT_PARAMETERS = ["ip_address"]
AXES_RESET_ON_SUCCESS = True
# Number of trusted reverse proxies that append to X-Forwarded-For (1 behind
# kamal-proxy). With 0 the header is ignored so clients cannot spoof their IP.
AXES_IPWARE_PROXY_COUNT = env.int("SECONDSHIFT_TRUSTED_PROXY_COUNT", default=0) or None
AXES_IPWARE_META_PRECEDENCE_ORDER = (
    ("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR")
    if AXES_IPWARE_PROXY_COUNT
    else ("REMOTE_ADDR",)
)

# --- Browser security --------------------------------------------------------

CSRF_TRUSTED_ORIGINS = env.list(
    AppEnv.CSRF_TRUSTED_ORIGINS, default=[FRONTEND_URL.rstrip("/")]
)
X_FRAME_OPTIONS = "DENY"
# PrimeVue injects component styles at runtime, so styles need 'unsafe-inline'.
# Scripts stay restricted to same-origin files.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE, "https://fonts.googleapis.com"],
    "font-src": [CSP.SELF, "https://fonts.gstatic.com"],
    "img-src": [CSP.SELF, "data:"],
    "connect-src": [CSP.SELF],
    "form-action": [CSP.SELF, "https://github.com"],
    "frame-ancestors": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "object-src": [CSP.NONE],
}

# --- Repositories and runner -------------------------------------------------

# Reading repositories from this machine's filesystem is a development feature.
LOCAL_REPOSITORIES_ENABLED = SINGLE_HOST and env.bool(
    "SECONDSHIFT_LOCAL_REPOSITORIES_ENABLED", default=True
)
# The worker copies a local repository's commit here for the runner. It is the
# runner's repository folder, so both default to the same place.
LOCAL_REPOSITORY_MIRROR_ROOT = Path(
    env.str(
        AppEnv.RUNNER_REPOSITORY_ROOT,
        default=str(DATA_DIR / "runner" / "repositories"),
    )
).resolve()
# Run limits. These are the defaults. A workspace owner can choose other values
# for the workspace or a job, up to the *_CEILING maximums. Repository manifests
# cannot set them. The values that apply are frozen into each execution plan.
AGENT_MAX_TURNS = env.int("SECONDSHIFT_AGENT_MAX_TURNS", default=24)
AGENT_MAX_TOOL_CALLS = env.int("SECONDSHIFT_AGENT_MAX_TOOL_CALLS", default=64)
AGENT_MAX_TOOL_RESULT_CHARS = env.int(
    "SECONDSHIFT_AGENT_MAX_TOOL_RESULT_CHARS", default=65_536
)
if AGENT_MAX_TURNS < 1:
    raise ImproperlyConfigured("SECONDSHIFT_AGENT_MAX_TURNS must be positive")
if AGENT_MAX_TOOL_CALLS < 0:
    raise ImproperlyConfigured("SECONDSHIFT_AGENT_MAX_TOOL_CALLS cannot be negative")
if AGENT_MAX_TOOL_RESULT_CHARS < 1:
    raise ImproperlyConfigured(
        "SECONDSHIFT_AGENT_MAX_TOOL_RESULT_CHARS must be positive"
    )
# The highest limits a workspace owner can choose for a workspace or a job.
# Each defaults to its limit, so owners can only lower it until this is raised.
AGENT_MAX_TURNS_CEILING = env.int(
    "SECONDSHIFT_AGENT_MAX_TURNS_CEILING", default=AGENT_MAX_TURNS
)
AGENT_MAX_TOOL_CALLS_CEILING = env.int(
    "SECONDSHIFT_AGENT_MAX_TOOL_CALLS_CEILING", default=AGENT_MAX_TOOL_CALLS
)
if AGENT_MAX_TURNS_CEILING < AGENT_MAX_TURNS:
    raise ImproperlyConfigured(
        "SECONDSHIFT_AGENT_MAX_TURNS_CEILING cannot be below "
        "SECONDSHIFT_AGENT_MAX_TURNS"
    )
if AGENT_MAX_TOOL_CALLS_CEILING < AGENT_MAX_TOOL_CALLS:
    raise ImproperlyConfigured(
        "SECONDSHIFT_AGENT_MAX_TOOL_CALLS_CEILING cannot be below "
        "SECONDSHIFT_AGENT_MAX_TOOL_CALLS"
    )
RUNNER_RUNTIME = env.str(AppEnv.RUNNER_RUNTIME, default=SandboxRuntime.DOCKER)
RUNNER_IMAGE = env.str(AppEnv.RUNNER_IMAGE, default=DEFAULT_SANDBOX_IMAGE)
# The orchestrator receives only this runner-scoped credential. It never receives
# the runtime socket or runner database credentials.
# On a single host the runner and gateway listen next to the web app.
RUNNER_SERVICE_URL = env.str(
    "SECONDSHIFT_RUNNER_SERVICE_URL",
    default="http://127.0.0.1:8002" if SINGLE_HOST else "",
)
RUNNER_SERVICE_TOKEN = env.str(AppEnv.RUNNER_SERVICE_TOKEN, default="")
# The trusted agent orchestrator calls this internal gateway. Command sandboxes
# always use --network=none and never receive its run-scoped token.
MODEL_GATEWAY_URL = env.str(
    "SECONDSHIFT_MODEL_GATEWAY_URL",
    default="http://127.0.0.1:8001" if SINGLE_HOST else "",
)
RUN_TIMEOUT_SECONDS = env.int("SECONDSHIFT_RUN_TIMEOUT_SECONDS", default=900)
RUN_TIMEOUT_SECONDS_CEILING = env.int(
    "SECONDSHIFT_RUN_TIMEOUT_SECONDS_CEILING", default=RUN_TIMEOUT_SECONDS
)
if RUN_TIMEOUT_SECONDS < 1:
    raise ImproperlyConfigured("SECONDSHIFT_RUN_TIMEOUT_SECONDS must be positive")
if RUN_TIMEOUT_SECONDS_CEILING < RUN_TIMEOUT_SECONDS:
    raise ImproperlyConfigured(
        "SECONDSHIFT_RUN_TIMEOUT_SECONDS_CEILING cannot be below "
        "SECONDSHIFT_RUN_TIMEOUT_SECONDS"
    )
RUN_WORK_ROOT = env.path("SECONDSHIFT_WORK_ROOT", default=DATA_DIR / "work")
RUN_OUTPUT_MAX_BYTES = env.int(
    "SECONDSHIFT_RUN_OUTPUT_MAX_BYTES", default=5 * 1024 * 1024
)
RUN_OUTPUT_EXCERPT_BYTES = 16 * 1024
# The most earlier reports a job can ask for with `history` in its manifest.
JOB_HISTORY_MAX = env.int("SECONDSHIFT_JOB_HISTORY_MAX", default=5)
if JOB_HISTORY_MAX < 0:
    raise ImproperlyConfigured("SECONDSHIFT_JOB_HISTORY_MAX cannot be negative")
# The longest an API token can last. Its creator can choose a shorter time.
API_TOKEN_MAX_DAYS = env.int("SECONDSHIFT_API_TOKEN_MAX_DAYS", default=90)
if API_TOKEN_MAX_DAYS < 1:
    raise ImproperlyConfigured("SECONDSHIFT_API_TOKEN_MAX_DAYS must be positive")
# Requests to /mcp each person can make per minute, across all their API tokens.
MCP_RATE_LIMIT = env.int("SECONDSHIFT_MCP_RATE_LIMIT", default=300)
if MCP_RATE_LIMIT < 1:
    raise ImproperlyConfigured("SECONDSHIFT_MCP_RATE_LIMIT must be positive")
# Total text a run starts with from its job's earlier reports, newest first.
RUN_HISTORY_MAX_CHARS = env.int("SECONDSHIFT_RUN_HISTORY_MAX_CHARS", default=65_536)
if RUN_HISTORY_MAX_CHARS < 1:
    raise ImproperlyConfigured("SECONDSHIFT_RUN_HISTORY_MAX_CHARS must be positive")
# Finished runs, their output and DBOS records are deleted after this. 0 keeps them.
RUN_RETENTION_DAYS = env.int("SECONDSHIFT_RUN_RETENTION_DAYS", default=90)
# How often the worker syncs local repositories whose branch has new commits.
# 0 turns automatic sync off.
REPOSITORY_SYNC_INTERVAL_SECONDS = env.int(
    "SECONDSHIFT_REPOSITORY_SYNC_INTERVAL_SECONDS", default=300
)

# --- Outbound calls and model gateway ----------------------------------------

# Webhooks and model providers may only reach public HTTPS endpoints unless
# local targets are allowed (HTTP, localhost, private networks): dev only.
OUTBOUND_ALLOW_LOCAL = SINGLE_HOST and env.bool(
    "SECONDSHIFT_OUTBOUND_ALLOW_LOCAL", default=True
)
OUTBOUND_TIMEOUT_SECONDS = 10
# Per-run token budget (input + output + cache tokens), largest accepted
# request body, and how long to wait between streamed chunks.
MODEL_RUN_TOKEN_BUDGET = env.int(
    "SECONDSHIFT_MODEL_RUN_TOKEN_BUDGET", default=2_000_000
)
MODEL_RUN_TOKEN_BUDGET_CEILING = env.int(
    "SECONDSHIFT_MODEL_RUN_TOKEN_BUDGET_CEILING", default=MODEL_RUN_TOKEN_BUDGET
)
if MODEL_RUN_TOKEN_BUDGET < 1:
    raise ImproperlyConfigured("SECONDSHIFT_MODEL_RUN_TOKEN_BUDGET must be positive")
if MODEL_RUN_TOKEN_BUDGET_CEILING < MODEL_RUN_TOKEN_BUDGET:
    raise ImproperlyConfigured(
        "SECONDSHIFT_MODEL_RUN_TOKEN_BUDGET_CEILING cannot be below "
        "SECONDSHIFT_MODEL_RUN_TOKEN_BUDGET"
    )
GATEWAY_MAX_BODY_BYTES = 32 * 1024 * 1024
GATEWAY_READ_TIMEOUT_SECONDS = 600

# GitHub App credentials belong only to the control plane, never to sandboxes.
GITHUB_APP_ID = env.str("SECONDSHIFT_GITHUB_APP_ID", default="")
GITHUB_APP_SLUG = env.str("SECONDSHIFT_GITHUB_APP_SLUG", default="")
GITHUB_APP_CLIENT_ID = env.str("SECONDSHIFT_GITHUB_APP_CLIENT_ID", default="")
GITHUB_APP_CLIENT_SECRET = env.str("SECONDSHIFT_GITHUB_APP_CLIENT_SECRET", default="")
GITHUB_APP_PRIVATE_KEY = env.str(
    "SECONDSHIFT_GITHUB_APP_PRIVATE_KEY", default=""
).replace("\\n", "\n")
