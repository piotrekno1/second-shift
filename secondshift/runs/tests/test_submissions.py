"""Submission, replay, execution and review of jobless repository tasks."""

import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db import connection as database
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.utils import timezone
from pydantic import ValidationError

from secondshift.config.constants import TaskOrigin
from secondshift.connections.errors import ExternalToolError
from secondshift.connections.models import AgentConnection, Connection
from secondshift.gateway.models import ModelUsage
from secondshift.gateway.tokens import aauthenticate, authenticate, issue_run_token
from secondshift.jobs.execution import choose_execution
from secondshift.jobs.manifest import JobManifest
from secondshift.jobs.models import Job
from secondshift.mcp_server.tests.test_job_tools import call, error_text, token
from secondshift.repositories.local import committed_file, git
from secondshift.repositories.models import Repository
from secondshift.runs.artifacts import artifact_storage, store_run_text
from secondshift.runs.execution.agent_broker import run_connection
from secondshift.runs.execution.run import (
    PULL_REQUEST_NOTE,
    capture_run_input_step,
    run_prompt,
)
from secondshift.runs.execution.tool_broker import broker_connection
from secondshift.runs.history import previous_reports
from secondshift.runs.models import Run, RunDelivery, Task
from secondshift.runs.orchestration.workflows import (
    deliver_run_notification_step,
    finish_run_step,
    prepare_source_step,
    start_runner_session_step,
)
from secondshift.runs.reporting.health import week_summary
from secondshift.runs.reporting.notifications import (
    notification_targets,
    run_report_payload,
)
from secondshift.runs.retention import prune_runs
from secondshift.runs.services import RunRefused
from secondshift.runs.submissions import SkillSubmission, submit_skill
from secondshift.runs.transitions import start_run
from secondshift.workspaces.models import WorkspaceMembership
from secondshift.workspaces.services import create_workspace

pytestmark = pytest.mark.django_db(transaction=True)
SKILL = ".agents/skills/review/SKILL.md"


@pytest.fixture
def owner() -> User:
    return get_user_model().objects.create_user(username="owner")


@pytest.fixture
def repository(owner: User, tmp_path: Path, settings: Any) -> Repository:
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / SKILL).parent.mkdir(parents=True)
    (root / SKILL).write_text("# Review\nWrite a useful report.")
    git(root, "add", ".")
    git(root, "commit", "-m", "Add skill")
    workspace = create_workspace("Test", "test", owner)
    model = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        default_model="claude-opus-5-5",
    )
    choose_execution(workspace, model=model)
    settings.LOCAL_REPOSITORIES_ENABLED = True
    settings.SOURCE_CACHE_ROOT = tmp_path / "source-cache"
    return Repository.objects.create(
        workspace=workspace,
        source_type=Repository.SourceType.LOCAL,
        local_path=str(root),
        full_name="local/project",
        default_branch="main",
        enabled=True,
    )


