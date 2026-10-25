from pathlib import Path

from django.contrib.auth import get_user_model

from secondshift.automations.models import JobGrant
from secondshift.automations.scheduling import desired_schedules
from secondshift.automations.sync import sync_manifest
from secondshift.integrations.models import Connection
from secondshift.repositories.models import Repository
from secondshift.workspaces.services import create_workspace


def write_manifest(path: Path, tools: list[str]) -> None:
    rendered_tools = ", ".join(tools)
    path.write_text(
        f"""
version: 1
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
    requires:
      mcps:
        - binding: observability
          tools: [{rendered_tools}]
""".strip()
    )


def test_expanding_tools_returns_existing_grant_to_pending(db, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="example/project",
    )
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.MCP,
        kind=Connection.Kind.MCP,
        binding_key="observability",
        display_name="Grafana",
    )
    path = tmp_path / ".secondshift.yaml"
    write_manifest(path, ["query_metrics"])
    job = sync_manifest(repository, path)[0]
    request = job.capability_requests.get()
    grant = JobGrant.objects.get(capability_request=request, connection=connection)
    grant.status = JobGrant.Status.APPROVED
    grant.save(update_fields=["status"])

    write_manifest(path, ["query_metrics", "search_dashboards"])
    sync_manifest(repository, path)

    grant.refresh_from_db()
    assert grant.status == JobGrant.Status.PENDING
    assert grant.allowed_tools == ["query_metrics", "search_dashboards"]


def test_job_without_a_schedule_is_manual_only(db, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", enabled=True
    )
    path = tmp_path / ".secondshift.yaml"
    path.write_text(
        """
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
""".strip()
    )
    job = sync_manifest(repository, path)[0]
    job.enabled = True
    job.save(update_fields=["enabled"])
    assert job.next_run_at is not None

    path.write_text(
        """
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
""".strip()
    )
    sync_manifest(repository, path)

    job.refresh_from_db()
    assert job.cron == ""
    assert job.next_run_at is None
    assert job.enabled is False
    assert desired_schedules() == []
