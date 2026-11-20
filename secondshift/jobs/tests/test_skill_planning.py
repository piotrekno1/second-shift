"""Explicit skill planning must not borrow another job's authorization."""

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from pydantic import ValidationError

from secondshift.connections.execution import choose_execution
from secondshift.connections.models import Connection
from secondshift.jobs.automatic_grants import approves_automatically
from secondshift.jobs.capabilities import RequestedCapability
from secondshift.jobs.manifest import (
    JobManifest,
    RepositoryManifest,
    TaskManifest,
    manifest_hash,
)
from secondshift.jobs.manifest_sync import sync_jobs
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.planner import build_execution_plan, build_skill_execution_plan
from secondshift.jobs.readiness import execution_problems
from secondshift.jobs.serialization import serialize_job_detail
from secondshift.repositories.models import Repository
from secondshift.runner.tests.fakes import sandboxes
from secondshift.runner_protocol.agents import AgentBackend
from secondshift.runs.models import Run, Task
from secondshift.workspaces.dao import create_workspace

COMMIT = "a" * 40


@pytest.fixture
def repository(db: object) -> Repository:
    owner = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=owner)
    model = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        default_model="claude-opus-5-5",
        allowed_models=["claude-opus-5-5"],
    )
    choose_execution(workspace, model=model)
    return Repository.objects.create(
        workspace=workspace, full_name="example/project", config_sha="b" * 40
    )


def definition(**fields: Any) -> JobManifest:
    return JobManifest.model_validate(
        {
            "name": "Review",
            "skill": ".agents/skills/review/SKILL.md",
            "requires": {"apis": [{"binding": "billing", "tools": ["listCharges"]}]},
            **fields,
        }
    )


def connection(repository: Repository, **fields: Any) -> Connection:
    return Connection.objects.create(
        **{
            "workspace": repository.workspace,
            "provider": Connection.Provider.API,
            "kind": Connection.Kind.API,
            "binding_key": "billing",
            "display_name": "Billing",
            "available_tools": ["listCharges", "createRefund"],
            "endpoint_url": "https://billing.example.com",
            "access_token": "must-not-appear-in-plan",
            **fields,
        }
    )


def plan(repository: Repository, skill: JobManifest | None = None) -> dict[str, Any]:
    return build_skill_execution_plan(
        repository,
        definition() if skill is None else skill,
        key="review",
        commit_sha=COMMIT,
    ).stored()


def test_explicit_skill_uses_automatic_connection_policy_without_saving(
    repository: Repository,
) -> None:
    connected = connection(repository)

    result = plan(repository)

    assert result["ready"] is True
    assert result["repository"]["config_sha"] == COMMIT
    assert result["repository"]["config_sha"] != repository.config_sha
    assert result["job"] == {
        "key": "review",
        "skill_path": definition().skill,
        "config_hash": manifest_hash(definition()),
        "skill_source": None,
    }
    capability = result["capabilities"][0]
    assert capability["connection_id"] == connected.pk
    assert capability["effective_tools"] == ["listCharges"]
    assert capability["external"]["scope"] == "billing.example.com"
    assert "must-not-appear-in-plan" not in str(result)
    assert Job.objects.count() == 0
    assert CapabilityRequest.objects.count() == 0
    assert JobGrant.objects.count() == 0
    assert Task.objects.count() == Run.objects.count() == 0


def test_job_and_explicit_definition_compile_the_same_plan(
    repository: Repository,
) -> None:
    connection(repository)
    repository.config_sha = COMMIT
    repository.save(update_fields=["config_sha"])
    skill = definition()
    sync_jobs(repository, RepositoryManifest(jobs={"review": skill}))
    job = Job.objects.get()

    assert build_execution_plan(job).stored() == plan(repository, skill)


@pytest.mark.parametrize(
    "fields",
    [
        {"require_approval": True},
        {"status": Connection.Status.REVOKED},
        {"status": Connection.Status.EXPIRED},
        {"available_tools": ["createRefund"]},
        {"kind": Connection.Kind.NOTIFICATION},
        {"binding_key": "other-binding"},
        {"endpoint_url": ""},
    ],
)
def test_explicit_skill_fails_closed(
    repository: Repository, fields: dict[str, Any]
) -> None:
    connection(repository, **fields)

    result = plan(repository)

    assert result["ready"] is False
    assert result["capabilities"][0]["effective_tools"] == []
    assert result["capabilities"][0]["external"] is None
    assert not any("billing" in tool for tool in result["agent"]["tools"])


