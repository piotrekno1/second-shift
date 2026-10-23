"""Resolve GitHub scope from server-managed repository identity."""

import re
from urllib.parse import urlsplit

from secondshift.config.constants import (
    GITHUB_HOST,
    GITHUB_REPOSITORY_PATTERN,
    GITHUB_SSH_PREFIX,
)
from secondshift.repositories.models import Repository


def github_remote_name(remote: str) -> str | None:
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
    return name if re.fullmatch(GITHUB_REPOSITORY_PATTERN, name) else None


def github_repository_scope(repository: Repository) -> str | None:
    if repository.source_type == Repository.SourceType.LOCAL:
        return github_remote_name(repository.clone_url)
    name = repository.full_name
    return name if re.fullmatch(GITHUB_REPOSITORY_PATTERN, name) else None
