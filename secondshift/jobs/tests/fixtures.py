from collections.abc import Iterator

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from secondshift.connections.models import AgentConnection
from secondshift.jobs import environments
from secondshift.jobs.models import Job
from secondshift.jobs.planner import build_execution_plan
from secondshift.repositories.models import Repository
from secondshift.runner_protocol.environments import DEFAULT_ENVIRONMENT
from secondshift.runs.constants import RunStart
from secondshift.runs.dao import create_task
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def runner_environments(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The control plane asks the runner for its environments. In tests the
    runner's settings answer, so a test changes them with settings.
    """
    monkeypatch.setattr(
        environments,
        "fetch_environments",
        lambda: {
            DEFAULT_ENVIRONMENT: settings.RUNNER_IMAGE,
            **settings.RUNNER_ENVIRONMENTS,
        },
    )
    environments.forget_runner_environments()
    yield
    environments.forget_runner_environments()


@pytest.fixture
def agent_run(
    agent_login: tuple[Workspace, AgentConnection, User],
) -> tuple[Run, User]:
    """A running job whose workspace default is a Claude Code login."""
    workspace, login, owner = agent_login
    workspace.default_agent_connection = login
    workspace.save(update_fields=["default_agent_connection"])
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/agents", config_sha="a" * 40
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path="SKILL.md",
        config_hash="abc",
    )
    run = Run.objects.create(
        task=create_task(repository=job.repository),
        started_by=RunStart.SCHEDULE,
        job=job,
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
        started_at=timezone.now(),
        status=Run.Status.RUNNING,
        execution_plan=build_execution_plan(job).stored(),
    )
    return run, owner
