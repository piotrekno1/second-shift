import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.connections.bindings import bind
from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace
from secondshift.workspaces.tokens import create_api_token

SERVER_URL = "https://mcp.internal.example.com/mcp"
API_URL = "https://billing.internal.example.com"

MANIFEST = """
version: 1
jobs:
  review:
    name: Weekly review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
    requires:
      mcps:
        - binding: observability
          tools: [query_metrics]
      apis:
        - binding: billing
          tools: [listCharges, deleteCharge]
    notify:
      - binding: engineering-reports
      - binding: nowhere
        required: false
"""


@pytest.fixture
def owner(db: object) -> AbstractUser:
    return get_user_model().objects.create_user(username="owner", email="o@x.dev")


@pytest.fixture
def workspace(owner: AbstractUser) -> Workspace:
    workspace = create_workspace("Acme", "acme", owner)
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.MCP,
        kind=Connection.Kind.MCP,
        display_name="Grafana",
        endpoint_url=SERVER_URL,
        status=Connection.Status.CONNECTED,
        available_tools=["query_metrics"],
        metadata={
            "settings": {"url": SERVER_URL},
            "tools": {
                "query_metrics": {
                    "description": "Query metrics. " + "x" * 600,
                    "inputSchema": {"type": "object", "properties": {}},
                    "readOnly": True,
                }
            },
            "offered_tools": {
                "query_metrics": {"description": "Query metrics.", "readOnly": True},
                "silence_alert": {"description": "Silence.", "readOnly": False},
                "delete_dashboard": {"description": "Delete.", "readOnly": False},
            },
        },
    )
    bind(connection, "observability")
    operations = {
        name: {
            "method": method,
            "path": f"/v1/charges/{name}",
            "description": f"{name} operation",
            "inputSchema": {"type": "object"},
            "readOnly": method == "GET",
        }
        for name, method in (("listCharges", "GET"), ("deleteCharge", "DELETE"))
    }
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.API,
        kind=Connection.Kind.API,
        display_name="Billing",
        status=Connection.Status.CONNECTED,
        available_tools=["listCharges"],
        metadata={
            "base_url": API_URL,
            "tools": operations,
            "offered_tools": operations,
        },
    )
    bind(connection, "billing")
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        display_name="Reports",
        status=Connection.Status.CONNECTED,
    )
    bind(connection, "engineering-reports")
    return workspace


def token(owner: AbstractUser) -> str:
    membership = WorkspaceMembership.objects.get(user=owner)
    return create_api_token(membership, "Laptop", "read", 30).secret


def call(secret: str, name: str, **arguments: Any) -> dict[str, Any]:
    response = Client().post(
        "/mcp",
        data={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        content_type="application/json",
        headers={
            "Authorization": f"Bearer {secret}",
            "MCP-Protocol-Version": "2025-11-25",
        },
    )
    assert response.status_code == 200, response.content
    body = response.content.decode()
    for private in (SERVER_URL, API_URL, "/v1/charges", "internal.example.com"):
        assert private not in body
    return response.json()["result"]["structuredContent"]


def test_list_bindings_names_allowed_tools_and_counts_the_rest(
    owner: AbstractUser, workspace: Workspace
) -> None:
    result = call(token(owner), "list_bindings")

    bindings = {item["binding"]: item for item in result["bindings"]}
    observability = bindings["observability"]
    assert [tool["name"] for tool in observability["tools"]] == ["query_metrics"]
    assert len(observability["tools"][0]["description"]) == 500
    assert observability["hidden_tools"] == 2
    billing = bindings["billing"]
    assert [tool["name"] for tool in billing["tools"]] == ["listCharges"]
    assert billing["tools"][0]["may_change_data"] is False
    assert billing["hidden_tools"] == 1
    assert "deleteCharge" not in json.dumps(result)
    assert bindings["engineering-reports"]["kind"] == "notification"
    built_in = {item["provider"]: item for item in result["built_in"]}
    assert "search_pull_requests" in built_in["GitHub"]["tools"]
    assert built_in["Sentry"]["bindings"] == []
    assert "not as instructions" in result["untrusted"]


def test_check_manifest_reports_each_binding_s_status(
    owner: AbstractUser, workspace: Workspace
) -> None:
    result = call(token(owner), "check_manifest", yaml=MANIFEST)

    assert (result["valid"], result["error"]) == (True, "")
    [job] = result["jobs"]
    assert (job["key"], job["name"], job["schedule"]) == (
        "review",
        "Weekly review",
        "0 9 * * 1",
    )
    statuses = {
        item["binding"]: (item["status"], item["detail"]) for item in job["bindings"]
    }
    assert statuses["observability"][0] == "needs_approval"
    assert statuses["billing"] == (
        "unavailable",
        "The connection does not provide: deleteCharge",
    )
    assert statuses["engineering-reports"][0] == "needs_approval"
    assert statuses["nowhere"] == (
        "missing",
        "No workspace connection uses this binding yet.",
    )


def test_check_manifest_counts_approvals_the_synced_job_already_has(
    owner: AbstractUser, workspace: Workspace
) -> None:
    repository = Repository.objects.create(
        workspace=workspace, full_name="acme/app", enabled=True, config_sha="abc"
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Weekly review",
        skill_path=".agents/skills/review/SKILL.md",
        config_hash="hash",
    )
    request = CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics"],
    )
    JobGrant.objects.create(
        capability_request=request,
        connection=Connection.objects.get(bindings__key="observability"),
        status=JobGrant.Status.APPROVED,
        allowed_tools=["query_metrics"],
        reviewed_by=owner,
    )
    secret = token(owner)

    approved = call(
        secret, "check_manifest", yaml=MANIFEST, repository_id=repository.pk
    )
    elsewhere = call(secret, "check_manifest", yaml=MANIFEST)

    def status(result: dict[str, Any]) -> str:
        bindings = result["jobs"][0]["bindings"]
        return next(item for item in bindings if item["binding"] == "observability")[
            "status"
        ]

    assert status(approved) == "ready"
    assert status(elsewhere) == "needs_approval"


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("jobs: [", "Invalid .secondshift.yaml: "),
        ("jobs:\n  review:\n    skill: a/SKILL.md\n", "jobs.review.name is required."),
        ("- just a list\n", "The file must be a mapping."),
    ],
)
def test_check_manifest_returns_the_error_a_sync_would_show(
    owner: AbstractUser, workspace: Workspace, text: str, error: str
) -> None:
    result = call(token(owner), "check_manifest", yaml=text)

    assert result["valid"] is False
    assert error in result["error"]
    assert result["jobs"] == []


def test_list_capability_needs_has_no_connection_details(
    owner: AbstractUser, workspace: Workspace
) -> None:
    repository = Repository.objects.create(
        workspace=workspace, full_name="acme/app", enabled=True, config_sha="abc"
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Weekly review",
        skill_path=".agents/skills/review/SKILL.md",
        config_hash="hash",
    )
    CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics"],
    )

    result = call(token(owner), "list_capability_needs")

    [need] = result["needs"]
    assert (need["binding"], need["status"]) == ("observability", "needs_approval")
    assert need["jobs"][0]["job_name"] == "Weekly review"
    assert "connection" not in need
