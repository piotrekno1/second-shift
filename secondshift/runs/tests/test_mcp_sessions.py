import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.db import connection as db_connection

from secondshift.connections.external import (
    SESSION_RESTARTED_NOTE,
    MCPKind,
    MCPSessionStore,
)
from secondshift.connections.mcp.base import MCPSessionRef
from secondshift.connections.mcp.custom import CUSTOM_MCP
from secondshift.connections.mcp.github import GITHUB_MCP
from secondshift.connections.mcp.sentry import SENTRY_MCP
from secondshift.connections.models import Connection
from secondshift.jobs.planner import build_execution_plan
from secondshift.runs.execution.mcp_sessions import (
    RunMCPSessions,
    close_run_mcp_sessions,
)
from secondshift.runs.execution.tool_broker import call_external_tool_step
from secondshift.runs.models import Run, RunEvent, RunMCPSession
from secondshift.runs.tests.test_mcp_broker import (  # noqa: F401
    NAME,
    mcp_http,
    planned_run,
)

ENDPOINT = "https://browser.example.com/mcp"
TOKEN = "custom-browser-token"


class StatefulServer:
    """An MCP server that counts calls per session, like a browser keeps a page."""

    def __init__(self, keeps_sessions: bool = True) -> None:
        self.keeps_sessions = keeps_sessions
        self.calls: dict[str, int] = {}
        self.initialized = 0
        self.deleted: list[str] = []

    def expire(self) -> None:
        self.calls.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        session = request.headers.get("mcp-session-id", "")
        if request.method == "DELETE":
            self.deleted.append(session)
            self.calls.pop(session, None)
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(405)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        headers = {}
        match message["method"]:
            case "initialize":
                self.initialized += 1
                result: dict[str, Any] = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "browser-fixture", "version": "1"},
                }
                if self.keeps_sessions:
                    session = f"secret-session-{self.initialized}"
                    self.calls[session] = 0
                    headers["mcp-session-id"] = session
            case "tools/list":
                result = {"tools": []}
            case "tools/call":
                if self.keeps_sessions and session not in self.calls:
                    return httpx.Response(404)
                if self.keeps_sessions:
                    self.calls[session] += 1
                    count = self.calls[session]
                else:
                    count = 1
                result = {"content": [{"type": "text", "text": f"call {count}"}]}
            case _:
                raise AssertionError(message)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": message["id"], "result": result},
            headers=headers,
        )


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[StatefulServer]:
    fake = StatefulServer()
    monkeypatch.setattr(
        "secondshift.connections.outbound.BoundedTransport",
        lambda: httpx.MockTransport(fake.handle),
    )
    yield fake


@pytest.fixture
def custom_run(planned_run: Run) -> Run:  # noqa: F811
    """The planned run with its GitHub binding pointed at a custom server."""
    connection = Connection.objects.get(binding_key="github")
    connection.provider = Connection.Provider.MCP
    connection.endpoint_url = ENDPOINT
    connection.access_token = TOKEN
    connection.metadata["settings"] = {"url": ENDPOINT}
    connection.save()
    planned_run.execution_plan = build_execution_plan(planned_run.job).stored()
    planned_run.save()
    return planned_run


def call(run: Run, call_id: str) -> str:
    result = call_external_tool_step(run.pk, NAME, {"query": "x"}, call_id)
    return json.dumps(result)


@pytest.mark.django_db
def test_calls_of_one_run_share_one_session(
    custom_run: Run, server: StatefulServer
) -> None:
    assert "call 1" in call(custom_run, "c1")
    assert "call 2" in call(custom_run, "c2")
    assert "call 3" in call(custom_run, "c3")
    assert server.initialized == 1
    assert server.deleted == []

    close_run_mcp_sessions(custom_run.pk)

    assert server.deleted == ["secret-session-1"]
    assert not RunMCPSession.objects.filter(run=custom_run).exists()


@pytest.mark.django_db
def test_a_session_the_server_forgot_is_replaced_once(
    custom_run: Run, server: StatefulServer
) -> None:
    call(custom_run, "c1")
    server.expire()

    result = call_external_tool_step(custom_run.pk, NAME, {"query": "x"}, "c2")

    texts = [item["text"] for item in result["content"]]
    assert texts == [SESSION_RESTARTED_NOTE, "call 1"]
    assert server.initialized == 2
    event = RunEvent.objects.get(run=custom_run, level="warning")
    assert "expired" in event.message
    assert "call 2" in call(custom_run, "c3")


@pytest.mark.django_db
def test_the_session_id_stays_out_of_events_results_and_plain_storage(
    custom_run: Run, server: StatefulServer
) -> None:
    results = call(custom_run, "c1")
    server.expire()
    results += call(custom_run, "c2")

    events = json.dumps(
        list(RunEvent.objects.filter(run=custom_run).values("message", "metadata"))
    )
    assert "secret-session" not in events
    assert "secret-session" not in results
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT session_id FROM runs_runmcpsession")
        stored = cursor.fetchone()[0]
    assert "secret-session" not in stored


@pytest.mark.django_db
def test_a_server_without_sessions_gets_a_fresh_one_each_call(
    custom_run: Run, server: StatefulServer
) -> None:
    server.keeps_sessions = False

    call(custom_run, "c1")
    call(custom_run, "c2")

    # One initialize to learn there is no session, then one per call.
    assert server.initialized == 3
    close_run_mcp_sessions(custom_run.pk)
    assert server.deleted == []


@pytest.mark.django_db
def test_built_in_providers_keep_no_session(planned_run: Run, mcp_http) -> None:  # noqa: F811
    call_external_tool_step(planned_run.pk, NAME, {"query": "is:merged"}, "c1")

    assert not RunMCPSession.objects.exists()
    assert not GITHUB_MCP.keeps_session
    assert not SENTRY_MCP.keeps_session
    assert CUSTOM_MCP.keeps_session


@pytest.mark.django_db
def test_the_first_saved_session_wins(custom_run: Run) -> None:
    connection = Connection.objects.get(binding_key="github")
    sessions = RunMCPSessions(custom_run.pk, connection.pk, "github")
    first = MCPSessionRef("first", "2025-06-18")

    assert sessions.keep(first) == first
    assert sessions.keep(MCPSessionRef("second", "2025-06-18")) == first


class SavedFirst:
    """A store where another call of the run saved a session first."""

    def __init__(self, saved: MCPSessionRef) -> None:
        self.saved = saved

    def load(self) -> MCPSessionRef | None:
        return None

    def keep(self, session: MCPSessionRef) -> MCPSessionRef:
        return self.saved

    def forget(self, session: MCPSessionRef) -> None:
        raise AssertionError(session)


def test_a_session_that_lost_the_race_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = MCPSessionRef("saved", "2025-06-18")
    opened = MCPSessionRef("opened", "2025-06-18")
    closed: list[MCPSessionRef] = []

    async def open_session(connection: Connection, token: str) -> MCPSessionRef:
        return opened

    async def close_session(
        connection: Connection, session: MCPSessionRef, token: str
    ) -> None:
        closed.append(session)

    monkeypatch.setattr(
        "secondshift.connections.external.open_mcp_session", open_session
    )
    monkeypatch.setattr(
        "secondshift.connections.external.close_mcp_session", close_session
    )
    store: MCPSessionStore = SavedFirst(saved)

    assert MCPKind().start_session(Connection(), store, "") == saved
    assert closed == [opened]
