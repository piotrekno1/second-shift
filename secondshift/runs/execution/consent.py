"""Whether a run may still use a connection its plan names.

A frozen plan never outlives consent. A job's runs need the owner's grant.
Ad hoc runs need a connection that grants access without review.
"""

from secondshift.connections.dao import auto_approved_connection
from secondshift.connections.models import Connection
from secondshift.jobs.dao import approved_tool_grant
from secondshift.jobs.plan import CapabilityPlan
from secondshift.runs.models import Run


def approved_connection(
    run: Run, capability: CapabilityPlan, tool: str | None = None
) -> Connection | None:
    """The connection, while consent still covers the capability and the tool."""
    if (
        run.status in Run.FINISHED_STATUSES
        and capability.kind != Connection.Kind.NOTIFICATION
    ):
        return None
    if not capability.authorized:
        return None
    if run.job is None:
        connection = auto_approved_connection(
            run.workspace_id,
            capability.connection_id,
            capability.kind,
            capability.binding,
        )
        if (
            connection is None
            or not connection.is_connected
            or set(capability.effective_tools) - set(connection.available_tools)
        ):
            return None
        return connection
    grant = approved_tool_grant(
        run.job, capability.kind, capability.binding, capability.connection_id
    )
    connection = grant.connection if grant else None
    if (
        grant is None
        or connection is None
        or connection.kind != capability.kind
        or connection.workspace_id != run.workspace_id
        or connection.binding_key != capability.binding
        or not connection.is_connected
    ):
        return None
    if tool is not None and not (
        tool in grant.allowed_tools
        and tool in grant.capability_request.requested_tools
        and tool in connection.available_tools
    ):
        return None
    return connection
