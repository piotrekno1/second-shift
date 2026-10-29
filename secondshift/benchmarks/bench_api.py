"""Dashboard and MCP endpoints over a workspace with 5,000 runs.

Each test first checks a query budget, then times the full request through
Django, Ninja and serialization.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client
from pytest_benchmark.fixture import BenchmarkFixture

from secondshift.benchmarks.seed import (
    make_workspace,
    owner_of,
    query_budget,
    seed_runs,
)
from secondshift.connections.models import Connection
from secondshift.gateway.tokens import authenticate, issue_run_token
from secondshift.jobs.models import Job
from secondshift.mcp_server.api import throttle
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.tokens import create_api_token

pytestmark = pytest.mark.django_db

# Budgets are fixed counts. Growing data must not add queries.
RUNS_PAGE_QUERIES = 8
OVERVIEW_QUERIES = 19
MCP_LIST_RUNS_QUERIES = 13
GATEWAY_AUTH_QUERIES = 1


@pytest.fixture
def workspace() -> Workspace:
    workspace = make_workspace()
    seed_runs(workspace)
    return workspace


@pytest.fixture
def client(workspace: Workspace) -> Client:
    client = Client()
    client.force_login(owner_of(workspace))
    return client


def get_ok(client: Client, path: str, params: dict[str, Any] | None = None) -> Any:
    response = client.get(path, params or {})
    assert response.status_code == 200, response.content
    return response.json()


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({}, id="newest"),
        pytest.param({"page_size": 100}, id="page-of-100"),
        pytest.param({"page": 40}, id="deep-page"),
        pytest.param({"sort": "cost"}, id="by-cost"),
        pytest.param({"q": "pull requests"}, id="search"),
        pytest.param({"status": ["failed"], "period": "7d"}, id="filtered"),
    ],
)
def test_runs_page(
    benchmark: BenchmarkFixture, client: Client, params: dict[str, Any]
) -> None:
    with query_budget(RUNS_PAGE_QUERIES):
        body = get_ok(client, "/api/runs", params)
    assert body["items"]

    benchmark(get_ok, client, "/api/runs", params)


def test_overview(benchmark: BenchmarkFixture, client: Client) -> None:
    with query_budget(OVERVIEW_QUERIES):
        body = get_ok(client, "/api/overview")
    assert body["runs_7d"]

    benchmark(get_ok, client, "/api/overview")


@pytest.fixture
def mcp_secret(workspace: Workspace) -> Iterator[str]:
    membership = WorkspaceMembership.objects.get(workspace=workspace)
    # Timed loops send thousands of calls. Keep the limiter in the path,
    # just above what the loop needs.
    previous = throttle.num_requests
    throttle.num_requests = 1_000_000
    yield create_api_token(membership, "Bench", "read", 30).secret
    throttle.num_requests = previous


def mcp_call(secret: str, name: str, arguments: dict[str, Any]) -> Any:
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
            "MCP-Protocol-Version": "2025-11-25",
            "Authorization": f"Bearer {secret}",
        },
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert not result.get("isError"), result
    return result


def test_mcp_list_runs(benchmark: BenchmarkFixture, mcp_secret: str) -> None:
    """Token auth, rate limit, tool dispatch and the audit record."""
    with query_budget(MCP_LIST_RUNS_QUERIES):
        mcp_call(mcp_secret, "list_runs", {"page_size": 50})

    benchmark(mcp_call, mcp_secret, "list_runs", {"page_size": 50})


def test_gateway_token_lookup(
    benchmark: BenchmarkFixture, workspace: Workspace
) -> None:
    """Every model request a run makes authenticates its token first."""
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        is_default=True,
        access_token="sk-ant-bench",
    )
    run = Run.objects.filter(job__in=Job.objects.filter(workspace=workspace)).first()
    assert run is not None
    Run.objects.filter(pk=run.pk).update(status=Run.Status.RUNNING)
    run.refresh_from_db()
    token = issue_run_token(run, connection)

    with query_budget(GATEWAY_AUTH_QUERIES):
        assert authenticate(token) is not None

    benchmark(authenticate, token)
