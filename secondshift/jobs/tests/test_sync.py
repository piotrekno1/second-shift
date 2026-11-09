from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from secondshift.config.constants import MAX_RUN_ISSUES, MAX_SKILL_PREVIEW_CHARS
from secondshift.connections.models import Connection
from secondshift.jobs.manifest import parse_manifest
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.scheduling import next_occurrence
from secondshift.jobs.sync import sync_jobs
from secondshift.jobs.tests.disk import sync_manifest
from secondshift.repositories.models import Repository
from secondshift.runs.orchestration.schedules import desired_schedules
from secondshift.workspaces.dao import create_workspace


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


@pytest.mark.parametrize(
    ("schedule", "zone"),
    [("0 18 * * 1", "UTC"), ("0 9 * * 1", "America/New_York")],
)
def test_changing_the_schedule_moves_the_next_run(
    db, tmp_path: Path, schedule: str, zone: str
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", enabled=True
    )
    path = tmp_path / ".secondshift.yaml"
    manifest = """
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "{schedule}"
    timezone: {zone}
""".strip()
    path.write_text(manifest.format(schedule="0 9 * * 1", zone="UTC"))
    job = sync_manifest(repository, path)[0]
    before = job.next_run_at

    path.write_text(manifest.format(schedule=schedule, zone=zone))
    sync_manifest(repository, path)

    job.refresh_from_db()
    assert job.next_run_at == next_occurrence(job)
    assert job.next_run_at != before


def test_a_job_dropped_from_the_manifest_is_marked_removed_until_named_again(
    db, tmp_path: Path
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", enabled=True
    )
    path = tmp_path / ".secondshift.yaml"
    both = """
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
  triage:
    name: Triage
    skill: .agents/skills/triage/SKILL.md
""".strip()
    path.write_text(both)
    review = sync_manifest(repository, path)[0]
    Job.objects.filter(pk=review.pk).update(enabled=True)

    path.write_text(both.split("  triage:")[0].replace("review:", "kept:"))
    sync_manifest(repository, path)
    review.refresh_from_db()
    removed_at = review.removed_at

    assert removed_at is not None
    assert review.enabled is False
    assert desired_schedules() == []
    # A later sync keeps the time it was first missing.
    sync_manifest(repository, path)
    review.refresh_from_db()
    assert review.removed_at == removed_at

    path.write_text(both)
    sync_manifest(repository, path)
    review.refresh_from_db()
    assert review.removed_at is None
    assert review.enabled is False


def test_sync_keeps_the_skill_text_for_people_to_read(db, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project"
    )
    path = tmp_path / ".secondshift.yaml"
    write_manifest(path, ["query_metrics"])
    skill = tmp_path / ".agents/skills/review/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Review\n\nCheck the dashboards.\n")

    job = sync_manifest(repository, path)[0]
    assert job.skill_text == "# Review\n\nCheck the dashboards.\n"
    assert job.skill_truncated is False

    skill.write_text("x" * (MAX_SKILL_PREVIEW_CHARS + 1))
    sync_manifest(repository, path)
    job.refresh_from_db()
    assert len(job.skill_text) == MAX_SKILL_PREVIEW_CHARS
    assert job.skill_truncated is True


def sync_text(repository: Repository, text: str) -> list[Job]:
    return sync_jobs(repository, parse_manifest(text))


TWO_JOBS = """
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
    requires:
      mcps:
        - binding: observability
          tools: [search_dashboards, query_metrics]
    notify:
      - binding: alerts
  digest:
    name: Digest
    skill: .agents/skills/digest/SKILL.md
"""

ONE_JOB_FEWER_TOOLS = """
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
    requires:
      mcps:
        - binding: observability
          tools: [query_metrics]
"""


@pytest.fixture
def repository(db: object) -> Repository:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.MCP,
        kind=Connection.Kind.MCP,
        binding_key="observability",
        display_name="Grafana",
    )
    return Repository.objects.create(workspace=workspace, full_name="example/project")


def updated_at(repository: Repository) -> list[object]:
    jobs = Job.objects.filter(repository=repository)
    return [
        *jobs.values_list("updated_at", flat=True),
        *CapabilityRequest.objects.filter(job__in=jobs).values_list(
            "updated_at", flat=True
        ),
        *JobGrant.objects.filter(capability_request__job__in=jobs).values_list(
            "updated_at", flat=True
        ),
    ]