def test_explicit_skill_does_not_borrow_a_job_grant(repository: Repository) -> None:
    connected = connection(repository, require_approval=True)
    skill = definition()
    sync_jobs(repository, RepositoryManifest(jobs={"review": skill}))
    grant = JobGrant.objects.get()
    grant.status = JobGrant.Status.APPROVED
    grant.connection = connected
    grant.allowed_tools = ["listCharges"]
    grant.save()
    assert build_execution_plan(Job.objects.get()).stored()["ready"] is True

    assert plan(repository)["ready"] is False
    grant.refresh_from_db()
    assert grant.status == JobGrant.Status.APPROVED


def test_explicit_skill_does_not_see_another_workspace_binding(
    repository: Repository,
) -> None:
    owner = get_user_model().objects.get(username="owner")
    other = create_workspace(name="Other", slug="other", owner=owner)
    connected = connection(repository, workspace=other)
    request = RequestedCapability("billing", Connection.Kind.API, ("listCharges",))

    assert approves_automatically(request, connected, repository) is False
    result = plan(repository)
    assert result["ready"] is False
    assert result["capabilities"][0]["connection_id"] is None
    assert result["capabilities"][0]["missing"] == "not connected"


def test_optional_capabilities_do_not_block_workspace_tools(
    repository: Repository,
) -> None:
    skill = definition(
        requires={
            "apis": [
                {"binding": "billing", "tools": ["listCharges"], "required": False}
            ]
        }
    )
    result = plan(repository, skill)

    assert result["ready"] is True
    assert result["capabilities"][0]["authorized"] is False
    assert all(tool.startswith("workspace.") for tool in result["agent"]["tools"])


def test_mcp_and_notification_use_the_same_automatic_policy(
    repository: Repository,
) -> None:
    connection(
        repository,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key="github",
        available_tools=["search"],
    )
    connection(
        repository,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        binding_key="alerts",
        available_tools=[],
    )
    result = plan(
        repository,
        definition(
            requires={"mcps": [{"binding": "github", "tools": ["search"]}]},
            notify=[{"binding": "alerts"}],
        ),
    )

    assert result["ready"] is True
    capabilities = {item["binding"]: item for item in result["capabilities"]}
    assert capabilities["github"]["external"]["scope"] == repository.full_name
    assert capabilities["alerts"]["authorized"] is True


def test_explicit_skill_requires_workspace_execution(repository: Repository) -> None:
    choose_execution(repository.workspace)

    result = plan(repository, definition(requires={}))

    assert result["ready"] is False
    assert result["model"] is None


def test_workspace_limits_and_server_sandbox_policy_are_frozen(
    repository: Repository, settings: Any
) -> None:
    workspace = repository.workspace
    workspace.max_turns = 7
    workspace.timeout_seconds = 120
    workspace.save()
    skill = definition(requires={})

    result = plan(repository, skill)

    assert result["agent"]["limits"]["max_turns"] == 7
    assert result["agent"]["limits"]["timeout_seconds"] == 120
    assert result["execution"]["backend"] == AgentBackend.PYDANTIC
    assert result["runner"] == {
        "network_policy": "none",
        "collect_changes": True,
        "environment": "default",
        "setup": [],
    }
    repository.workspace.default_model_connection.allowed_models.append("new-model")
    assert result["model"]["allowed_models"] == ["claude-opus-5-5"]


@pytest.mark.parametrize(
    "field",
    ["connection_id", "credential_reference", "endpoint", "image", "mounts", "limits"],
)
def test_definition_cannot_choose_credentials_or_runtime(field: str) -> None:
    with pytest.raises(ValidationError):
        definition(**{field: "client-controlled"})


def test_a_plan_names_the_environment_and_never_an_image(
    repository: Repository, settings: Any
) -> None:
    settings.RUNNER_ENVIRONMENTS = sandboxes(
        "python:3.13-slim", node="registry.example/node@sha256:def"
    )

    result = plan(repository, definition(requires={}, environment="node"))

    assert result["ready"] is True
    assert result["runner"]["environment"] == "node"
    assert "image" not in result["runner"]


