"""Resolve GitHub scope from server-managed repository identity."""

from secondshift.connections.repository_ref import RepositoryRef
from secondshift.github.identity import github_remote_name, valid_name
from secondshift.repositories.models import Repository


def github_repository_scope(repository: Repository) -> str | None:
    if repository.source_type == Repository.SourceType.LOCAL:
        return github_remote_name(repository.clone_url)
    name = repository.full_name
    return name if valid_name(name) else None


def repository_ref(repository: Repository) -> RepositoryRef:
    """What external tools may know about the repository a job works on."""
    from_github = repository.source_type == Repository.SourceType.GITHUB
    return RepositoryRef(
        workspace_id=repository.workspace_id,
        github_name=github_repository_scope(repository),
        github_id=(
            int(repository.external_id)
            if from_github and repository.external_id
            else None
        ),
        source=repository.github_connection if from_github else None,
    )
