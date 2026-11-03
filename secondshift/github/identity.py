"""Repository names from GitHub remotes."""

import re
from urllib.parse import urlsplit

from secondshift.config.constants import (
    GITHUB_BASE_URL,
    GITHUB_HOST,
    GITHUB_REPOSITORY_PATTERN,
    GITHUB_SSH_PREFIX,
)


def github_remote_name(remote: str) -> str | None:
    """The owner/name a GitHub remote points at, or None for anything else."""
    if remote.startswith(GITHUB_SSH_PREFIX):
        path = remote.removeprefix(GITHUB_SSH_PREFIX)
    else:
        try:
            parts = urlsplit(remote)
            if parts.hostname != GITHUB_HOST or parts.scheme not in {"https", "ssh"}:
                return None
            path = parts.path.removeprefix("/")
        except ValueError:
            return None
    name = path.removesuffix("/").removesuffix(".git")
    return name if valid_name(name) else None


def valid_name(full_name: str) -> bool:
    return bool(re.fullmatch(GITHUB_REPOSITORY_PATTERN, full_name))


def clone_url(full_name: str) -> str:
    return f"{GITHUB_BASE_URL}/{full_name}.git"
