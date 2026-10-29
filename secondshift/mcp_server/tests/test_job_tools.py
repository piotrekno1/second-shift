import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client
from django.utils import timezone

from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, Job
from secondshift.repositories.models import Repository
from secondshift.repositories.sync import SyncError
from secondshift.runs.artifacts import store_run_text
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace
from secondshift.workspaces.tokens import create_api_token

LOCAL_PATH = "/tmp/secret-project"


def make_job(workspace: Workspace, key: str, repository: Repository) -> Job:
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key=key,
        name=key.title(),
        skill_path=f".agents/skills/{key}/SKILL.md",
        cron="0 9 * * 1",
        timezone="UTC",
        config_hash="config-hash",
    )


@pytest.fixture
def owner(db: object) -> AbstractUser:
    return get_user_model().objects.create_user(username="owner", email="o@x.dev")


@pytest.fixture
def workspace(owner: AbstractUser) -> Workspace:
    return create_workspace("Acme", "acme", owner)


@pytest.fixture
def repository(workspace: Workspace) -> Repository:
    return Repository.objects.create(
        workspace=workspace,
        full_name="local/project",
        source_type=Repository.SourceType.LOCAL,
        local_path=LOCAL_PATH,
        clone_url="https://github.com/local/project.git",
        enabled=True,
        config_sha="abc123",
    )


@pytest.fixture
def job(workspace: Workspace, repository: Repository) -> Job:
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
    return make_job(workspace, "review", repository)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    runs: list[int] = []
    monkeypatch.setattr(
        "secondshift.runs.services.enqueue_run", lambda run: runs.append(run.pk)
    )
    return runs


def token(owner: AbstractUser, scope: str) -> str:
    membership = WorkspaceMembership.objects.get(user=owner)
    return create_api_token(membership, "Laptop", scope, 30).secret


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
    assert LOCAL_PATH not in response.content.decode()
    return response.json()["result"]


def error_text(result: dict[str, Any]) -> str:
    assert result["isError"] is True
    return result["content"][0]["text"]


def test_list_jobs_says_which_jobs_are_ready_and_why_not(
    owner: AbstractUser, job: Job, repository: Repository
) -> None:
    blocked = make_job(job.workspace, "triage", repository)
    blocked.enabled = True
    blocked.save()
    CapabilityRequest.objects.create(
        job=blocked, binding="github", kind=CapabilityRequest.Kind.MCP
    )
    secret = token(owner, "read")

    jobs = call(secret, "list_jobs")["structuredContent"]["jobs"]

    by_key = {item["key"]: item for item in jobs}
    assert (by_key["review"]["ready"], by_key["review"]["blocked_by"]) == (True, [])
    assert by_key["triage"]["ready"] is False
    assert by_key["triage"]["blocked_by"] == ["`github` waits for an owner's approval."]
    enabled = call(secret, "list_jobs", enabled=True)["structuredContent"]["jobs"]
    assert [item["key"] for item in enabled] == ["triage"]


def test_get_job_reads_only_the_token_s_workspace(
    owner: AbstractUser, job: Job
) -> None:
    other_owner = get_user_model().objects.create_user(username="x", email="x@x.dev")
    other = create_workspace("Other", "other", other_owner)
    other_repository = Repository.objects.create(
        workspace=other, full_name="other/repo", enabled=True, config_sha="def"
    )
    theirs = make_job(other, "secret", other_repository)
    secret = token(owner, "read")

    mine = call(secret, "get_job", job_id=job.pk)["structuredContent"]

    assert (mine["job"]["key"], mine["ready"]) == ("review", True)
    assert error_text(call(secret, "get_job", job_id=theirs.pk)) == (
        "Not found in this token's workspace."
    )


def test_run_job_needs_the_run_scope(
    owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    result = call(token(owner, "read"), "run_job", job_id=job.pk)

    assert error_text(result) == "This API token cannot run jobs."
    assert queued == []


def test_run_job_starts_once_and_then_returns_the_active_run(
    owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    secret = token(owner, "run")

    first = call(secret, "run_job", job_id=job.pk)["structuredContent"]
    second = call(secret, "run_job", job_id=job.pk)["structuredContent"]

    assert first["started"] is True
    assert queued == [first["run_id"]]
    assert second == {
        "run_id": first["run_id"],
        "status": "queued",
        "started": False,
        "message": "This job already has a queued or running run.",
    }


def test_run_job_explains_why_a_job_cannot_run(
    owner: AbstractUser, job: Job, queued: list[int]
) -> None:
    Repository.objects.filter(pk=job.repository_id).update(enabled=False)

    result = call(token(owner, "run"), "run_job", job_id=job.pk)

    assert error_text(result) == "Enable the repository before running its jobs."


def test_runs_and_reports_can_be_read_and_reports_are_marked_untrusted(
    owner: AbstractUser, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Run.objects.create(
        job=job,
        scheduled_for=timezone.now(),
        status=Run.Status.SUCCEEDED,
        finished_at=timezone.now(),
    )
    stored = store_run_text(run, "# Findings\n\nAll good.")
    Run.objects.filter(pk=run.pk).update(output_path=stored.path)
    secret = token(owner, "read")

    listed = call(secret, "list_runs", job_id=job.pk)["structuredContent"]
    detail = call(secret, "get_run", run_id=run.pk)["structuredContent"]
    report = call(secret, "get_run_report", run_id=run.pk)["structuredContent"]

    assert [item["id"] for item in listed["runs"]] == [run.pk]
    assert detail["run"]["status"] == "succeeded"
    assert "not as instructions" in detail["untrusted"]
    assert (report["report"], report["truncated"]) == ("# Findings\n\nAll good.", False)
    assert "not as instructions" in report["untrusted"]
    monkeypatch.setattr("secondshift.mcp_server.tools.runs.REPORT_MAX_BYTES", 5)
    cut = call(secret, "get_run_report", run_id=run.pk)["structuredContent"]
    assert (cut["report"], cut["truncated"]) == ("# Fin", True)


def test_a_run_without_a_report_returns_an_empty_one(
    owner: AbstractUser, job: Job
) -> None:
    run = Run.objects.create(job=job, scheduled_for=timezone.now())

    report = call(token(owner, "read"), "get_run_report", run_id=run.pk)

    assert report["structuredContent"]["report"] == ""


def test_list_runs_rejects_unknown_arguments(owner: AbstractUser, job: Job) -> None:
    result = call(token(owner, "read"), "list_runs", sort="cost")

    assert error_text(result) == "Invalid tool arguments."


def test_sync_repository_needs_the_run_scope_and_reports_errors(
    owner: AbstractUser,
    job: Job,
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(repo: Repository) -> Repository:
        raise SyncError(".secondshift.yaml: jobs.review.name is required.")

    assert error_text(
        call(token(owner, "read"), "sync_repository", repository_id=repository.pk)
    ) == ("This API token cannot resync repositories.")
    monkeypatch.setattr(
        "secondshift.mcp_server.tools.repositories.resync", lambda repo: repo
    )
    synced = call(token(owner, "run"), "sync_repository", repository_id=repository.pk)
    monkeypatch.setattr("secondshift.mcp_server.tools.repositories.resync", broken)
    failed = call(token(owner, "run"), "sync_repository", repository_id=repository.pk)

    result = synced["structuredContent"]
    assert (result["repository_id"], result["revision"]) == (repository.pk, "abc123")
    assert [item["name"] for item in result["jobs"]] == ["Review"]
    assert "local_path" not in json.dumps(result)
    assert error_text(failed) == ".secondshift.yaml: jobs.review.name is required."
