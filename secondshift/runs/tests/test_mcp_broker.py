import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import storages
from django.utils import timezone
from pydantic_ai import ToolFailed
from pydantic_ai.models.test import TestModel

from secondshift.config.constants import GITHUB_MCP_URL
from secondshift.connections.errors import ExternalToolError
from secondshift.connections.mcp.base import MCPError, MCPRequestError
from secondshift.connections.models import Connection
from secondshift.connections.tests.test_github_mcp import TOOL_SCHEMAS
from secondshift.connections.tests.test_github_mcp import mcp_http as imported_mcp_http
from secondshift.connections.webhooks import deliver_webhook
from secondshift.jobs.manifest import load_manifest
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.tool_policy import ToolPolicyError
from secondshift.repositories.models import Repository
from secondshift.runs.execution.external_tools import external_tools
from secondshift.runs.execution.run import WORKSPACE_AGENT, execute_agent_run
from secondshift.runs.execution.tool_broker import call_external_tool_step
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps
from secondshift.runs.models import Run
from secondshift.runs.reporting.notifications import (
    notification_targets,
    run_report_payload,
)
from secondshift.runs.tests.test_execution import tool_context
from secondshift.workspaces.services import create_workspace, workspace_owner

LATER = datetime(2100, 1, 1, tzinfo=UTC)


@pytest.fixture
def mcp_http(monkeypatch) -> list[dict[str, object]]:
    return imported_mcp_http.__wrapped__(monkeypatch)


NAME = "mcp.github.search_pull_requests"


@pytest.fixture
def planned_run() -> Run:
    owner = get_user_model().objects.create_user(username="broker-owner")
    workspace = create_workspace(name="Broker", slug="broker", owner=owner)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", config_sha="a" * 40
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="briefing",
        name="Briefing",
        skill_path=".agents/skills/engineering-briefing/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )
    Connection.objects.create(
        workspace=workspace,
        provider="openai",
        kind="model",
        display_name="Model",
        default_model="test",
        is_default=True,
    )
    connection = Connection.objects.create(
        workspace=workspace,
        provider="github",
        kind="mcp",
        binding_key="github",
        display_name="GitHub",
        endpoint_url=GITHUB_MCP_URL,
        access_token="github_pat_test_token",
        auth_strategy="api_key",
        available_tools=list(TOOL_SCHEMAS),
        metadata={"tools": TOOL_SCHEMAS},
    )
    request = CapabilityRequest.objects.create(
        job=job,
        kind="mcp",
        binding="github",
        requested_tools=list(TOOL_SCHEMAS),
    )
    JobGrant.objects.create(
        capability_request=request,
        connection=connection,
        status="approved",
        allowed_tools=list(TOOL_SCHEMAS),
    )
    return Run.objects.create(
        job=job,
        status="running",
        scheduled_for=timezone.now(),
        commit_sha="a" * 40,
        input_snapshot={"skill_path": job.skill_path},
        execution_plan=build_execution_plan(job),
    )


@pytest.mark.django_db
def test_broker_calls_real_sdk_and_records_safe_evidence(planned_run, mcp_http) -> None:
    result = call_external_tool_step(
        planned_run.pk, NAME, {"query": "is:merged"}, "call-1"
    )
    call = next(message for message in mcp_http if message["method"] == "tools/call")
    assert call["params"]["arguments"]["query"] == "repo:example/project is:merged"
    assert "github_pat_test_token" not in json.dumps(result)
    event = planned_run.events.get()
    assert event.metadata["ok"] is True
    assert "github_pat_test_token" not in json.dumps(event.metadata)
    assert "query" not in event.metadata


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        "grant",
        "connection",
        "scope",
        "schema",
        "workspace",
        "binding",
        "canceled",
        "tools",
    ],
)
def test_policy_changes_stop_active_run(planned_run, change, mcp_http) -> None:
    grant = JobGrant.objects.select_related("connection").get(
        capability_request__job=planned_run.job
    )
    connection = grant.connection
    if change == "grant":
        grant.status = "denied"
        grant.save()
    elif change == "connection":
        connection.status = "revoked"
        connection.save()
    elif change == "scope":
        planned_run.job.repository.full_name = "other/repo"
        planned_run.job.repository.save()
    elif change == "schema":
        connection.metadata = {**connection.metadata, "tools": {}}
        connection.save()
    elif change == "workspace":
        other = create_workspace(
            name="Other", slug="other", owner=workspace_owner(planned_run.job.workspace)
        )
        connection.workspace = other
        connection.save()
    elif change == "binding":
        connection.binding_key = "other-binding"
        connection.save()
    elif change == "canceled":
        planned_run.status = "canceled"
        planned_run.save()
    else:
        grant.allowed_tools = []
        grant.save()
    with pytest.raises(ExternalToolError):
        call_external_tool_step(
            planned_run.pk, NAME, {"query": "is:merged"}, "call-denied"
        )
    assert not mcp_http


