import json
import socket
from typing import Any

import httpx
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from secondshift.connections.bindings import bind
from secondshift.connections.errors import ExternalToolError
from secondshift.connections.external import external_access
from secondshift.connections.http_api.client import APIRequest, build_request, send
from secondshift.connections.http_api.connections import (
    create_api_connection,
    recheck_api_connection,
    update_api_connection,
)
from secondshift.connections.http_api.spec import (
    APIError,
    load_spec,
    read_operation,
    read_spec,
)
from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.serialization import serialize_job
from secondshift.repositories.models import Repository
from secondshift.runs.execution.external_tools import external_tools
from secondshift.runs.execution.tool_broker import call_external_tool_step
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace
from secondshift.workspaces.services import create_workspace

SPEC_URL = "https://docs.example.com/openapi.json"
BASE = "https://api.example.com/v1"
TOKEN = "secret-api-token"

SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Billing"},
    "servers": [
        {
            "url": "https://api.example.com/{version}",
            "variables": {"version": {"default": "v1"}},
        },
        {"url": "/sandbox"},
        {"url": "ftp://files.example.com"},
    ],
    "components": {
        "parameters": {
            "Limit": {
                "name": "limit",
                "in": "query",
                "schema": {"type": "integer", "minimum": 1, "exclusiveMinimum": True},
            }
        },
        "schemas": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/components/schemas/Node"}},
            },
            "Charge": {
                "type": "object",
                "properties": {"amount": {"type": "integer"}},
                "required": ["amount"],
            },
        },
    },
    "paths": {
        "/charges": {
            "get": {
                "operationId": "listCharges",
                "summary": "List charges",
                "parameters": [
                    {"$ref": "#/components/parameters/Limit"},
                    {
                        "name": "status",
                        "in": "query",
                        "schema": {"type": "array", "items": {"type": "string"}},
                    },
                    {"name": "X-Debug", "in": "header", "schema": {"type": "string"}},
                ],
            },
            "post": {
                "operationId": "createCharge",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Charge"}
                        }
                    },
                },
            },
        },
        "/charges/{chargeId}": {
            "parameters": [
                {
                    "name": "chargeId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "get": {"summary": "Get a charge"},
            "delete": {"operationId": "deleteCharge"},
        },
        "/tree": {
            "get": {
                "operationId": "tree",
                "parameters": [
                    {
                        "name": "node",
                        "in": "query",
                        "schema": {"$ref": "#/components/schemas/Node"},
                    }
                ],
            }
        },
        # The template names a parameter that is not declared: skipped.
        "/broken/{id}": {"get": {"operationId": "broken"}},
    },
}


class FakeAPI:
    def __init__(self) -> None:
        self.spec: dict[str, Any] = json.loads(json.dumps(SPEC))
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == SPEC_URL:
            return httpx.Response(200, json=self.spec)
        self.requests.append(request)
        if request.url.path == "/v1/redirect":
            return httpx.Response(
                302, headers={"Location": "https://evil.example/steal"}
            )
        return httpx.Response(
            200,
            json={"echo": str(request.url), "leak": TOKEN},
            headers={"Content-Type": "application/json"},
        )


@pytest.fixture
def fake(monkeypatch) -> FakeAPI:
    # Every host resolves to a public address, so the base URL check passes.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    api = FakeAPI()
    for module in ("spec", "client"):
        monkeypatch.setattr(
            f"secondshift.connections.http_api.{module}.MCPTransport",
            lambda: httpx.MockTransport(api.handle),
        )
    return api


@pytest.fixture
def owner(db) -> Any:
    return get_user_model().objects.create_user(username="api-owner")


@pytest.fixture
def workspace(owner) -> Workspace:
    return create_workspace(name="API", slug="api", owner=owner)


