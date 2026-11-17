import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from pydantic import ValidationError

from secondshift.jobs.models import Job
from secondshift.jobs.tests.plans import stored_plan
from secondshift.repositories.models import Repository
from secondshift.repositories.sources import SourceSnapshot
from secondshift.runner_protocol.sessions import CommandResult, RunnerSessionSpec
from secondshift.runs.constants import RunSandbox, RunStart
from secondshift.runs.dao import create_task
from secondshift.runs.models import Run, RunEvent, RunFailure
from secondshift.runs.orchestration import workflows
from secondshift.runs.orchestration.workflows import (
    prepare_source_step,
    start_runner_session_step,
    stop_runner_session_step,
)
from secondshift.workspaces.dao import create_workspace


class FakeRunnerClient:
    def __init__(self) -> None:
        self.started: list[RunnerSessionSpec] = []
        self.stopped: list[str] = []
        self.uploaded: list[tuple[str, Path]] = []

    def upload_source(self, sha256: str, path: Path) -> None:
        self.uploaded.append((sha256, path))

    def start(self, spec: RunnerSessionSpec) -> str:
        self.started.append(spec)
        return f"run-{spec.run_id}"

    def stop(self, session_id: str) -> None:
        self.stopped.append(session_id)


def execution_run(db) -> Run:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="current/project",
        config_sha="a" * 40,
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )
    return Run.objects.create(
        task=create_task(repository=job.repository),
        started_by=RunStart.SCHEDULE,
        job=job,
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
        execution_plan=stored_plan(
            repository={"full_name": "frozen/project"},
            runner={"image": "server-owned:test"},
        ),
    )


@pytest.mark.django_db
def test_only_a_plan_that_publishes_changes_records_them(db, monkeypatch) -> None:
    run = execution_run(db)
    run.execution_plan["runner"]["collect_changes"] = True
    run.save()
    client = FakeRunnerClient()
    monkeypatch.setattr(workflows, "get_runner_client", lambda: client)
    monkeypatch.setattr(workflows, "source_snapshot", lambda *args: SNAPSHOT)

    start_runner_session_step(run.pk)

    assert client.started[0].collect_changes is True


SNAPSHOT = SourceSnapshot(path=Path("/cache/a.tar.gz"), sha256="e" * 64, size_bytes=1)


@pytest.mark.django_db
def test_runner_session_uses_only_the_frozen_server_plan(db, monkeypatch) -> None:
    run = execution_run(db)
    client = FakeRunnerClient()
    monkeypatch.setattr(
        "secondshift.runs.orchestration.workflows.get_runner_client", lambda: client
    )
    snapshots = []

    def source_snapshot(repository: Repository, commit_sha: str) -> SourceSnapshot:
        snapshots.append((repository.pk, commit_sha))
        return SNAPSHOT

    monkeypatch.setattr(workflows, "source_snapshot", source_snapshot)

    session_id = start_runner_session_step(run.pk)
    stop_runner_session_step(session_id)

    assert client.started == [
        RunnerSessionSpec(
            run_id=run.pk,
            session_key=run.runner_session_key.hex,
            repository="frozen/project",
            commit_sha="a" * 40,
            image="server-owned:test",
            source_sha256="e" * 64,
        )
    ]
    assert snapshots == [(run.job.repository.pk, "a" * 40)]
    assert client.uploaded == [("e" * 64, SNAPSHOT.path)]
    assert client.stopped == [session_id]
    run.refresh_from_db()
    assert run.sandbox_runtime == RunSandbox.ISOLATED_RUNNER


@pytest.mark.django_db
def test_runner_session_rejects_a_networked_plan(db, monkeypatch) -> None:
    run = execution_run(db)
    run.execution_plan["runner"]["network_policy"] = "host"
    run.save(update_fields=["execution_plan"])
    client = FakeRunnerClient()
    monkeypatch.setattr(
        "secondshift.runs.orchestration.workflows.get_runner_client", lambda: client
    )

    with pytest.raises(ValidationError, match="network_policy"):
        start_runner_session_step(run.pk)

    assert client.started == []


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


