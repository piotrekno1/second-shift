from django.contrib.auth import get_user_model

from secondshift.automations.models import CapabilityRequest, Job, JobGrant
from secondshift.automations.planning import build_execution_plan
from secondshift.core.models import Workspace
from secondshift.integrations.models import Connection
from secondshift.repositories.models import Repository


def create_job(db) -> Job:
    user = get_user_model().objects.create_user(username="owner")
    workspace = Workspace.objects.create(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="example/project",
    )
    Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        status=Connection.Status.CONNECTED,
        allowed_models=["claude-opus-5-5"],
        default_model="claude-opus-5-5",
        is_default=True,
    )
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

    plan = build_execution_plan(job)

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
            "max_tool_calls": 64,
            "max_tool_result_chars": 65536,
            "token_budget": 2_000_000,
            "timeout_seconds": 900,
        },
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
