from dataclasses import dataclass, field
from typing import Literal

from secondshift.automations.models import CapabilityRequest, JobGrant
from secondshift.core.models import Workspace
from secondshift.integrations.mcp.providers import mcp_scopes, missing_scope_message
from secondshift.integrations.models import Connection

NeedStatus = Literal["missing", "unavailable", "needs_approval", "ready"]
STATUS_ORDER: dict[str, int] = {
    "missing": 0,
    "unavailable": 1,
    "needs_approval": 2,
    "ready": 3,
}


@dataclass
class NeedJob:
    job_id: int
    job_name: str
    repository: str
    capability_id: int
    required: bool
    grant_status: str
    mcp_scopes: dict[str, str | None] = field(default_factory=dict)


@dataclass
class CapabilityNeed:
    binding: str
    kind: str
    required: bool
    requested_tools: list[str]
    status: NeedStatus
    detail: str
    connection: Connection | None
    jobs: list[NeedJob] = field(default_factory=list)


def need_status(
    kind: str,
    connection: Connection | None,
    requests: list[CapabilityRequest],
    requested_tools: set[str],
) -> tuple[NeedStatus, str]:
    if connection is None:
        return "missing", "No workspace connection uses this binding yet."
    if connection.kind != kind:
        return (
            "unavailable",
            f"The {connection.binding_key} connection is a "
            f"{connection.get_kind_display()} connection, not {kind}.",
        )
    if connection.status != Connection.Status.CONNECTED:
        return (
            "unavailable",
            f"The connection is {connection.get_status_display().lower()}.",
        )
    if kind == Connection.Kind.MCP:
        for request in requests:
            if message := missing_scope_message(
                connection.provider, request.job.repository
            ):
                return "unavailable", message
    missing_tools = requested_tools - set(connection.available_tools)
    if missing_tools:
        return (
            "unavailable",
            "The connection does not provide: " + ", ".join(sorted(missing_tools)),
        )
    unapproved = [
        request
        for request in requests
        if getattr(request, "grant", None) is None
        or request.grant.status == JobGrant.Status.PENDING
    ]
    if unapproved:
        return "needs_approval", "Connected. Approve it for the jobs that use it."
    return "ready", "Connected and reviewed for every job."


def capability_needs(workspace: Workspace) -> list[CapabilityNeed]:
    """Group every capability requested by active jobs by its logical binding."""
    requests = (
        CapabilityRequest.objects.filter(
            job__workspace=workspace,
            job__repository__removed_at__isnull=True,
        )
        .select_related("job__repository", "grant")
        .order_by("binding", "job__name")
    )
    connections = {
        connection.binding_key: connection
        for connection in Connection.objects.filter(workspace=workspace).exclude(
            binding_key=""
        )
    }
    grouped: dict[tuple[str, str], list[CapabilityRequest]] = {}
    for request in requests:
        grouped.setdefault((request.binding, request.kind), []).append(request)

    needs: list[CapabilityNeed] = []
    for (binding, kind), items in grouped.items():
        connection = connections.get(binding)
        requested_tools = {tool for item in items for tool in item.requested_tools}
        status, detail = need_status(kind, connection, items, requested_tools)
        needs.append(
            CapabilityNeed(
                binding=binding,
                kind=kind,
                required=any(item.required for item in items),
                requested_tools=sorted(requested_tools),
                status=status,
                detail=detail,
                connection=connection,
                jobs=[
                    NeedJob(
                        job_id=item.job_id,
                        job_name=item.job.name,
                        repository=item.job.repository.display_name,
                        mcp_scopes=(
                            mcp_scopes(item.job.repository)
                            if kind == Connection.Kind.MCP
                            else {}
                        ),
                        capability_id=item.pk,
                        required=item.required,
                        grant_status=(
                            item.grant.status
                            if getattr(item, "grant", None)
                            else "unconfigured"
                        ),
                    )
                    for item in items
                ],
            )
        )
    needs.sort(
        key=lambda need: (STATUS_ORDER[need.status], not need.required, need.binding)
    )
    return needs
