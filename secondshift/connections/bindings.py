"""Binding names that jobs ask for, and the connections they point to.

A job names a binding in .secondshift.yaml. Each name points to one connection
in a workspace. One connection can serve several names, so rotating a key or
changing a URL happens in one place.
"""

from django.db import IntegrityError, transaction

from secondshift.connections.errors import ConnectionConflict
from secondshift.connections.models import Connection, ConnectionBinding

# Kinds a job can ask for by binding. Identities, sources, and model providers
# are never named in a manifest.
BINDING_KINDS = (Connection.Kind.MCP, Connection.Kind.API, Connection.Kind.NOTIFICATION)
BINDING_TAKEN = "This binding already has a connection."


def binding_connections(workspace_id: int) -> dict[str, Connection]:
    """Every binding name in the workspace, mapped to its connection."""
    return {
        binding.key: binding.connection
        for binding in ConnectionBinding.objects.filter(
            workspace_id=workspace_id
        ).select_related("connection")
    }


def binding_connection(workspace_id: int, key: str) -> Connection | None:
    binding = (
        ConnectionBinding.objects.filter(workspace_id=workspace_id, key=key)
        .select_related("connection")
        .first()
    )
    return binding.connection if binding else None


def ensure_binding_free(workspace_id: int, key: str) -> None:
    """Fail early, before a create does slow work such as a sign-in."""
    if ConnectionBinding.objects.filter(workspace_id=workspace_id, key=key).exists():
        raise ConnectionConflict(BINDING_TAKEN)


def bind(connection: Connection, key: str) -> ConnectionBinding:
    """Point a binding at this connection. Nothing is approved by this.

    Each job that uses the binding still needs its own approval.
    """
    if connection.kind not in BINDING_KINDS:
        raise ConnectionConflict("This connection cannot serve a binding.")
    existing = ConnectionBinding.objects.filter(
        workspace_id=connection.workspace_id, key=key
    ).first()
    if existing:
        if existing.connection_id == connection.pk:
            return existing
        raise ConnectionConflict(BINDING_TAKEN)
    binding = ConnectionBinding(
        workspace_id=connection.workspace_id, connection=connection, key=key
    )
    binding.full_clean(validate_constraints=False)
    try:
        with transaction.atomic():
            binding.save()
    except IntegrityError:
        raise ConnectionConflict(BINDING_TAKEN) from None
    return binding


@transaction.atomic
def unbind(connection: Connection, key: str) -> None:
    """Stop this connection serving a binding. The connection itself stays.

    Grants for the binding go back to pending, so the jobs ask again.
    """
    deleted, _ = ConnectionBinding.objects.filter(
        connection=connection, key=key
    ).delete()
    if not deleted:
        raise ConnectionConflict("This connection does not serve that binding.")
    connection.job_grants.filter(capability_request__binding=key).update(
        connection=None,
        status="pending",
        allowed_tools=[],
        reviewed_by=None,
        reviewed_at=None,
    )
