from dataclasses import replace

import pytest
from django.contrib.auth import get_user_model

from secondshift.config.constants import AgentBackend
from secondshift.connections.models import Connection
from secondshift.jobs import planning
from secondshift.jobs.execution import choose_execution
from secondshift.jobs.manifest import TaskManifest
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.readiness import readiness_problems
from secondshift.repositories.models import Repository
from secondshift.workspaces.dao import create_workspace


def create_job(db) -> Job:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="example/project",
    )
    model = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        status=Connection.Status.CONNECTED,
        allowed_models=["claude-opus-5-5"],
        default_model="claude-opus-5-5",
    )
    choose_execution(model.workspace, model=model)
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        config_hash="abc",
    )


def test_plan_fails_closed_without_grant(db) -> None:
    job = create_job(db)
    CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics"],
    )

    plan = build_execution_plan(job)

    assert plan["ready"] is False
    assert plan["capabilities"][0]["reasons"] == ["no_grant"]


def test_plan_fails_closed_without_a_model_provider(db) -> None:
    job = create_job(db)
    Connection.objects.filter(kind=Connection.Kind.MODEL).delete()

    plan = build_execution_plan(Job.objects.get(pk=job.pk))

    assert plan["ready"] is False
    assert plan["model"] is None


def test_plan_contains_only_effective_approved_tools(db) -> None:
    job = create_job(db)
    request = CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics"],
    )
    connection = Connection.objects.create(
        workspace=job.workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key="observability",
        display_name="Grafana production",
        available_tools=["query_metrics", "delete_dashboard"],
        credential_reference="vault://grafana/production",
    )
    JobGrant.objects.create(
        capability_request=request,
        connection=connection,
        status=JobGrant.Status.APPROVED,
        allowed_tools=["query_metrics"],
    )

    plan = build_execution_plan(job)

    assert plan["ready"] is True
    assert plan["agent"] == {
        "driver": "pydantic-ai",
        "protocol": "pydantic-ai-v1",
        "limits": {
            "max_turns": 24,
            "max_tool_calls": 150,
            "max_tool_result_chars": 65536,
            "token_budget": 2_000_000,
            "timeout_seconds": 900,
        },
        "subagents": 0,
        "tools": [
            "mcp.observability.query_metrics",
            "workspace.list_files",
            "workspace.read_file",
            "workspace.run_command",
            "workspace.search_text",
        ],
    }
    assert plan["capabilities"][0]["effective_tools"] == ["query_metrics"]
    assert "credential_reference" not in str(plan)


def test_plan_fails_closed_when_requested_tool_was_not_discovered(db) -> None:
    job = create_job(db)
    request = CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics"],
    )
    connection = Connection.objects.create(
        workspace=job.workspace,
        provider=Connection.Provider.MCP,
        kind=Connection.Kind.MCP,
        binding_key="observability",
        display_name="Grafana production",
        available_tools=[],
    )
    JobGrant.objects.create(
        capability_request=request,
        connection=connection,
        status=JobGrant.Status.APPROVED,
        allowed_tools=["query_metrics"],
    )

    plan = build_execution_plan(job)

    assert plan["ready"] is False
    assert "tools_unavailable" in plan["capabilities"][0]["reasons"]


def test_plan_fails_closed_for_unsupported_mcp_providers(db) -> None:
    job = create_job(db)
    request = CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics"],
    )
    connection = Connection.objects.create(
        workspace=job.workspace,
        provider=Connection.Provider.API,
        kind=Connection.Kind.MCP,
        binding_key="observability",
        display_name="Custom server",
        available_tools=["query_metrics"],
    )
    JobGrant.objects.create(
        capability_request=request,
        connection=connection,
        status=JobGrant.Status.APPROVED,
        allowed_tools=["query_metrics"],
    )

    plan = build_execution_plan(job)

    assert plan["ready"] is False
    assert plan["capabilities"][0]["reasons"] == ["mcp_provider_unsupported"]
    assert "external" not in plan["capabilities"][0]


def optional_request(job: Job, binding: str) -> CapabilityRequest:
    return CapabilityRequest.objects.create(
        job=job,
        binding=binding,
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["search"],
        required=False,
    )


def mcp_connection(job: Job, binding: str, **fields: object) -> Connection:
    return Connection.objects.create(
        workspace=job.workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key=binding,
        display_name=binding,
        status=Connection.Status.CONNECTED,
        available_tools=["search"],
        **fields,
    )


def test_plan_says_why_each_binding_is_missing(db) -> None:
    job = create_job(db)
    optional_request(job, "sentry")
    waiting = optional_request(job, "grafana")
    mcp_connection(job, "grafana")
    denied = optional_request(job, "linear")
    JobGrant.objects.create(
        capability_request=denied,
        connection=mcp_connection(job, "linear"),
        status=JobGrant.Status.DENIED,
    )
    JobGrant.objects.create(capability_request=waiting)

    plan = build_execution_plan(job)

    missing = {item["binding"]: item["missing"] for item in plan["capabilities"]}
    assert missing == {
        "grafana": "waiting for approval",
        "linear": "denied",
        "sentry": "not connected",
    }
    assert plan["ready"] is True


def test_plan_says_nothing_is_missing_for_authorized_bindings(db) -> None:
    job = create_job(db)
    request = optional_request(job, "grafana")
    JobGrant.objects.create(
        capability_request=request,
        connection=mcp_connection(job, "grafana"),
        status=JobGrant.Status.APPROVED,
        allowed_tools=["search"],
    )

    plan = build_execution_plan(job)

    assert plan["capabilities"][0]["authorized"] is True
    assert "missing" not in plan["capabilities"][0]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            {"default_model": ""},
            "The model provider Anthropic has no model selected.",
        ),
        (
            {"metadata": {"last_check": {"ok": False}}},
            "The model provider Anthropic failed its last check.",
        ),
    ],
)
def test_plan_fails_closed_with_an_unusable_default_model(
    db, change: dict[str, object], problem: str
) -> None:
    job = create_job(db)
    Connection.objects.filter(kind=Connection.Kind.MODEL).update(**change)
    job = Job.objects.get(pk=job.pk)

    plan = build_execution_plan(job)

    assert plan["ready"] is False
    assert plan["model"] is None
    assert problem in readiness_problems(job)


def test_plan_caps_subagents_at_the_server_maximum(db, settings) -> None:
    settings.RUN_SUBAGENTS_MAX = 2
    job = create_job(db)
    job.subagents = 5

    assert build_execution_plan(job)["agent"]["subagents"] == 2


@pytest.mark.parametrize(
    ("backend", "planned"),
    [(AgentBackend.CLAUDE_CODE, 2), (AgentBackend.CODEX, 0)],
)
def test_codex_runs_plan_no_subagents(db, monkeypatch, backend, planned) -> None:
    job = create_job(db)
    job.subagents = 2
    selection = planning.workspace_execution(job.workspace)
    monkeypatch.setattr(
        planning,
        "workspace_execution",
        lambda workspace: replace(selection, backend=backend),
    )

    assert build_execution_plan(job)["agent"]["subagents"] == planned


def test_a_task_can_start_subagents(db) -> None:
    job = create_job(db)
    definition = TaskManifest(name="Audit", subagents=1)

    plan = planning.build_skill_execution_plan(
        job.repository, definition, key="audit", commit_sha="a" * 40
    )

    assert plan["agent"]["subagents"] == 1
