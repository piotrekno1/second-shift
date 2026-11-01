"""Grants approved without an owner, for connections that do not require approval.

When a connection's owner turns approval off, merging `.secondshift.yaml` is
the review. A job still gets only tools the owner allowed on the connection.
Anything else waits for an owner, as before. Owner decisions are kept: an
approved or denied grant is never changed here.
"""

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from secondshift.connections.external import external_access
from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, JobGrant
from secondshift.repositories.models import Repository


def approves_automatically(
    request: CapabilityRequest, connection: Connection, repository: Repository
) -> bool:
    if connection.require_approval:
        return False
    if connection.status != Connection.Status.CONNECTED:
        return False
    if connection.kind != request.kind or connection.binding_key != request.binding:
        return False
    if set(request.requested_tools) - set(connection.available_tools):
        return False
    access = external_access(connection, repository)
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


def approve_waiting(requests: QuerySet[CapabilityRequest]) -> int:
    """Approve the waiting requests that their connections allow. Returns how many."""
    requests = requests.filter(job__repository__removed_at__isnull=True).select_related(
        "grant", "job__repository"
    )
    loaded = list(requests)
    if not loaded:
        return 0
    connections = {
        (connection.workspace_id, connection.binding_key): connection
        for connection in Connection.objects.filter(
            workspace_id__in={request.job.workspace_id for request in loaded},
            binding_key__in={request.binding for request in loaded},
            require_approval=False,
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
    JobGrant.objects.bulk_create(created)
    JobGrant.objects.bulk_update(
        updated,
        [
            "connection",
            "status",
            "allowed_tools",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ],
    )
    return len(created) + len(updated)


def approve_waiting_for(connection: Connection) -> int:
    """Approve requests that were waiting for this connection."""
    if connection.require_approval or not connection.binding_key:
        return 0
    return approve_waiting(
        CapabilityRequest.objects.filter(
            job__workspace_id=connection.workspace_id,
            binding=connection.binding_key,
        )
    )


@transaction.atomic
def set_require_approval(connection: Connection, require: bool) -> Connection:
    """Change the setting. Existing grants stay."""
    if connection.require_approval != require:
        connection.require_approval = require
        connection.save(update_fields=["require_approval", "updated_at"])
    approve_waiting_for(connection)
    return connection
