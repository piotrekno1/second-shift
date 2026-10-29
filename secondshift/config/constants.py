"""Names shared by the settings modules, entry points and the launcher.

The runner's settings must never import the control plane's settings, which
read its keys. Values both need live here instead.
"""

from enum import StrEnum

EXECUTE_RUN_WORKFLOW = "secondshift.execute_run"
SCHEDULED_JOB_WORKFLOW = "secondshift.scheduled_job"

ENVIRONMENT_VAR = "SECONDSHIFT_ENV"
SETTINGS_MODULE_VAR = "DJANGO_SETTINGS_MODULE"
SERVICE_VAR = "SECONDSHIFT_SERVICE"


GUNICORN_CONFIG = "python:secondshift.config.gunicorn"


class AppEnv(StrEnum):
    DATA_DIR = "SECONDSHIFT_DATA_DIR"
    RUNNER_RUNTIME = "SECONDSHIFT_RUNNER_RUNTIME"
    RUNNER_IMAGE = "SECONDSHIFT_RUNNER_IMAGE"
    RUNNER_SERVICE_URL = "SECONDSHIFT_RUNNER_SERVICE_URL"
    RUNNER_SERVICE_TOKEN = "SECONDSHIFT_RUNNER_SERVICE_TOKEN"
    MODEL_GATEWAY_URL = "SECONDSHIFT_MODEL_GATEWAY_URL"
    RUNNER_WORK_ROOT = "SECONDSHIFT_RUNNER_WORK_ROOT"
    RUNNER_REPOSITORY_ROOT = "SECONDSHIFT_RUNNER_REPOSITORY_ROOT"

    SECRET_KEY = "SECONDSHIFT_SECRET_KEY"
    ENCRYPTION_KEY = "SECONDSHIFT_ENCRYPTION_KEY"
    DATABASE_URL = "SECONDSHIFT_DATABASE_URL"
    CACHE_URL = "SECONDSHIFT_CACHE_URL"
    PUBLIC_URL = "SECONDSHIFT_PUBLIC_URL"
    DEBUG = "SECONDSHIFT_DEBUG"
    ALLOWED_HOSTS = "SECONDSHIFT_ALLOWED_HOSTS"
    CSRF_TRUSTED_ORIGINS = "SECONDSHIFT_CSRF_TRUSTED_ORIGINS"
    LOG_LEVEL = "SECONDSHIFT_LOG_LEVEL"
    DB_CONN_MAX_AGE_SECONDS = "SECONDSHIFT_DB_CONN_MAX_AGE_SECONDS"
    WORKFLOW_DATABASE_URL = "SECONDSHIFT_WORKFLOW_DATABASE_URL"
    RUN_CONCURRENCY = "SECONDSHIFT_RUN_CONCURRENCY"
    RUNNER_ALLOWED_HOSTS = "SECONDSHIFT_RUNNER_ALLOWED_HOSTS"
    RUNNER_DATABASE_URL = "SECONDSHIFT_RUNNER_DATABASE_URL"
    RUNNER_DB_CONN_MAX_AGE_SECONDS = "SECONDSHIFT_RUNNER_DB_CONN_MAX_AGE_SECONDS"
    EMAIL_URL = "SECONDSHIFT_EMAIL_URL"
    FROM_EMAIL = "SECONDSHIFT_FROM_EMAIL"


GITHUB_HOST = "github.com"
GITHUB_BASE_URL = f"https://{GITHUB_HOST}"
GITHUB_SSH_PREFIX = f"git@{GITHUB_HOST}:"
GITHUB_REPOSITORY_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
MAX_REPOSITORY_BUNDLE_BYTES = 64 * 1024 * 1024
GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/readonly"
# The model gateway refuses a run past its token budget with this message.
TOKEN_BUDGET_MESSAGE = "This run has used its token budget."

# How much of a report a run keeps as its summary. Webhooks send the summary.
SUMMARY_MAX_CHARS = 4_000


class GitHubMCPTool(StrEnum):
    """Read tools for pull requests, commits, and Actions runs.

    Actions tools that trigger runs, list artifacts, or return download links
    are left out or blocked by method.
    """

    SEARCH_PULL_REQUESTS = "search_pull_requests"
    PULL_REQUEST_READ = "pull_request_read"
    LIST_COMMITS = "list_commits"
    GET_COMMIT = "get_commit"
    ACTIONS_LIST = "actions_list"
    ACTIONS_GET = "actions_get"
    GET_JOB_LOGS = "get_job_logs"


GITHUB_MCP_TOOLS = tuple(GitHubMCPTool)

SENTRY_MCP_URL = "https://mcp.sentry.dev/mcp"
# Sentry organization and project slugs.
SENTRY_SLUG_PATTERN = r"[a-z0-9][a-z0-9_\-]{0,63}"


class SentryMCPTool(StrEnum):
    """Read tools for errors, issues, and traces.

    The search tools pass their query to Sentry's own search agent. Replays,
    logs, and profiles are left out because they often hold personal data.
    """

    FIND_PROJECTS = "find_projects"
    SEARCH_ISSUES = "search_issues"
    SEARCH_ERRORS = "search_errors"
    SEARCH_TRACES = "search_traces"
    GET_SENTRY_RESOURCE = "get_sentry_resource"


SENTRY_MCP_TOOLS = tuple(SentryMCPTool)


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


def settings_module(name: str) -> str:
    return f"secondshift.config.settings.{name}"


class SandboxRuntime(StrEnum):
    """The Docker runtime that runs command sandboxes."""

    DOCKER = "docker"
    GVISOR = "runsc"


class RunSandbox(StrEnum):
    """What a run records as its sandbox."""

    PENDING = "pending-runner-service"
    ISOLATED_RUNNER = "isolated-runner"


RUNNER_SETTINGS = settings_module(Service.RUNNER)

# Docker's official Python image, pinned by digest so a rebuilt tag can't change
# what runs. The agent's tools need only `find`, `cat` and `grep`. Git runs in
# the runner, not the sandbox. Operators override it with SECONDSHIFT_RUNNER_IMAGE.
# To update, run `docker buildx imagetools inspect python:3.13-slim` and copy
# the index digest.
DEFAULT_SANDBOX_IMAGE = (
    "python:3.13-slim"
    "@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39"
)

# The repository file that lists jobs, at the repository root.
MANIFEST_FILE = ".secondshift.yaml"

# How much of a skill file a job keeps for people to read in the dashboard.
MAX_SKILL_PREVIEW_CHARS = 200_000
