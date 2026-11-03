"""Reach a repository on GitHub through the connection it was added with.

Each operation gets one token and checks the repository's identity once, then
uses that token for every call and git command it makes.
"""

from dataclasses import dataclass

from secondshift.connections.models import Connection
from secondshift.github import credentials, repos
from secondshift.github.errors import GitHubError
from secondshift.github.identity import clone_url
from secondshift.repositories.models import Repository


@dataclass(frozen=True)
class GitHubAccess:
    """A checked repository and a token for it. Never stored or logged."""

    repository_id: int
    full_name: str
    token: str
    metadata: dict

    @property
    def clone_url(self) -> str:
        return clone_url(self.full_name)


def usable_source(connection: Connection | None) -> bool:
    return bool(
        connection
        and connection.provider == Connection.Provider.GITHUB
        and connection.kind == Connection.Kind.SOURCE
        and connection.auth_strategy == Connection.AuthStrategy.MANAGED
        and connection.status == Connection.Status.CONNECTED
    )


def connection_source(connection: Connection | None) -> credentials.Source:
    if not usable_source(connection):
        raise GitHubError("The GitHub installation is disconnected.")
    return credentials.AppInstallation(connection.external_account_id)


def connection_access(
    connection: Connection | None,
    repository_id: int,
    permissions: dict[str, str] = credentials.READ,
) -> GitHubAccess:
    """Access to a repository the connection reaches, before it has a row."""
    token = credentials.token_for(
        connection_source(connection), repository_id, permissions
    )
    metadata = repos.repository(token, repository_id)
    return GitHubAccess(repository_id, metadata["full_name"], token, metadata)


def github_access(
    repository: Repository, permissions: dict[str, str] = credentials.READ
) -> GitHubAccess:
    """Access to an added repository. Fails if it was removed, moved or renamed."""
    if repository.removed_at is not None:
        raise GitHubError("This repository has been removed.")
    connection = repository.github_connection
    if connection is not None and connection.workspace_id != repository.workspace_id:
        raise GitHubError("The GitHub installation is disconnected.")
    access = connection_access(connection, int(repository.external_id), permissions)
    # Do not silently move a repository's identity or its existing approvals.
    if repository.full_name != access.full_name:
        raise GitHubError("Repository was renamed. Remove and import it again.")
    return access