@pytest.mark.django_db
def test_unapproved_names_and_invalid_arguments_never_reach_mcp(
    planned_run, mcp_http
) -> None:
    with pytest.raises(ToolPolicyError):
        call_external_tool_step(
            planned_run.pk, "mcp.github.update_pull_request", {}, "denied"
        )
    with pytest.raises(ExternalToolError, match="schema"):
        call_external_tool_step(planned_run.pk, NAME, {"query": 42}, "invalid")
    with pytest.raises(ExternalToolError, match="repository"):
        call_external_tool_step(
            planned_run.pk, NAME, {"query": "repo:other/private"}, "outside"
        )
    assert not mcp_http


@pytest.mark.django_db(transaction=True)
def test_agent_mcp_report_and_webhook_payload_flow(
    planned_run, mcp_http, monkeypatch, settings, tmp_path: Path
) -> None:
    settings.STORAGES = {
        **settings.STORAGES,
        "run_artifacts": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": tmp_path},
        },
    }
    storages._storages.clear()
    tool_name = next(
        tool.name
        for tool in external_tools(planned_run)
        if tool.description.startswith(NAME)
    )
    model = TestModel(
        call_tools=[tool_name],
        custom_output_text="What shipped: [PR #1](https://github.com/example/project/pull/1).",
    )
    monkeypatch.setattr(
        "secondshift.runs.execution.run.capture_run_input_step", lambda *args: None
    )
    with WORKSPACE_AGENT.override(model=model):
        result = execute_agent_run(planned_run, "offline-sandbox")
    assert result["succeeded"]
    assert planned_run.events.filter(message__startswith="Tool completed").exists()
    with storages["run_artifacts"].open(result["output"]["path"]) as artifact:
        assert b"What shipped" in artifact.read()
    webhook = Connection.objects.create(
        workspace=planned_run.job.workspace,
        provider="webhook",
        kind="notification",
        binding_key="engineering-reports",
        display_name="Reports",
        delivery_url="https://example.com/hook",
    )
    request = CapabilityRequest.objects.create(
        job=planned_run.job, kind="notification", binding="engineering-reports"
    )
    JobGrant.objects.create(
        capability_request=request, connection=webhook, status="approved"
    )
    planned_run.execution_plan["capabilities"].append(
        {"kind": "notification", "authorized": True, "connection_id": webhook.pk}
    )
    planned_run.summary = result["summary"]
    planned_run.status = "succeeded"
    assert notification_targets(planned_run) == [webhook.pk]
    assert "PR #1" in run_report_payload(planned_run)["text"]


def test_briefing_manifest_is_valid() -> None:
    manifest = load_manifest(Path("examples/engineering-briefing/.secondshift.yaml"))
    assert manifest.jobs["engineering-briefing"].requires.mcps[0].tools == list(
        TOOL_SCHEMAS
    )
    root = load_manifest(Path(".secondshift.yaml"))
    assert Path(root.jobs["engineering-briefing"].skill).is_file()


@pytest.mark.django_db
def test_briefing_report_is_received_by_real_webhook(planned_run, settings) -> None:
    received = []

    class InboxHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), InboxHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.OUTBOUND_ALLOW_LOCAL = True
    planned_run.summary = (
        "What shipped: [PR #1](https://github.com/example/project/pull/1)."
    )
    planned_run.status = "succeeded"
    connection = Connection(
        provider="webhook",
        delivery_url=f"http://127.0.0.1:{server.server_port}/reports",
    )
    try:
        result = deliver_webhook(connection, run_report_payload(planned_run))
        assert result.ok
        assert received[0]["event"] == "run.finished"
        assert "PR #1" in received[0]["text"]
        assert "github_pat_test_token" not in json.dumps(received)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.django_db
