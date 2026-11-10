"""Grants approved without an owner, for connections that do not require approval.

When a connection's owner turns approval off, merging `.secondshift.yaml` is
the review. A job still gets only tools the owner allowed on the connection.
Anything else waits for an owner, as before. Owner decisions are kept: an
approved or denied grant is never changed here.
"""

from django.db import transaction
from django.utils import timezone

from secondshift.connections.dao import auto_approved_connections
from secondshift.connections.external import external_access
from secondshift.connections.models import Connection
from secondshift.jobs.capabilities import RequestedCapability
from secondshift.jobs.dao import (
    binding_active_requests,
    create_grants,
    save_grant_reviews,
)
from secondshift.jobs.models import CapabilityRequest, JobGrant
from secondshift.repositories.github import repository_ref
from secondshift.repositories.models import Repository


def approves_automatically(
    request: CapabilityRequest | RequestedCapability,
    connection: Connection,
    repository: Repository,
) -> bool:
    if connection.workspace_id != repository.workspace_id:
        return False
    if connection.require_approval:
        return False
    if not connection.is_connected:
        return False
    if connection.kind != request.kind or connection.binding_key != request.binding:
        return False
    if set(request.requested_tools) - set(connection.available_tools):
        return False
    access = external_access(connection, repository_ref(repository))
    return access is None or access.ok


def automatic_approval(
    request: CapabilityRequest, connection: Connection, repository: Repository
) -> dict[str, object] | None:
    """The grant fields that approve the request, or None if it must wait."""
    if not approves_automatically(request, connection, repository):
        return None
    return {
        "connection_id": connection.pk,
        "status": JobGrant.Status.APPROVED,
        "allowed_tools": sorted(request.requested_tools),
        "reviewed_by_id": None,
        "reviewed_at": timezone.now(),
    }


def approve_waiting(loaded: list[CapabilityRequest]) -> int:
    """Approve the waiting requests that their connections allow. Returns how many.

    Each request needs its grant and its job's repository loaded.
    """
    if not loaded:
        return 0
    connections = {
        (connection.workspace_id, connection.binding_key): connection
        for connection in auto_approved_connections(
            {request.job.workspace_id for request in loaded},
            {request.binding for request in loaded},
        )
    }
    now = timezone.now()
    created: list[JobGrant] = []
    updated: list[JobGrant] = []
    for request in loaded:
        grant = getattr(request, "grant", None)
        if grant is not None and grant.status != JobGrant.Status.PENDING:
            continue
        connection = connections.get((request.job.workspace_id, request.binding))
        if connection is None:
            continue
        fields = automatic_approval(request, connection, request.job.repository)
        if fields is None:
            continue
        if grant is None:
            grant = JobGrant(capability_request=request)
            created.append(grant)
        else:
            updated.append(grant)
        for name, value in {**fields, "updated_at": now}.items():
            setattr(grant, name, value)
    create_grants(created)
    save_grant_reviews(updated)
    return len(created) + len(updated)


def approve_waiting_for(connection: Connection) -> int:
    """Approve requests that were waiting for this connection."""
    if connection.require_approval or not connection.binding_key:
        return 0
    return approve_waiting(
        binding_active_requests(connection.workspace_id, connection.binding_key)
    )


@transaction.atomic
def set_require_approval(connection: Connection, require: bool) -> Connection:
    """Change the setting. Existing grants stay."""
    if connection.require_approval != require:
        connection.require_approval = require
        connection.save(update_fields=["require_approval", "updated_at"])
    approve_waiting_for(connection)
    return connection
