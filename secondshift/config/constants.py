"""Names shared by the settings modules, entry points and the launcher.

The runner's settings must never import the control plane's settings, which
read its keys. Values both need live here instead.
"""

from enum import StrEnum

ENVIRONMENT_VAR = "DJANGO_ENV"
SETTINGS_MODULE_VAR = "DJANGO_SETTINGS_MODULE"
SERVICE_VAR = "SECONDSHIFT_SERVICE"
GITHUB_HOST = "github.com"
GITHUB_BASE_URL = f"https://{GITHUB_HOST}"
GITHUB_SSH_PREFIX = f"git@{GITHUB_HOST}:"
GITHUB_REPOSITORY_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/readonly"


class GitHubMCPTool(StrEnum):
    SEARCH_PULL_REQUESTS = "search_pull_requests"
    PULL_REQUEST_READ = "pull_request_read"
    LIST_COMMITS = "list_commits"
    GET_COMMIT = "get_commit"


GITHUB_MCP_TOOLS = tuple(GitHubMCPTool)


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