@pytest.mark.django_db
def test_prepare_source_builds_the_runs_commit_from_a_local_repository(
    db, settings, tmp_path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.email", "test@example.com")
    git(source, "config", "user.name", "Test")
    (source / "README.md").write_text("first\n")
    git(source, "add", "README.md")
    git(source, "commit", "-m", "first")
    revision = git(source, "rev-parse", "HEAD")
    run = execution_run(db)
    Run.objects.filter(pk=run.pk).update(commit_sha=revision)
    repository = run.job.repository
    repository.source_type = Repository.SourceType.LOCAL
    repository.local_path = str(source)
    repository.save(update_fields=["source_type", "local_path"])
    settings.LOCAL_REPOSITORIES_ENABLED = True
    settings.SOURCE_CACHE_ROOT = tmp_path / "sources"

    prepare_source_step(run.pk)

    snapshot = tmp_path / "sources" / "snapshots" / str(repository.pk)
    assert [path.name for path in snapshot.iterdir()] == [f"{revision}.tar.gz"]
    assert RunEvent.objects.filter(
        run=run, message__startswith=f"Checked out the repository at {revision[:12]}"
    ).exists()


@pytest.mark.django_db
def test_prepare_source_checks_github_access_first(db, monkeypatch) -> None:
    run = execution_run(db)

    def removed(repository: Repository) -> dict:
        raise RuntimeError("This repository has been removed.")

    monkeypatch.setattr(workflows, "github_access", removed)
    monkeypatch.setattr(
        workflows, "source_snapshot", lambda *args: pytest.fail("built a snapshot")
    )

    with pytest.raises(RuntimeError, match="removed"):
        prepare_source_step(run.pk)


@pytest.mark.django_db
def test_a_failed_run_still_notifies(
    db, monkeypatch: pytest.MonkeyPatch, fake_stages: list[str]
) -> None:
    run = execution_run(db)
    notified: list[int] = []

    def fail(run: Run, session_id: str) -> None:
        raise RuntimeError("The agent returned an empty report.")

    events: list[str] = []
    monkeypatch.setattr(workflows, "execute_agent_run", fail)
    monkeypatch.setattr(workflows, "notify", notified.append)
    monkeypatch.setattr(
        workflows, "record_run_event_step", lambda run_id, text: events.append(text)
    )

    with pytest.raises(RuntimeError):
        workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    assert notified == [run.pk]
    # The timeline names the skill the run was given.
    assert events == ["Running SKILL.md."]


@pytest.mark.django_db
def test_a_notification_error_keeps_the_run_failure(
    db, monkeypatch: pytest.MonkeyPatch, fake_stages: list[str]
) -> None:
    run = execution_run(db)

    def fail(run: Run, session_id: str) -> None:
        raise RuntimeError("The agent returned an empty report.")

    def broken_notify(run_id: int) -> None:
        raise ConnectionError("database went away")

    monkeypatch.setattr(workflows, "execute_agent_run", fail)
    monkeypatch.setattr(workflows, "notify", broken_notify)

    with pytest.raises(RuntimeError, match="empty report"):
        workflows.execute_run(run.pk)


class SetupRunnerClient(FakeRunnerClient):
    def __init__(self, results: list[CommandResult]) -> None:
        super().__init__()
        self.results = results
        self.setups: list[tuple[str, int]] = []

    def setup(self, session_id: str, index: int) -> CommandResult:
        self.setups.append((session_id, index))
        return self.results[index]


def setup_run(db, commands: list[str]) -> Run:
    run = execution_run(db)
    run.execution_plan["runner"]["setup"] = commands
    run.save(update_fields=["execution_plan"])
    return run


@pytest.mark.django_db
def test_the_session_spec_carries_the_plans_setup(db, monkeypatch) -> None:
    run = setup_run(db, ["npm ci --ignore-scripts"])
    client = FakeRunnerClient()
    monkeypatch.setattr(workflows, "get_runner_client", lambda: client)
    monkeypatch.setattr(workflows, "source_snapshot", lambda *args: SNAPSHOT)

    start_runner_session_step(run.pk)

    assert client.started[0].setup == ("npm ci --ignore-scripts",)


@pytest.mark.django_db
def test_setup_runs_each_command_before_the_agent(
    db, monkeypatch: pytest.MonkeyPatch, fake_stages: list[str]
) -> None:
    run = setup_run(db, ["npm ci", "uv sync"])
    client = SetupRunnerClient(
        [CommandResult("setup-0", 0, "ok"), CommandResult("setup-1", 0, "ok")]
    )
    order: list[str] = []
    monkeypatch.setattr(workflows, "get_runner_client", lambda: client)

    def agent(run: Run, session_id: str) -> None:
        order.append(f"agent after {len(client.setups)} setup commands")
        raise RuntimeError("stop here")

    monkeypatch.setattr(workflows, "execute_agent_run", agent)
    monkeypatch.setattr(workflows, "notify", lambda run_id: None)

    with pytest.raises(RuntimeError, match="stop here"):
        workflows.execute_run(run.pk)

    assert client.setups == [(f"run-{run.pk}", 0), (f"run-{run.pk}", 1)]
    assert order == ["agent after 2 setup commands"]
    assert list(
        RunEvent.objects.filter(message__startswith="Setup ran").values_list(
            "message", flat=True
        )
    ) == [
        'Setup ran "npm ci" with access to package registries only.',
        'Setup ran "uv sync" with access to package registries only.',
    ]


@pytest.mark.django_db
def test_a_failed_setup_command_stops_the_run_before_the_agent(
    db, monkeypatch: pytest.MonkeyPatch, fake_stages: list[str]
) -> None:
    run = setup_run(db, ["npm ci", "uv sync"])
    client = SetupRunnerClient(
        [CommandResult("setup-0", 1, "npm ERR! 404 Not Found - left-pad")]
    )
    monkeypatch.setattr(workflows, "get_runner_client", lambda: client)
    monkeypatch.setattr(
        workflows, "execute_agent_run", lambda *args: pytest.fail("agent ran")
    )
    monkeypatch.setattr(workflows, "notify", lambda run_id: None)

    with pytest.raises(RuntimeError, match="exited with code 1"):
        workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    assert run.failure == RunFailure.SETUP
    assert run.failure_reason == '"npm ci" exited with code 1.'
    assert client.setups == [(f"run-{run.pk}", 0)]
    assert fake_stages == [f"run-{run.pk}"]
    event = RunEvent.objects.get(level="error", message__startswith="Setup stopped")
    assert "npm ERR! 404 Not Found - left-pad" in event.message
