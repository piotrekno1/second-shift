import pytest

from secondshift.connections.models import Connection
from secondshift.jobs.grants import GrantError, decide_grant
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.tests.test_planning import create_job
from secondshift.workspaces.services import workspace_owner

APPROVED = JobGrant.Status.APPROVED
DENIED = JobGrant.Status.DENIED


@pytest.fixture
def job(db: object) -> Job:
    return create_job(db)


@pytest.fixture
def capability(job: Job) -> CapabilityRequest:
    return CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics", "list_alerts"],
    )


def mcp(job: Job, **overrides: object) -> Connection:
    values: dict[str, object] = {
        "workspace": job.workspace,
        "provider": Connection.Provider.MCP,
        "kind": Connection.Kind.MCP,
        "binding_key": "observability",
        "display_name": "Grafana",
        "status": Connection.Status.CONNECTED,
        "available_tools": ["query_metrics", "list_alerts"],
        "metadata": {"settings": {"url": "https://mcp.example.com/mcp"}},
    }
    return Connection.objects.create(**(values | overrides))


def decide(
    capability: CapabilityRequest,
    decision: str,
    connection: Connection | None,
    tools: list[str] | None = None,
) -> JobGrant:
    owner = workspace_owner(capability.job.workspace)
    return decide_grant(capability, decision, connection, tools, owner)


def test_approving_grants_every_requested_tool(
    job: Job, capability: CapabilityRequest
) -> None:
    grant = decide(capability, APPROVED, mcp(job))

    assert grant.status == APPROVED
    assert grant.allowed_tools == ["list_alerts", "query_metrics"]


def test_denying_grants_nothing(job: Job, capability: CapabilityRequest) -> None:
    grant = decide(capability, DENIED, None)

    assert (grant.status, grant.allowed_tools) == (DENIED, [])


@pytest.mark.parametrize(
    ("connection", "tools", "status", "message"),
    [
        (None, None, 422, "Choose a connection before approving"),
        ({"binding_key": "other"}, None, 422, "kind and binding must match"),
        ({"status": Connection.Status.REVOKED}, None, 409, "is not connected"),
        ({}, ["query_metrics", "drop_tables"], 422, "must be requested by the job"),
        ({}, ["query_metrics"], 422, "Approve every requested tool"),
        ({"available_tools": ["query_metrics"]}, None, 409, "lacks a requested tool"),
        ({"metadata": {}}, None, 409, "has no server URL"),
    ],
)
def test_approval_is_refused_when_it_does_not_fit(
    job: Job,
    capability: CapabilityRequest,
    connection: dict[str, object] | None,
    tools: list[str] | None,
    status: int,
    message: str,
) -> None:
    chosen = None if connection is None else mcp(job, **connection)

    with pytest.raises(GrantError, match=message) as refused:
        decide(capability, APPROVED, chosen, tools)

    assert refused.value.status == status
    assert not JobGrant.objects.exists()
