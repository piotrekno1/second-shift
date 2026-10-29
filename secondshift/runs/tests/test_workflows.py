import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from secondshift.config.constants import RunSandbox
from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.repositories.sources import SourceSnapshot
from secondshift.runner.protocol import RunnerSessionSpec
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.orchestration import workflows
from secondshift.runs.orchestration.workflows import (
    prepare_source_step,
    start_runner_session_step,
    stop_runner_session_step,
)
from secondshift.workspaces.services import create_workspace


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
        job=job,
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
        execution_plan={
            "ready": True,
            "repository": {
                "full_name": "frozen/project",
                "config_sha": "a" * 40,
            },
            "runner": {
                "image": "server-owned:test",
                "network_policy": "none",
            },
        },
    )


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

    with pytest.raises(RuntimeError, match="offline sandbox"):
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

    monkeypatch.setattr(workflows, "github_repository_metadata", removed)
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

    monkeypatch.setattr(workflows, "execute_agent_run", fail)
    monkeypatch.setattr(workflows, "notify", notified.append)

    with pytest.raises(RuntimeError):
        workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    assert notified == [run.pk]


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
