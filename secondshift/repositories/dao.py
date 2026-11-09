"""Queries and writes on repositories."""

from typing import Any

from django.db.models import Q, QuerySet

from secondshift.connections.models import Connection
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace


def get_repository_with_connection(repository_id: int) -> Repository:
    """The repository with its GitHub connection loaded."""
    return Repository.objects.select_related("github_connection").get(pk=repository_id)


def lock_repository(repository_id: int, with_workspace: bool = False) -> Repository:
    """Lock the repository for this transaction.

    Submissions, dispatch and cancellations take this lock to stay in order.
    """
    repositories = Repository.objects.select_for_update()
    if with_workspace:
        repositories = repositories.select_related("workspace")
    return repositories.get(pk=repository_id)


def active_repositories(workspace_id: int) -> QuerySet[Repository]:
    """The workspace's repositories that were not removed."""
    return Repository.objects.filter(workspace_id=workspace_id, removed_at__isnull=True)


def active_repositories_with_jobs(workspace_id: int) -> QuerySet[Repository]:
    return active_repositories(workspace_id).prefetch_related("jobs")


def active_repository_ids() -> set[int]:
    """Every repository that was not removed, in any workspace."""
    return set(
        Repository.objects.filter(removed_at__isnull=True).values_list("pk", flat=True)
    )


def connection_repositories(connection: Connection) -> list[Repository]:
    """The repositories added through this GitHub connection, by name."""
    return list(
        Repository.objects.filter(
            github_connection=connection, removed_at__isnull=True
        ).order_by("full_name")
    )


def has_repositories(connection: Connection) -> bool:
    """Whether any repository, removed or not, uses this connection."""
    return connection.repositories.exists()


def repositories_to_sync(include_local: bool) -> list[Repository]:
    """Repositories that sync on their own, with their workspace and connection.

    Disabled repositories count only until their first good sync.
    """
    sources = Q(source_type=Repository.SourceType.GITHUB)
    if include_local:
        sources |= Q(source_type=Repository.SourceType.LOCAL, local_path__isnull=False)
    return list(
        Repository.objects.filter(
            sources,
            Q(enabled=True) | Q(config_sha=""),
            auto_sync=True,
            removed_at__isnull=True,
        ).select_related("workspace", "github_connection")
    )


def set_sync_error(repository_id: int, error: str, checked_sha: str = "") -> None:
    """Record a failed sync. The last good sync stays in place."""
    fields: dict[str, str] = {"last_sync_error": error}
    if checked_sha:
        fields["checked_sha"] = checked_sha
    Repository.objects.filter(pk=repository_id).update(**fields)


def restore_repository_fields(repository_id: int, values: dict[str, Any]) -> None:
    """Put back fields saved before a change that failed."""
    Repository.objects.filter(pk=repository_id).update(**values)


def lock_or_create_github_repository(
    workspace: Workspace, full_name: str, defaults: dict[str, Any]
) -> tuple[Repository, bool]:
    """The workspace's repository with this name, locked, or a new one."""
    return Repository.objects.select_for_update().get_or_create(
        workspace=workspace, full_name=full_name, defaults=defaults
    )


def local_repository(workspace: Workspace, local_path: str) -> Repository | None:
    """The workspace's repository at this folder, if it was added before."""
    return Repository.objects.filter(workspace=workspace, local_path=local_path).first()


def save_local_repository(
    workspace: Workspace, local_path: str, fields: dict[str, Any]
) -> Repository:
    """Create or update the workspace's repository at this folder."""
    repository, _ = Repository.objects.update_or_create(
        workspace=workspace, local_path=local_path, defaults=fields
    )
    return repository


def create_repository(**fields: Any) -> Repository:
    return Repository.objects.create(**fields)
