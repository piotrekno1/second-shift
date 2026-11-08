"""Named queries on connections.

Other apps read connections through these helpers, not Connection.objects.
"""

from collections.abc import Collection

from django.db.models import QuerySet

from secondshift.connections.models import Connection


def bound_connections(
    workspace_id: int, bindings: Collection[str] | None = None
) -> QuerySet[Connection]:
    """The workspace's connections that have a binding key.

    Pass `bindings` to keep only connections with those keys.
    """
    connections = Connection.objects.filter(workspace_id=workspace_id).exclude(
        binding_key=""
    )
    if bindings is not None:
        connections = connections.filter(binding_key__in=bindings)
    return connections


def connections_by_binding(
    workspace_id: int, bindings: Collection[str] | None = None
) -> dict[str, Connection]:
    """The workspace's bound connections, keyed by binding key."""
    return {
        connection.binding_key: connection
        for connection in bound_connections(workspace_id, bindings)
    }


def connection_for_binding(workspace_id: int, binding: str) -> Connection | None:
    """The workspace's connection with this binding key, if there is one."""
    return bound_connections(workspace_id, [binding]).first()


def auto_approved_connections(
    workspace_ids: Collection[int], bindings: Collection[str]
) -> QuerySet[Connection]:
    """Connections with these keys that grant access without an owner's review."""
    return Connection.objects.filter(
        workspace_id__in=workspace_ids,
        binding_key__in=bindings,
        require_approval=False,
    ).exclude(binding_key="")