def connect(workspace: Workspace, **overrides: str) -> Connection:
    values = {
        "binding_key": "billing",
        "display_name": "",
        "spec_url": SPEC_URL,
        "spec_text": "",
        "base_url": BASE,
        "auth": "bearer",
        "header": "",
        "token": TOKEN,
        **overrides,
    }
    return create_api_connection(workspace, **values)


def test_spec_reads_operations_parameters_and_servers(fake: FakeAPI) -> None:
    spec = load_spec(SPEC_URL)
    assert spec.title == "Billing"
    # Variables use defaults, relative URLs resolve against the spec, ftp is dropped.
    assert spec.servers == (
        "https://api.example.com/v1",
        "https://docs.example.com/sandbox",
    )
    assert set(spec.operations) == {
        "listCharges",
        "createCharge",
        "get_charges_chargeId",
        "deleteCharge",
        "tree",
    }
    listing = spec.operations["listCharges"]
    assert listing.read_only
    # Header parameters are never exposed to the model.
    assert [item.name for item in listing.parameters] == ["limit", "status"]
    assert listing.input_schema["additionalProperties"] is False
    assert "exclusiveMinimum" not in listing.input_schema["properties"]["limit"]
    create = spec.operations["createCharge"]
    assert create.has_body and not create.read_only
    assert create.input_schema["properties"]["body"]["required"] == ["amount"]
    assert create.input_schema["required"] == ["body"]
    # A cyclic schema ends in an empty schema instead of recursing forever.
    assert "$ref" not in json.dumps(spec.operations["tree"].input_schema)


@pytest.mark.parametrize(
    ("path", "parameters"),
    [
        # A path and query parameter share a name.
        (
            "/items/{id}",
            [
                {"in": "path", "name": "id", "required": True},
                {"in": "query", "name": "id"},
            ],
        ),
        # The path has a placeholder no parameter fills.
        ("/items/{id}", []),
    ],
)
def test_unclear_operations_are_left_out(
    path: str, parameters: list[dict[str, object]]
) -> None:
    raw = {"operationId": "getItem", "parameters": parameters}

    assert read_operation({}, path, "get", raw, []) is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"swagger": "2.0", "paths": {}}', "OpenAPI 3"),
        ("openapi: 3.0.0\na: &x [1]\nb: *x\n", "aliases"),
        ("not: [valid", "JSON or YAML"),
        ('{"openapi": "3.0.0", "paths": {}}', "no operations"),
    ],
)
def test_bad_specs_are_refused(text: str, message: str) -> None:
    with pytest.raises(APIError, match=message):
        load_spec(spec_text=text)


def test_yaml_spec_without_url_keeps_only_absolute_servers() -> None:
    spec = load_spec(
        spec_text="openapi: 3.0.3\nservers:\n  - url: /relative\n  - url: https://api.example.com\npaths:\n  /ping:\n    get:\n      operationId: ping\n"
    )
    assert spec.servers == ("https://api.example.com",)
    assert set(spec.operations) == {"ping"}


@pytest.mark.parametrize(
    "value", ["a/b", "..", ".", "https://evil.example", "a\\b", ""]
)
def test_path_values_cannot_change_the_path(value: str) -> None:
    operation = read_spec(SPEC).operations["get_charges_chargeId"].definition()
    with pytest.raises(APIError):
        build_request(BASE, operation, {"chargeId": value})


def test_requests_use_only_declared_values() -> None:
    operations = read_spec(SPEC).operations
    detail = build_request(
        BASE, operations["get_charges_chargeId"].definition(), {"chargeId": "ch 1?x"}
    )
    assert detail.url == f"{BASE}/charges/ch%201%3Fx"
    listing = build_request(
        BASE,
        operations["listCharges"].definition(),
        {"limit": 5, "status": ["paid", "open"]},
    )
    assert listing.method == "GET"
    assert listing.url == f"{BASE}/charges?limit=5&status=paid&status=open"
    assert listing.body is None
    with pytest.raises(APIError, match="does not declare"):
        build_request(BASE, operations["listCharges"].definition(), {"X-Debug": "1"})
    with pytest.raises(APIError, match="does not declare"):
        build_request(BASE, operations["listCharges"].definition(), {"body": {"a": 1}})
    created = build_request(
        BASE, operations["createCharge"].definition(), {"body": {"amount": 5}}
    )
    assert created.method == "POST" and created.body == {"amount": 5}