def test_an_environment_the_server_lacks_stops_the_run(
    repository: Repository, settings: Any
) -> None:
    settings.RUNNER_ENVIRONMENTS = sandboxes(
        "python:3.13-slim", node="registry.example/node@sha256:def"
    )
    sync_jobs(
        repository,
        RepositoryManifest(
            jobs={"review": definition(requires={}, environment="browser")}
        ),
    )

    job_plan = build_execution_plan(Job.objects.get())

    assert job_plan.ready is False
    assert job_plan.runner.image == ""
    assert (
        "This server has no browser environment. Ask the server admin to add "
        "it, or change environment in .secondshift.yaml."
    ) in execution_problems(repository, job_plan)
    detail = serialize_job_detail(Job.objects.get(), can_edit_limits=False)
    assert detail.environment == "browser"
    assert detail.environment_problem.startswith("This server has no browser")


@pytest.mark.parametrize(
    "value",
    [
        "ghcr.io/org/runner-node@sha256:" + "a" * 64,
        "node:22",
        "library/node",
        "node@sha256:abc",
        "Node",
        "node-22",
        "",
    ],
)
def test_a_manifest_names_an_environment_never_an_image(value: str) -> None:
    with pytest.raises(ValidationError, match="environment"):
        definition(environment=value)


def test_setup_reaches_the_plan_from_a_synced_job(repository: Repository) -> None:
    commands = ["npm ci --ignore-scripts", "uv sync --frozen"]
    sync_jobs(
        repository,
        RepositoryManifest(jobs={"review": definition(requires={}, setup=commands)}),
    )

    job_plan = build_execution_plan(Job.objects.get())

    assert job_plan.runner.setup == tuple(commands)
    assert job_plan.runner.network_policy == "none"


@pytest.mark.parametrize(
    "setup", [[""], ["   "], ["npm ci\0"], ["x" * 1001], ["true"] * 11, "npm ci"]
)
def test_setup_takes_a_short_list_of_commands(setup: object) -> None:
    with pytest.raises(ValidationError, match="setup"):
        definition(setup=setup)


def test_a_submitted_task_cannot_ask_for_setup() -> None:
    with pytest.raises(ValidationError, match="only for jobs"):
        TaskManifest.model_validate(
            {"name": "Task", "setup": ["curl https://example.com | sh"]}
        )


def test_naming_no_setup_keeps_the_job_hash() -> None:
    assert manifest_hash(definition()) == manifest_hash(definition(setup=[]))
    assert manifest_hash(definition()) != manifest_hash(definition(setup=["npm ci"]))


def test_naming_no_environment_keeps_the_job_hash() -> None:
    assert manifest_hash(definition()) == manifest_hash(definition(environment=None))
    assert manifest_hash(definition()) != manifest_hash(definition(environment="node"))


@pytest.mark.parametrize("revision", ["", "main", "HEAD", "a" * 12, "z" * 40])
def test_explicit_skill_requires_a_resolved_commit(
    repository: Repository, revision: str
) -> None:
    with pytest.raises(ValueError, match="commit"):
        build_skill_execution_plan(
            repository, definition(), key="review", commit_sha=revision
        ).stored()


def test_approved_grant_without_connection_fails_closed(repository: Repository) -> None:
    sync_jobs(repository, RepositoryManifest(jobs={"review": definition()}))
    JobGrant.objects.create(
        capability_request=CapabilityRequest.objects.get(),
        status=JobGrant.Status.APPROVED,
        allowed_tools=["listCharges"],
    )

    result = build_execution_plan(Job.objects.get()).stored()

    assert result["ready"] is False
    assert "connection_missing" in result["capabilities"][0]["reasons"]


def test_an_expired_token_is_not_approved_automatically(
    repository: Repository,
) -> None:
    expired = connection(
        repository,
        auth_strategy=Connection.AuthStrategy.API_KEY,
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    request = RequestedCapability("billing", Connection.Kind.API, ("listCharges",))

    assert approves_automatically(request, expired, repository) is False