def test_one_connection_is_reused_with_independent_job_scopes(
    planned_run, mcp_http
) -> None:
    connection = Connection.objects.get(binding_key="github")
    other_repository = Repository.objects.create(
        workspace=planned_run.job.workspace,
        full_name="example/another",
        config_sha="b" * 40,
    )
    job = Job.objects.create(
        workspace=planned_run.job.workspace,
        repository=other_repository,
        key="other",
        name="Other",
        skill_path="SKILL.md",
        cron="0 9 * * 1",
        config_hash="other",
    )
    request = CapabilityRequest.objects.create(
        job=job, binding="github", kind="mcp", requested_tools=list(TOOL_SCHEMAS)
    )
    JobGrant.objects.create(
        capability_request=request,
        connection=connection,
        status="approved",
        allowed_tools=list(TOOL_SCHEMAS),
    )
    other_run = Run.objects.create(
        job=job,
        status="running",
        commit_sha="b" * 40,
        scheduled_for=timezone.now(),
        execution_plan=build_execution_plan(job),
    )
    call_external_tool_step(planned_run.pk, NAME, {"query": "is:merged"}, "first")
    call_external_tool_step(other_run.pk, NAME, {"query": "is:merged"}, "second")
    queries = [
        message["params"]["arguments"]["query"]
        for message in mcp_http
        if message["method"] == "tools/call"
    ]
    assert queries == [
        "repo:example/project is:merged",
        "repo:example/another is:merged",
    ]
    with pytest.raises(ExternalToolError, match="repository"):
        call_external_tool_step(
            planned_run.pk, NAME, {"query": "repo:example/another"}, "cross-repo"
        )


@pytest.mark.django_db
def test_local_job_without_github_origin_is_not_ready(planned_run) -> None:
    repository = planned_run.job.repository
    repository.source_type = "local"
    repository.clone_url = ""
    repository.save()
    plan = build_execution_plan(planned_run.job)
    assert not plan["ready"]
    assert "github_repository_unknown" in plan["capabilities"][0]["reasons"]


@pytest.mark.django_db
def test_remote_failures_are_reportable_but_policy_denials_still_stop_the_run(
    planned_run, monkeypatch
) -> None:

    tool = external_tools(planned_run)[0]
    context = tool_context(
        WorkspaceToolDeps(
            run_id=planned_run.pk,
            session_id="offline",
            max_output_chars=1000,
            deadline=LATER,
        )
    )

    def unavailable(*args, **kwargs) -> None:
        raise MCPRequestError("GitHub evidence unavailable.")

    monkeypatch.setattr(
        "secondshift.runs.execution.external_tools.call_external_tool_step", unavailable
    )
    with pytest.raises(ToolFailed, match="evidence unavailable"):
        tool.function(context)

    def denied(*args, **kwargs) -> None:
        raise MCPError("Access is no longer approved.")

    monkeypatch.setattr(
        "secondshift.runs.execution.external_tools.call_external_tool_step", denied
    )
    with pytest.raises(ExternalToolError, match="no longer approved"):
        tool.function(context)


def set_rules(run: Run, rules: dict) -> None:
    grant = JobGrant.objects.get(capability_request__job=run.job)
    grant.tool_rules = rules
    grant.save()
    run.execution_plan = build_execution_plan(run.job)
    run.save()


@pytest.mark.django_db
def test_owner_rules_cannot_loosen_the_github_scope(planned_run, mcp_http) -> None:
    set_rules(
        planned_run,
        {
            "search_pull_requests": {
                "owner": {"type": "fixed", "value": "attacker"},
                "repo": {"type": "fixed", "value": "private"},
            }
        },
    )

    with pytest.raises(ExternalToolError, match="approved repository"):
        call_external_tool_step(planned_run.pk, NAME, {"query": "x"}, "call-1")

    assert not mcp_http


@pytest.mark.django_db
def test_a_call_that_breaks_an_owner_rule_never_reaches_mcp(
    planned_run, mcp_http
) -> None:
    set_rules(
        planned_run,
        {"search_pull_requests": {"owner": {"type": "forbidden"}}},
    )

    with pytest.raises(ExternalToolError, match="owner"):
        call_external_tool_step(
            planned_run.pk, NAME, {"query": "x", "owner": "someone"}, "blocked"
        )

    assert not mcp_http
    event = planned_run.events.get()
    assert event.message == f"Tool blocked by an owner rule: {NAME}"
    assert "someone" not in json.dumps(event.metadata)


@pytest.mark.django_db
def test_rules_changed_mid_run_do_not_affect_the_run(planned_run, mcp_http) -> None:
    grant = JobGrant.objects.get(capability_request__job=planned_run.job)
    grant.tool_rules = {"search_pull_requests": {"owner": {"type": "forbidden"}}}
    grant.save()

    call_external_tool_step(
        planned_run.pk, NAME, {"query": "x", "owner": "example"}, "call-1"
    )

    assert any(message["method"] == "tools/call" for message in mcp_http)


@pytest.mark.django_db
def test_the_model_sees_the_narrowed_schema(planned_run) -> None:
    set_rules(
        planned_run,
        {"search_pull_requests": {"owner": {"type": "allowed", "values": ["a"]}}},
    )

    tool = next(
        item
        for item in external_tools(planned_run)
        if "search_pull_requests" in item.name
    )

    schema = tool.function_schema.json_schema
    assert schema["properties"]["owner"]["enum"] == ["a"]
    assert "owner" in schema["required"]
