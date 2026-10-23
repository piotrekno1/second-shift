import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from secondshift.automations.models import Job, Run
from secondshift.automations.runner_protocol import RunnerSessionSpec
from secondshift.automations.workflows import (
    materialize_local_repository_step,
    start_runner_session_step,
    stop_runner_session_step,
)
from secondshift.core.models import Workspace
from secondshift.repositories.models import Repository


class FakeRunnerClient:
    def __init__(self) -> None:
        self.started: list[RunnerSessionSpec] = []
        self.stopped: list[str] = []

    def start(self, spec: RunnerSessionSpec) -> str:
        self.started.append(spec)
        return f"run-{spec.run_id}"

    def stop(self, session_id: str) -> None:
        self.stopped.append(session_id)


def execution_run(db) -> Run:
    user = get_user_model().objects.create_user(username="owner")
    workspace = Workspace.objects.create(name="Test", slug="test", owner=user)
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


@pytest.mark.django_db
def test_runner_session_uses_only_the_frozen_server_plan(db, monkeypatch) -> None:
    run = execution_run(db)
    client = FakeRunnerClient()
    monkeypatch.setattr(
        "secondshift.automations.workflows.get_runner_client", lambda: client
    )

    session_id = start_runner_session_step(run.pk)
    stop_runner_session_step(session_id)

    assert client.started == [
        RunnerSessionSpec(
            run_id=run.pk,
            repository="frozen/project",
            commit_sha="a" * 40,
            image="server-owned:test",
        )
    ]
    assert client.stopped == [session_id]
    run.refresh_from_db()
    assert run.sandbox_runtime == "isolated-runner"


@pytest.mark.django_db
def test_runner_session_rejects_a_networked_plan(db, monkeypatch) -> None:
    run = execution_run(db)
    run.execution_plan["runner"]["network_policy"] = "host"
    run.save(update_fields=["execution_plan"])
    client = FakeRunnerClient()
    monkeypatch.setattr(
        "secondshift.automations.workflows.get_runner_client", lambda: client
    )

    with pytest.raises(RuntimeError, match="offline sandbox"):
        start_runner_session_step(run.pk)

    assert client.started == []


@pytest.mark.django_db
def test_local_materialization_uses_frozen_name_and_revision(
    db, settings, tmp_path, monkeypatch
) -> None:
    run = execution_run(db)
    repository = run.job.repository
    repository.source_type = Repository.SourceType.LOCAL
    repository.local_path = str(tmp_path / "source")
    repository.save(update_fields=["source_type", "local_path"])
    settings.LOCAL_REPOSITORIES_ENABLED = True
    settings.LOCAL_REPOSITORY_MIRROR_ROOT = tmp_path / "runner-repositories"
    calls = []

    def materialize(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "secondshift.automations.workflows.materialize_local_repository", materialize
    )

    materialize_local_repository_step(run.pk)

    assert calls == [
        {
            "source": tmp_path / "source",
            "repository_name": "frozen/project",
            "commit_sha": "a" * 40,
            "repository_root": tmp_path / "runner-repositories",
        }
    ]