def test_connect_selects_get_operations_and_stores_no_secret_in_metadata(
    fake: FakeAPI, workspace: Workspace
) -> None:
    connection = connect(workspace)
    assert connection.kind == Connection.Kind.API
    assert connection.display_name == "Billing"
    assert connection.endpoint_url == BASE
    assert connection.metadata["target"] == "api.example.com"
    assert connection.available_tools == ["get_charges_chargeId", "listCharges", "tree"]
    assert set(connection.metadata["offered_tools"]) >= {"createCharge", "deleteCharge"}
    assert TOKEN not in json.dumps(connection.metadata)


@pytest.mark.parametrize("header", ["Host", "Cookie", "Content-Type", "bad header", ""])
def test_reserved_or_invalid_credential_headers_are_refused(
    fake: FakeAPI, workspace: Workspace, header: str
) -> None:
    with pytest.raises(APIError, match="header name"):
        connect(workspace, auth="header", header=header)


def test_base_url_is_checked(fake: FakeAPI, workspace: Workspace) -> None:
    for bad in (
        "ftp://api.example.com",
        "https://user:pass@api.example.com",
        "https://api.example.com/v1?x=1",
    ):
        with pytest.raises(APIError):
            connect(workspace, base_url=bad)


def test_operation_choice_and_recheck_keep_the_base_url(
    fake: FakeAPI, workspace: Workspace
) -> None:
    connection = connect(workspace)
    update_api_connection(connection, None, None, None, ["listCharges", "createCharge"])
    assert connection.available_tools == ["createCharge", "listCharges"]
    with pytest.raises(APIError, match="from the spec"):
        update_api_connection(connection, None, None, None, ["dropTables"])

    fake.spec["servers"] = [{"url": "https://moved.example.com"}]
    del fake.spec["paths"]["/charges"]["post"]
    message = recheck_api_connection(connection)
    connection.refresh_from_db()
    assert connection.endpoint_url == BASE
    assert connection.available_tools == ["listCharges"]
    assert "different server" in message and "api.example.com" in message


def test_api_endpoints(
    client: Client, fake: FakeAPI, workspace: Workspace, owner: Any
) -> None:
    client.force_login(owner)
    summary = client.post(
        "/api/connections/api/inspect",
        data=json.dumps({"spec_url": SPEC_URL}),
        content_type="application/json",
    )
    assert summary.json() == {
        "title": "Billing",
        "servers": ["https://api.example.com/v1", "https://docs.example.com/sandbox"],
        "operations": 5,
        "read_only": 3,
    }
    created = client.post(
        "/api/connections/api",
        data=json.dumps(
            {
                "binding_key": "billing",
                "spec_url": SPEC_URL,
                "base_url": BASE,
                "auth": "header",
                "header": "X-API-Key",
                "token": TOKEN,
            }
        ),
        content_type="application/json",
    )
    assert created.status_code == 200, created.content
    body = created.json()
    assert TOKEN not in created.content.decode()
    assert body["settings"] == {
        "auth": "header",
        "header": "X-API-Key",
        "base_url": BASE,
        "spec_url": SPEC_URL,
    }
    assert {item["name"] for item in body["offered_tools"]} >= {"createCharge"}


