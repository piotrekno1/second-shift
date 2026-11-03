"""Resolve GitHub scope from server-managed repository identity."""

from secondshift.github.identity import github_remote_name, valid_name
from secondshift.repositories.models import Repository


def github_repository_scope(repository: Repository) -> str | None:
    if repository.source_type == Repository.SourceType.LOCAL:
        return github_remote_name(repository.clone_url)
    name = repository.full_name
    return name if valid_name(name) else None