def test_an_unchanged_manifest_saves_no_rows(repository: Repository) -> None:
    sync_text(repository, TWO_JOBS)
    before = updated_at(repository)

    sync_text(repository, TWO_JOBS)

    assert updated_at(repository) == before


def test_removing_tools_narrows_an_approved_grant(repository: Repository) -> None:
    sync_text(repository, TWO_JOBS)
    grant = JobGrant.objects.get(capability_request__binding="observability")
    assert grant.allowed_tools == ["query_metrics", "search_dashboards"]
    grant.status = JobGrant.Status.APPROVED
    grant.save(update_fields=["status"])

    sync_text(repository, ONE_JOB_FEWER_TOOLS)

    grant.refresh_from_db()
    assert grant.status == JobGrant.Status.APPROVED
    assert grant.allowed_tools == ["query_metrics"]


def test_removed_bindings_are_deleted_and_removed_jobs_disabled(
    repository: Repository,
) -> None:
    review, digest = sync_text(repository, TWO_JOBS)
    Job.objects.filter(pk=digest.pk).update(enabled=True)

    sync_text(repository, ONE_JOB_FEWER_TOOLS)

    assert list(review.capability_requests.values_list("binding", flat=True)) == [
        "observability"
    ]
    digest.refresh_from_db()
    assert digest.enabled is False


def test_a_connection_added_later_is_granted_on_the_next_sync(
    repository: Repository,
) -> None:
    sync_text(repository, TWO_JOBS)
    assert not JobGrant.objects.filter(capability_request__binding="alerts").exists()
    Connection.objects.create(
        workspace=repository.workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        binding_key="alerts",
        display_name="Alerts",
        require_approval=True,
    )

    sync_text(repository, TWO_JOBS)

    grant = JobGrant.objects.get(capability_request__binding="alerts")
    assert grant.status == JobGrant.Status.PENDING


def test_sync_stores_the_triggers_a_job_names(db, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project"
    )
    path = tmp_path / ".secondshift.yaml"
    path.write_text(
        """
version: 1
jobs:
  triage:
    name: Triage
    skill: .agents/skills/triage/SKILL.md
    triggers: [webhook]
    webhook:
      repeat_key: groupKey
""".strip()
    )

    job = sync_manifest(repository, path)[0]

    stored = Job.objects.get(pk=job.pk)
    assert stored.triggers == ["webhook"]
    assert stored.webhook == {
        "skip_when": {},
        "repeat_key": "groupKey",
        "repeat_window": None,
    }

    path.write_text(path.read_text().split("\n    triggers:")[0])
    sync_manifest(repository, path)

    stored = Job.objects.get(pk=job.pk)
    assert (stored.triggers, stored.webhook) == ([], {})


def test_a_job_can_ask_for_draft_pull_requests(db, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", config_sha="a" * 40
    )
    path = tmp_path / ".secondshift.yaml"
    path.write_text(
        """
version: 1
jobs:
  docs:
    name: Docs drift
    skill: .agents/skills/docs/SKILL.md
    open_pull_request: true
""".strip()
    )

    job = Job.objects.get(pk=sync_manifest(repository, path)[0].pk)
    plan = build_execution_plan(job).stored()

    assert job.open_pull_request is True
    assert plan["pull_request"] == {"base": "main", "run_ci": False}
    assert plan["runner"]["collect_changes"] is True

    path.write_text(path.read_text().replace("\n    open_pull_request: true", ""))
    job = Job.objects.get(pk=sync_manifest(repository, path)[0].pk)
    assert job.open_pull_request is False
    assert build_execution_plan(job).stored()["pull_request"] is None


def test_a_job_can_ask_to_open_issues(db, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", config_sha="a" * 40
    )
    path = tmp_path / ".secondshift.yaml"
    path.write_text(
        """
version: 1
jobs:
  review:
    name: Weekly review
    skill: .agents/skills/review/SKILL.md
    open_issues: true
""".strip()
    )

    job = Job.objects.get(pk=sync_manifest(repository, path)[0].pk)
    plan = build_execution_plan(job).stored()

    assert job.open_issues == {"max": MAX_RUN_ISSUES, "labels": []}
    assert plan["issues"] == {"max": MAX_RUN_ISSUES, "labels": []}
    # Issues need no file changes, so the run does not record them.
    assert plan["runner"]["collect_changes"] is False

    path.write_text(path.read_text().replace("\n    open_issues: true", ""))
    job = Job.objects.get(pk=sync_manifest(repository, path)[0].pk)
    assert job.open_issues is None
    assert build_execution_plan(job).stored()["issues"] is None