@pytest.fixture
def api_run(fake: FakeAPI, workspace: Workspace) -> Run:
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", config_sha="a" * 40
    )
    Connection.objects.create(
        workspace=workspace,
        provider="openai",
        kind="model",
        display_name="Model",
        default_model="test",
        is_default=True,
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="billing",
        name="Billing report",
        skill_path=".agents/skills/billing/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )
    connection = connect(workspace, auth="header", header="X-API-Key")
    request = CapabilityRequest.objects.create(
        job=job, kind="api", binding="billing", requested_tools=["listCharges"]
    )
    JobGrant.objects.create(
        capability_request=request,
        connection=connection,
        status="approved",
        allowed_tools=["listCharges"],
    )
    return Run.objects.create(
        job=job,
        status="running",
        scheduled_for=timezone.now(),
        commit_sha="a" * 40,
        input_snapshot={"skill_path": job.skill_path},
        execution_plan=build_execution_plan(job),
    )


def test_job_calls_an_approved_operation(fake: FakeAPI, api_run: Run) -> None:
    capability = api_run.execution_plan["capabilities"][0]
    assert capability["authorized"]
    assert capability["external"]["base_url"] == BASE
    assert capability["external"]["scope"] == "api.example.com"
    access = external_access(
        Connection.objects.get(bindings__key="billing"), api_run.job.repository
    )
    assert access and access.text == (
        "Allow these operations to use anything the credential can reach on api.example.com."
    )
    [tool] = external_tools(api_run)
    assert tool.name.startswith("api_listCharges_")

    result = call_external_tool_step(
        api_run.pk, "api.billing.listCharges", {"limit": 2}, "c1"
    )

    request = fake.requests[-1]
    assert str(request.url) == f"{BASE}/charges?limit=2"
    assert request.headers["X-API-Key"] == TOKEN
    assert "Authorization" not in request.headers
    assert result["status"] == 200
    assert TOKEN not in json.dumps(result)
    event = api_run.events.get()
    assert event.message == "Tool completed: api.billing.listCharges"
    assert TOKEN not in json.dumps(event.metadata)


def test_unapproved_operations_and_changed_base_urls_stop_the_call(
    fake: FakeAPI, api_run: Run
) -> None:
    with pytest.raises(Exception, match="not authorized"):
        call_external_tool_step(
            api_run.pk, "api.billing.deleteCharge", {"chargeId": "1"}, "c2"
        )
    with pytest.raises(ExternalToolError, match="schema"):
        call_external_tool_step(
            api_run.pk, "api.billing.listCharges", {"limit": "two"}, "c3"
        )
    connection = Connection.objects.get(bindings__key="billing")
    update_api_connection(connection, None, None, "https://other.example.com", None)
    with pytest.raises(ExternalToolError, match="base URL changed"):
        call_external_tool_step(
            api_run.pk, "api.billing.listCharges", {"limit": 2}, "c4"
        )
    assert all(request.url.host == "api.example.com" for request in fake.requests)


def test_redirects_are_not_followed(fake: FakeAPI, workspace: Workspace) -> None:
    connection = connect(workspace)
    result = async_to_sync(send)(connection, APIRequest("GET", f"{BASE}/redirect"))
    assert result["status"] == 302
    assert "evil.example" not in json.dumps(result)
    assert all(request.url.host != "evil.example" for request in fake.requests)


def test_access_review_flags_operations_that_change_data(
    fake: FakeAPI, workspace: Workspace
) -> None:
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project"
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="cleanup",
        name="Cleanup",
        skill_path=".agents/skills/cleanup/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )
    connect(workspace)
    CapabilityRequest.objects.create(
        job=job,
        kind="api",
        binding="billing",
        requested_tools=["listCharges", "deleteCharge"],
    )
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        display_name="GitHub",
        available_tools=["get_commit"],
        metadata={"tools": {"get_commit": {"readOnly": False}}},
    )
    bind(connection, "github")
    CapabilityRequest.objects.create(
        job=job, kind="mcp", binding="github", requested_tools=["get_commit"]
    )

    capabilities = {item.binding: item for item in serialize_job(job).capabilities}

    assert capabilities["billing"].changing_tools == ["deleteCharge"]
    # Built-in providers only offer allowlisted read tools.
    assert capabilities["github"].changing_tools == []