@pytest.fixture(autouse=True)
def queued(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    ids: list[int] = []
    monkeypatch.setattr(
        "secondshift.runs.submissions.enqueue_run", lambda run: ids.append(run.pk)
    )
    return ids


def submission(repository: Repository, **fields: Any) -> SkillSubmission:
    return SkillSubmission.model_validate(
        {
            "repository_id": repository.pk,
            "definition": {"name": "Review tonight", "skill": SKILL},
            "input": "Focus on installation instructions.",
            "idempotency_key": "review-tonight-1",
            **fields,
        }
    )


def start(repository: Repository, owner: User, **fields: Any) -> Run:
    return submit_skill(
        repository, submission(repository, **fields), owner, TaskOrigin.MANUAL
    ).run


def test_accepts_pins_and_replays_without_touching_jobs(
    repository: Repository, owner: User, queued: list[int]
) -> None:
    first = submit_skill(repository, submission(repository), owner, TaskOrigin.MANUAL)
    root = Path(repository.local_path)
    pinned = git(root, "rev-parse", "HEAD")
    (root / SKILL).write_text("# Changed skill")
    git(root, "commit", "-am", "Change skill")
    second = submit_skill(repository, submission(repository), owner, TaskOrigin.MANUAL)

    assert first.started is True and second.started is False
    assert first.run.pk == second.run.pk
    assert queued == [first.run.pk]
    assert first.run.job_id is None and first.run.task.job_id is None
    assert first.run.task.repository == repository
    assert first.run.task.submitted_by == owner
    assert first.run.task.origin == TaskOrigin.MANUAL
    assert first.run.commit_sha == first.run.task.commit_sha == pinned
    assert first.run.execution_plan["repository"]["config_sha"] == pinned
    assert "useful report" in committed_file(root, pinned, SKILL)
    assert first.run.input_snapshot == first.run.task.input_snapshot
    assert "installation instructions" in run_prompt(first.run)
    assert Task.objects.count() == Run.objects.count() == 1
    assert not Job.objects.exists()


@pytest.mark.parametrize("revision", ["main", "commit"])
def test_explicit_revision(repository: Repository, owner: User, revision: str) -> None:
    pinned = git(Path(repository.local_path), "rev-parse", "HEAD")
    run = start(
        repository, owner, revision=pinned if revision == "commit" else revision
    )
    assert run.commit_sha == pinned


@pytest.mark.parametrize("revision", ["missing-branch", "--all", "HEAD~1", "main:path"])
def test_invalid_revision_creates_nothing(
    repository: Repository, owner: User, revision: str
) -> None:
    with pytest.raises(RunRefused):
        start(repository, owner, revision=revision)
    assert not Task.objects.exists() and not Run.objects.exists()


def test_symlink_skill_is_rejected(repository: Repository, owner: User) -> None:
    root = Path(repository.local_path)
    (root / SKILL).unlink()
    (root / SKILL).symlink_to("/etc/passwd")
    git(root, "add", ".")
    git(root, "commit", "-m", "Link is not a skill")
    with pytest.raises(RunRefused, match="unavailable"):
        start(repository, owner)
    assert not Task.objects.exists()


def test_accepts_backlog_but_refuses_conflicting_retry(
    repository: Repository, owner: User
) -> None:
    run = start(repository, owner)
    following = start(repository, owner, idempotency_key="different")
    assert following.pk != run.pk
    assert following.status == run.status == Run.Status.QUEUED
    with pytest.raises(RunRefused, match="different submission"):
        start(repository, owner, input="Different scope")
    assert Task.objects.count() == Run.objects.count() == 2


def test_new_submission_allowed_after_completion(
    repository: Repository, owner: User
) -> None:
    run = start(repository, owner)
    Run.objects.filter(pk=run.pk).update(status=Run.Status.SUCCEEDED)
    assert start(repository, owner, idempotency_key="next").pk != run.pk


def test_disabled_repository_creates_nothing(
    repository: Repository, owner: User
) -> None:
    repository.enabled = False
    repository.save()
    with pytest.raises(RunRefused):
        start(repository, owner)
    assert not Task.objects.exists()


def test_missing_approval_creates_nothing(repository: Repository, owner: User) -> None:
    fields = {
        "definition": {
            "name": "Review",
            "skill": SKILL,
            "requires": {"apis": [{"binding": "billing", "tools": ["listCharges"]}]},
        }
    }
    with pytest.raises(RunRefused, match="approval"):
        start(repository, owner, **fields)
    assert not Task.objects.exists()


def test_run_creation_failure_rolls_back_assignment(
    repository: Repository,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
    queued: list[int],
) -> None:
    def fail(**fields: Any) -> Run:
        raise IntegrityError("run creation failed")

    monkeypatch.setattr(Run.objects, "create", fail)
    with pytest.raises(IntegrityError):
        start(repository, owner)
    assert not Task.objects.exists() and not queued


def test_http_submission_is_reviewable_and_workspace_scoped(
    repository: Repository, owner: User
) -> None:
    client = Client()
    client.force_login(owner)
    response = client.post(
        "/api/tasks",
        submission(repository).model_dump(),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["started"] is True and data["run"]["job_id"] is None
    run_id = data["run"]["id"]
    assert client.get(f"/api/runs/{run_id}").status_code == 200
    listed = client.get(
        "/api/runs", {"repository_id": repository.pk, "q": "Review tonight"}
    ).json()
    assert listed["count"] == 1 and listed["items"][0]["id"] == run_id
    assert week_summary(repository.workspace).runs == 1
    other = get_user_model().objects.create_user(username="other")
    create_workspace("Other", "other", other)
    client.force_login(other)
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.get("/api/runs").json()["count"] == 0
    assert (
        client.post(
            "/api/tasks",
            submission(repository).model_dump(),
            content_type="application/json",
        ).status_code
        == 404
    )


def test_mcp_run_scope_and_origin(repository: Repository, owner: User) -> None:
    denied = call(
        token(owner, "read"), "submit_task", **submission(repository).model_dump()
    )
    assert "cannot run jobs" in error_text(denied)
    assert not Task.objects.exists()
    secret = token(owner, "run")
    first = call(secret, "submit_task", **submission(repository).model_dump())
    assert not first.get("isError"), first
    task = Task.objects.get()
    assert task.origin == TaskOrigin.MCP and task.submitted_by == owner
    replay = call(secret, "submit_task", **submission(repository).model_dump())
    assert not replay.get("isError")
    assert Task.objects.count() == Run.objects.count() == 1
    detail = call(secret, "get_run", run_id=Run.objects.get().pk)
    assert not detail.get("isError"), detail


def test_member_cannot_submit(repository: Repository, owner: User) -> None:
    membership = WorkspaceMembership.objects.get(user=owner)
    membership.role = WorkspaceMembership.Role.MEMBER
    membership.save()
    client = Client()
    client.force_login(owner)
    response = client.post(
        "/api/tasks",
        submission(repository).model_dump(),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert not Task.objects.exists()


def test_worker_source_sandbox_output_and_retention(
    repository: Repository, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = start(repository, owner)
    prepare_source_step.__wrapped__(run.pk)
    runner = Mock()
    runner.start.return_value = "sandbox-1"
    monkeypatch.setattr(
        "secondshift.runs.orchestration.workflows.get_runner_client", lambda: runner
    )
    assert start_runner_session_step.__wrapped__(run.pk) == "sandbox-1"
    spec = runner.start.call_args.args[0]
    assert spec.commit_sha == run.commit_sha
    assert spec.repository == repository.full_name
    output = store_run_text(run, "Review complete.")
    start_run(run.pk, run.dbos_workflow_id)
    finish_run_step.__wrapped__(
        run.pk,
        {
            "succeeded": True,
            "exit_code": 0,
            "summary": "Review complete.",
            "output": output.as_dict(),
        },
    )
    run.refresh_from_db()
    assert (
        run.status == Run.Status.SUCCEEDED and run.output_excerpt == "Review complete."
    )
    assert run_report_payload(run)["repository"]["full_name"] == repository.full_name
    client = Client()
    client.force_login(owner)
    assert client.get(f"/api/runs/{run.pk}/files/output").status_code == 200
    Run.objects.filter(pk=run.pk).update(
        finished_at=timezone.now() - timedelta(days=90)
    )
    assert prune_runs(30) == 1
    assert not Task.objects.exists()


def test_gateway_and_subscription_use_task_workspace(
    repository: Repository, owner: User, settings: Any
) -> None:
    run = start(repository, owner)
    model = repository.workspace.default_model_connection
    secret = issue_run_token(run, model)
    assert authenticate(secret).run_id == run.pk
    assert async_to_sync(aauthenticate)(secret).run_id == run.pk
    settings.AGENT_IMAGE = "server-owned-agent"
    settings.AGENT_BROKER_URL = "http://gateway/agent/mcp"
    login = AgentConnection.objects.create(
        workspace=repository.workspace,
        owner=owner,
        backend="claude_code",
        display_name="Personal",
        auth_data="never-in-plan",
    )
    choose_execution(repository.workspace, login=login)
    Run.objects.filter(pk=run.pk).update(status=Run.Status.SUCCEEDED)
    second = start(repository, owner, idempotency_key="subscription")
    assert run_connection(second).pk == login.pk
    assert "never-in-plan" not in str(second.execution_plan)


def test_history_cannot_cross_ad_hoc_tasks(repository: Repository, owner: User) -> None:
    first = start(repository, owner)
    Run.objects.filter(pk=first.pk).update(status=Run.Status.SUCCEEDED)
    second = start(repository, owner, idempotency_key="next")
    second.input_snapshot["history_run_ids"] = [first.pk]
    assert previous_reports(second) == ""


def test_task_source_constraints(repository: Repository, owner: User) -> None:
    run = start(repository, owner)
    with pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.create(commit_sha=run.commit_sha, scheduled_for=timezone.now())


def test_external_tools_and_notifications_recheck_consent(
    repository: Repository, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Connection.objects.create(
        workspace=repository.workspace,
        kind=Connection.Kind.API,
        provider=Connection.Provider.API,
        binding_key="billing",
        display_name="Billing",
        access_token="server-secret",
        endpoint_url="https://billing.example.com",
        available_tools=["listCharges"],
        metadata={
            "base_url": "https://billing.example.com",
            "tools": {
                "listCharges": {
                    "description": "Read charges",
                    "inputSchema": {"type": "object"},
                }
            },
        },
    )
    delivery = Connection.objects.create(
        workspace=repository.workspace,
        kind=Connection.Kind.NOTIFICATION,
        provider=Connection.Provider.WEBHOOK,
        binding_key="alerts",
        display_name="Alerts",
    )
    run = start(
        repository,
        owner,
        definition={
            "name": "Billing review",
            "skill": SKILL,
            "requires": {"apis": [{"binding": "billing", "tools": ["listCharges"]}]},
            "notify": [{"binding": "alerts"}],
        },
    )
    from_name = next(
        name for name in run.execution_plan["agent"]["tools"] if "listCharges" in name
    )
    assert broker_connection(run, from_name).connection.pk == api.pk
    assert notification_targets(run) == [delivery.pk]
    api.require_approval = True
    api.save()
    with pytest.raises(ExternalToolError, match="no longer approved"):
        broker_connection(run, from_name)
    delivery.require_approval = True
    delivery.save()
    assert notification_targets(run) == []
    deliver = Mock()
    monkeypatch.setattr(
        "secondshift.runs.orchestration.workflows.deliver_webhook", deliver
    )
    deliver_run_notification_step.__wrapped__(run.pk, delivery.pk)
    deliver.assert_not_called()


def test_queued_ad_hoc_run_counts_against_subscription_cap(
    repository: Repository, owner: User, settings: Any
) -> None:
    settings.AGENT_IMAGE = "server-owned-agent"
    settings.AGENT_BROKER_URL = "http://gateway/agent/mcp"
    login = AgentConnection.objects.create(
        workspace=repository.workspace,
        owner=owner,
        backend="claude_code",
        display_name="Personal",
        auth_data="never-in-plan",
        max_runs_per_day=1,
    )
    choose_execution(repository.workspace, login=login)
    run = start(repository, owner)
    Run.objects.filter(pk=run.pk).update(status=Run.Status.SUCCEEDED)
    with pytest.raises(RunRefused, match="limit"):
        start(repository, owner, idempotency_key="over-cap")
    assert Task.objects.count() == 1


def test_ad_hoc_usage_is_counted_in_workspace_totals(
    repository: Repository, owner: User
) -> None:
    run = start(repository, owner)
    ModelUsage.objects.create(
        run=run,
        connection=repository.workspace.default_model_connection,
        provider="anthropic",
        model="test-model",
        status_code=200,
        input_tokens=100,
        output_tokens=10,
        cost_usd="0.125",
    )
    assert float(week_summary(repository.workspace).spend_usd) == 0.125


@pytest.mark.parametrize("field", ["image", "connection_id", "endpoint", "limits"])
def test_http_submission_refuses_policy_overrides(
    repository: Repository, owner: User, field: str
) -> None:
    payload = submission(repository).model_dump()
    payload["definition"][field] = "client-controlled"
    client = Client()
    client.force_login(owner)
    assert (
        client.post("/api/tasks", payload, content_type="application/json").status_code
        == 422
    )
    assert not Task.objects.exists()


def test_pre_submission_models_can_still_create_job_tasks(
    repository: Repository, owner: User
) -> None:
    job = Job.objects.create(
        workspace=repository.workspace,
        repository=repository,
        key="legacy",
        name="Legacy",
        skill_path=SKILL,
        config_hash="legacy-config",
    )
    apps = (
        MigrationExecutor(database)
        .loader.project_state([("runs", "0005_backfill_tasks")])
        .apps
    )
    legacy_task = apps.get_model("runs", "Task").objects.create(
        job_id=job.pk,
        submitted_by_id=owner.pk,
        origin="manual",
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
    )
    legacy_run = apps.get_model("runs", "Run").objects.create(
        job_id=job.pk,
        task_id=legacy_task.pk,
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
    )
    assert Task.objects.get(pk=legacy_task.pk).submission_key == ""
    assert Run.objects.get(pk=legacy_run.pk).repository == repository


def test_written_task_needs_no_skill_file_and_keeps_original_instructions(
    repository: Repository, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(repository.local_path)
    git(root, "rm", SKILL)
    git(root, "commit", "-m", "No repository skill needed")
    instructions = (
        "# Review setup\n\n- Check installation instructions.\n- Write a report."
    )
    run = start(
        repository, owner, definition={"name": "Review setup"}, input=instructions
    )
    assert run.commit_sha == git(root, "rev-parse", "HEAD")
    assert run.input_snapshot["skill_path"] is None
    assert run.execution_plan["job"]["skill_path"] == ""
    assert run.execution_plan["ready"] is True
    assert instructions in run_prompt(run)
    assert "Read  with" not in run_prompt(run)
    runner = Mock(
        side_effect=AssertionError("Written tasks must not read a skill file")
    )
    monkeypatch.setattr("secondshift.runs.execution.run.get_runner_client", runner)
    capture_run_input_step.__wrapped__(run.pk, "no-sandbox-read")
    runner.assert_not_called()
    run.refresh_from_db()
    with artifact_storage().open(run.input_path) as stored:
        text = stored.read().decode()
    assert instructions in text
    assert "## Skill:" not in text
    assert "could not read this file" not in text
    assert not run.events.filter(level="warning").exists()


@pytest.mark.parametrize("instructions", ["", "  ", "\n\t"])
def test_written_task_requires_nonblank_instructions(
    repository: Repository, instructions: str
) -> None:
    with pytest.raises(ValidationError, match="Write task instructions"):
        submission(
            repository, definition={"name": "Missing instructions"}, input=instructions
        )


def test_http_and_mcp_accept_written_tasks(repository: Repository, owner: User) -> None:
    payload = submission(
        repository, definition={"name": "Written review"}, input="Review the README."
    )
    client = Client()
    client.force_login(owner)
    response = client.post(
        "/api/tasks", payload.model_dump(), content_type="application/json"
    )
    assert response.status_code == 200, response.content
    first = Run.objects.get()
    assert first.input_snapshot["skill_path"] is None
    detail = client.get(f"/api/runs/{first.pk}").json()
    assert (
        detail["skill_path"] == ""
        and detail["submission_input"] == "Review the README."
    )
    replay = call(token(owner, "run"), "submit_task", **payload.model_dump())
    assert not replay.get("isError"), replay
    assert Task.objects.count() == Run.objects.count() == 1


def test_repository_jobs_still_require_a_skill_file() -> None:
    with pytest.raises(ValidationError):
        JobManifest.model_validate({"name": "A scheduled job"})


def test_written_task_uses_same_capability_approval_boundary(
    repository: Repository, owner: User
) -> None:
    with pytest.raises(RunRefused, match="approval"):
        start(
            repository,
            owner,
            definition={
                "name": "Read billing",
                "requires": {
                    "apis": [{"binding": "billing", "tools": ["listCharges"]}]
                },
            },
            input="Review recent charges.",
        )
    assert not Task.objects.exists()


@pytest.fixture
def github_repository(
    repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> Repository:
    """The local test project, treated as a GitHub repository that allows drafts."""
    pinned = git(Path(repository.local_path), "rev-parse", "HEAD")
    monkeypatch.setattr(
        "secondshift.runs.submissions.skill_commit", lambda *args: pinned
    )
    Repository.objects.filter(pk=repository.pk).update(
        source_type=Repository.SourceType.GITHUB, allow_pull_requests=True
    )
    repository.refresh_from_db()
    return repository


def test_a_pull_request_task_collects_changes_and_waits_to_publish(
    github_repository: Repository, owner: User
) -> None:
    run = start(github_repository, owner, pull_request=True)

    assert run.execution_plan["pull_request"] == {"base": "main", "run_ci": False}
    assert run.execution_plan["runner"]["collect_changes"] is True
    [delivery] = run.deliveries.all()
    assert delivery.kind == RunDelivery.Kind.PULL_REQUEST
    assert delivery.status == RunDelivery.Status.PENDING
    assert delivery.target == "local/project into main"
    assert PULL_REQUEST_NOTE in run_prompt(run)


def test_the_draft_targets_the_requested_branch(
    github_repository: Repository, owner: User
) -> None:
    run = start(github_repository, owner, pull_request=True, revision="release")

    assert run.execution_plan["pull_request"]["base"] == "release"


def test_a_task_at_a_commit_targets_the_followed_branch(
    github_repository: Repository, owner: User
) -> None:
    pinned = git(Path(github_repository.local_path), "rev-parse", "HEAD")

    run = start(github_repository, owner, pull_request=True, revision=pinned)

    assert run.execution_plan["pull_request"]["base"] == "main"


def test_other_tasks_neither_record_changes_nor_mention_pull_requests(
    github_repository: Repository, owner: User
) -> None:
    run = start(github_repository, owner)

    assert run.execution_plan["pull_request"] is None
    assert run.execution_plan["runner"]["collect_changes"] is False
    assert not run.deliveries.exists()
    assert PULL_REQUEST_NOTE not in run_prompt(run)


@pytest.mark.parametrize(
    "change",
    [{"allow_pull_requests": False}, {"source_type": Repository.SourceType.LOCAL}],
)
def test_pull_requests_need_a_github_repository_that_allows_them(
    github_repository: Repository, owner: User, change: dict[str, object]
) -> None:
    Repository.objects.filter(pk=github_repository.pk).update(**change)
    github_repository.refresh_from_db()

    with pytest.raises(RunRefused, match="does not allow"):
        start(github_repository, owner, pull_request=True)

    assert not Task.objects.exists() and not Run.objects.exists()


def test_asking_for_a_pull_request_is_part_of_the_submission(
    github_repository: Repository, owner: User
) -> None:
    start(github_repository, owner)

    with pytest.raises(RunRefused, match="different submission"):
        start(github_repository, owner, pull_request=True)


def test_a_task_definition_cannot_ask_for_a_pull_request(
    repository: Repository,
) -> None:
    with pytest.raises(
        ValidationError, match="not open_pull_request in its definition"
    ):
        submission(
            repository,
            definition={"name": "Review", "skill": SKILL, "open_pull_request": True},
        )


def test_a_task_cannot_open_issues(repository: Repository) -> None:
    with pytest.raises(ValidationError, match="Only saved jobs can open issues"):
        submission(
            repository,
            definition={"name": "Review", "skill": SKILL, "open_issues": True},
        )
