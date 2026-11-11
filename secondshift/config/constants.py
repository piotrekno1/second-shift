"""Names shared by the settings modules, entry points and the launcher.

The runner's settings must never import the control plane's settings, which
read its keys. Values both need live here instead.
"""

from enum import StrEnum
from urllib.parse import urlsplit

# Git object formats supported by repository snapshots and execution plans.
COMMIT_SHA_PATTERN = r"[0-9a-f]{40}|[0-9a-f]{64}"

EXECUTE_RUN_WORKFLOW = "secondshift.execute_run"
SCHEDULED_JOB_WORKFLOW = "secondshift.scheduled_job"
RETRY_NOTIFICATION_WORKFLOW = "secondshift.retry_notification"
RETRY_PULL_REQUEST_WORKFLOW = "secondshift.retry_pull_request"
RETRY_ISSUES_WORKFLOW = "secondshift.retry_issues"

ENVIRONMENT_VAR = "SECONDSHIFT_ENV"
SETTINGS_MODULE_VAR = "DJANGO_SETTINGS_MODULE"
SERVICE_VAR = "SECONDSHIFT_SERVICE"


GUNICORN_CONFIG = "python:secondshift.config.gunicorn"

# How agent containers reach services on the Docker host.
HOST_ALIAS = "host.docker.internal"
# `secondshift up` builds this tag from the installed package.
LOCAL_AGENT_TAG = "secondshift-agent:local"
LOCAL_GATEWAY_URL = "http://127.0.0.1:8001"


# The gateway's MCP endpoint for subscription agents. Deploy files spell it out too.
AGENT_BROKER_PATH = "gateway/agent/mcp"


def local_agent_broker_url(gateway_url: str) -> str:
    """The gateway's agent broker, as agent containers on this host reach it."""
    port = urlsplit(gateway_url).port or 80
    return f"http://{HOST_ALIAS}:{port}/{AGENT_BROKER_PATH}"


class ArtifactStorage(StrEnum):
    FILESYSTEM = "filesystem"
    S3 = "s3"


class AppEnv(StrEnum):
    AGENT_IMAGE = "SECONDSHIFT_AGENT_IMAGE"
    AGENT_RESOLV_CONF = "SECONDSHIFT_AGENT_RESOLV_CONF"
    AGENT_NETWORK = "SECONDSHIFT_AGENT_NETWORK"
    AGENT_BROKER_URL = "SECONDSHIFT_AGENT_BROKER_URL"
    DATA_DIR = "SECONDSHIFT_DATA_DIR"
    RUNNER_RUNTIME = "SECONDSHIFT_RUNNER_RUNTIME"
    RUNNER_IMAGE = "SECONDSHIFT_RUNNER_IMAGE"
    RUNNER_SERVICE_URL = "SECONDSHIFT_RUNNER_SERVICE_URL"
    RUNNER_SERVICE_TOKEN = "SECONDSHIFT_RUNNER_SERVICE_TOKEN"
    MODEL_GATEWAY_URL = "SECONDSHIFT_MODEL_GATEWAY_URL"
    RUNNER_WORK_ROOT = "SECONDSHIFT_RUNNER_WORK_ROOT"
    SOURCE_CACHE_ROOT = "SECONDSHIFT_SOURCE_CACHE_ROOT"
    SOURCE_CACHE_MAX_BYTES = "SECONDSHIFT_SOURCE_CACHE_MAX_BYTES"
    EXTERNAL_SKILLS_ENABLED = "SECONDSHIFT_EXTERNAL_SKILLS_ENABLED"
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
    DB_CONN_MAX_AGE_SECONDS = "SECONDSHIFT_DB_CONN_MAX_AGE_SECONDS"
    WORKFLOW_DATABASE_URL = "SECONDSHIFT_WORKFLOW_DATABASE_URL"
    RUN_CONCURRENCY = "SECONDSHIFT_RUN_CONCURRENCY"
    RUN_GLOBAL_CONCURRENCY = "SECONDSHIFT_RUN_GLOBAL_CONCURRENCY"
    TASK_QUEUE_LIMIT = "SECONDSHIFT_TASK_QUEUE_LIMIT"
    RUNNER_ALLOWED_HOSTS = "SECONDSHIFT_RUNNER_ALLOWED_HOSTS"
    RUNNER_DATABASE_URL = "SECONDSHIFT_RUNNER_DATABASE_URL"
    RUNNER_DB_CONN_MAX_AGE_SECONDS = "SECONDSHIFT_RUNNER_DB_CONN_MAX_AGE_SECONDS"
    EMAIL_URL = "SECONDSHIFT_EMAIL_URL"
    FROM_EMAIL = "SECONDSHIFT_FROM_EMAIL"

    HTTP_BIND = "SECONDSHIFT_HTTP_BIND"
    HTTP_PROCESSES = "SECONDSHIFT_HTTP_PROCESSES"
    HTTP_TIMEOUT_SECONDS = "SECONDSHIFT_HTTP_TIMEOUT_SECONDS"
    HTTP_GRACEFUL_TIMEOUT_SECONDS = "SECONDSHIFT_HTTP_GRACEFUL_TIMEOUT_SECONDS"
    HTTP_LOG_LEVEL = "SECONDSHIFT_HTTP_LOG_LEVEL"
    HTTP_ACCESS_LOG = "SECONDSHIFT_HTTP_ACCESS_LOG"


