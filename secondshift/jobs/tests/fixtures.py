import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from secondshift.connections.models import AgentConnection
from secondshift.jobs.models import Job
from secondshift.jobs.planner import build_execution_plan
from secondshift.repositories.models import Repository
from secondshift.runner_protocol.environments import RunnerEnvironments
from secondshift.runs.constants import RunStart
from secondshift.runs.dao import create_task
from secondshift.runs.models import Run, Runner
from secondshift.workspaces.models import Workspace


def offer_environments(environments: RunnerEnvironments, name: str = "default") -> None:
    """Register a runner that answered with these environments, as the
    worker's check would leave it.
    """
    Runner.objects.update_or_create(
        name=name,
        defaults={
            "url": f"https://{name}:8002",
            "token": f"{name}-token".ljust(32, "x"),
            "environments": sorted(environments.images),
            "images": environments.images,
            "default_environment": environments.default,
        },
    )


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