GITHUB_HOST = "github.com"
GITHUB_BASE_URL = f"https://{GITHUB_HOST}"
GITHUB_SSH_PREFIX = f"git@{GITHUB_HOST}:"
GITHUB_REPOSITORY_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
GITHUB_API_URL = "https://api.github.com"
GITHUB_OAUTH_URL = f"{GITHUB_BASE_URL}/login/oauth"
# The REST API version every request asks for.
GITHUB_API_VERSION = "2022-11-28"
# Fine-grained tokens are limited to chosen repositories. Classic tokens are not.
GITHUB_FINE_GRAINED_TOKEN_PATTERN = r"github_pat_[A-Za-z0-9_]+"
GITHUB_TOKEN_HINT = "Enter a fine-grained GitHub token. It starts with github_pat_."
# Limits for the files of one commit that a run gets. The control plane checks
# them while it builds a snapshot, and the runner checks them again.
MAX_SOURCE_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_SOURCE_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_FILES = 200_000
# Limits for what a run leaves behind: the agent's file changes, and the files
# it saves in /outputs. The runner applies them, and the control plane again.
MAX_CHANGES_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_FILES = 100
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
# Earlier runs' saved files that one run gets back. Uploaded like a snapshot,
# so it stays under that limit even when the files do not compress.
MAX_HISTORY_FILES_BYTES = 48 * 1024 * 1024
# Issues one run may open. A weekly review should file a few, not dozens.
MAX_RUN_ISSUES = 10
# Where a sandbox finds the folder for files it wants to hand back.
SANDBOX_OUTPUTS_DIR = "/outputs"
# Where a sandbox finds the files its job's earlier runs saved. Read-only.
SANDBOX_HISTORY_DIR = "/history"
# Where a sandbox finds a skill from another repository. Read-only.
SANDBOX_SKILL_DIR = "/skill"
# Limits for a skill from another repository: its SKILL.md and the files beside it.
MAX_SKILL_FILES = 500
MAX_SKILL_UNPACKED_BYTES = 20 * 1024 * 1024
MAX_SKILL_SNAPSHOT_BYTES = 10 * 1024 * 1024
GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/readonly"
# The model gateway refuses a run past its token budget with this message.
TOKEN_BUDGET_MESSAGE = "This run has used its token budget."


class TriggerKind(StrEnum):
    """Events a repository may ask to start a job. Never a URL or a secret."""

    WEBHOOK = "webhook"


class ModelFamily(StrEnum):
    """The API shape a model provider speaks. Names the gateway's URL path."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class RunStart(StrEnum):
    """What started a run."""

    SCHEDULE = "schedule"
    MANUAL = "manual"
    WEBHOOK = "webhook"
    MCP = "mcp"


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


class AgentBackend(StrEnum):
    """Who runs the agent: SecondShift's own loop, or a subscription CLI."""

    PYDANTIC = "pydantic"
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"

    @property
    def uses_model_api(self) -> bool:
        """Runs call a model provider through SecondShift's gateway. The CLIs
        call their provider directly with the subscription login."""
        return self is AgentBackend.PYDANTIC

    @property
    def starts_subagents(self) -> bool:
        """Runs can start sub-agents when the job allows them."""
        return self is not AgentBackend.CODEX

    @property
    def limits_model_calls(self) -> bool:
        """Runs stop at the run's model-call limit."""
        return self is not AgentBackend.CODEX
