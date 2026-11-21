"""Scheduling a tried skill by opening a pull request that adds its job."""

from collections.abc import Callable
from typing import Any

import pytest
import yaml
from django.test import Client

from secondshift.jobs.manifest import parse_manifest
from secondshift.repositories.models import Repository
from secondshift.repositories.pull_requests import (
    DraftPullRequest,
    PublishedPullRequest,
    PublishError,
)
from secondshift.runs import scheduling
from secondshift.runs.models import Run
from secondshift.runs.schemas import ScheduleSkillSchema
from secondshift.runs.serialization import serialize_run_detail
from secondshift.runs.services import RunRefused
from secondshift.runs.tests.test_workflows import execution_run
from secondshift.workspaces.dao import workspace_owner

SHA = "b" * 40
SKILL = "skills/repository-health/SKILL.md"
OPENED = PublishedPullRequest(31, "https://github.com/current/project/pull/31")
CHOICE = ScheduleSkillSchema(
    name="Repository health",
    schedule="0 9 * * 1",
    timezone="Europe/Warsaw",
    notify=["team-slack"],
)


@pytest.fixture
def run(db: object) -> Run:
    """A successful try of a skill from another repository."""
    run = execution_run(db)
    Repository.objects.filter(pk=run.repository.pk).update(
        source_type=Repository.SourceType.GITHUB,
        allow_pull_requests=True,
        default_branch="trunk",
    )
    run.job = None
    run.status = Run.Status.SUCCEEDED
    run.execution_plan["job"]["skill_path"] = SKILL
    run.execution_plan["job"]["skill_source"] = {
        "repository": "acme/skills",
        "commit_sha": SHA,
    }
    run.save()
    return Run.objects.get(pk=run.pk)


class Publisher:
    def __init__(self, existing: str | None, result: Any = OPENED) -> None:
        self.existing = existing
        self.result = result
        self.calls: list[tuple[str, str, DraftPullRequest]] = []

    def __call__(
        self,
        repository: Repository,
        path: str,
        change: Callable[[str | None], str],
        draft: DraftPullRequest,
    ) -> PublishedPullRequest:
        self.calls.append((path, change(self.existing), draft))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def publisher(
    monkeypatch: pytest.MonkeyPatch, existing: str | None = None, result: Any = OPENED
) -> Publisher:
    fake = Publisher(existing, result)
    monkeypatch.setattr(scheduling, "publish_file_change", fake)
    return fake


def test_the_draft_adds_the_job_pinned_to_the_tried_commit(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = publisher(monkeypatch)

    assert scheduling.schedule_skill(run, CHOICE) == OPENED

    [(path, text, draft)] = fake.calls
    assert path == ".secondshift.yaml"
    job = parse_manifest(text).jobs["repository-health"]
    assert job.skill == SKILL
    assert job.skill_source is not None
    assert job.skill_source.repository == "acme/skills"
    assert job.skill_source.ref == SHA
    assert job.schedule == "0 9 * * 1"
    assert job.timezone == "Europe/Warsaw"
    assert [item.binding for item in job.notify] == ["team-slack"]
    assert not job.open_pull_request
    assert draft.base == "trunk"
    assert draft.title == "Schedule Repository health"
    assert draft.branch == f"secondshift/schedule-repository-health-{run.pk}"
    assert f"SecondShift-Run: {run.pk}" in draft.message


def test_only_what_was_chosen_goes_into_the_job(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = publisher(monkeypatch)
    choice = ScheduleSkillSchema(name="Health", open_pull_request=True)

    scheduling.schedule_skill(run, choice)

    [(_, text, _)] = fake.calls
    job = yaml.safe_load(text)["jobs"]["health"]
    assert set(job) == {"name", "skill", "skill_source", "open_pull_request"}


def test_a_job_that_already_exists_is_refused_with_its_name(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = (
        "version: 1\njobs:\n  repository-health:\n    name: H\n    skill: a/SKILL.md\n"
    )
    publisher(monkeypatch, existing)

    with pytest.raises(RunRefused, match="already has a job called repository-health"):
        scheduling.schedule_skill(run, CHOICE)


def test_github_problems_are_shown_as_they_are(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher(monkeypatch, result=PublishError("GitHub refused write access."))

    with pytest.raises(RunRefused, match="GitHub refused write access"):
        scheduling.schedule_skill(run, CHOICE)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"allow_pull_requests": False}, "has not allowed pull requests"),
        ({"source_type": Repository.SourceType.LOCAL}, "has not allowed pull"),
    ],
)
def test_a_repository_that_takes_no_pull_requests_is_refused(
    run: Run, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any], reason: str
) -> None:
    fake = publisher(monkeypatch)
    Repository.objects.filter(pk=run.repository.pk).update(**change)
    run.refresh_from_db()

    with pytest.raises(RunRefused, match=reason):
        scheduling.schedule_skill(run, CHOICE)
    assert fake.calls == []


def test_only_a_successful_try_can_be_scheduled(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher(monkeypatch)
    run.status = Run.Status.FAILED
    with pytest.raises(RunRefused, match="once a run of it succeeds"):
        scheduling.schedule_skill(run, CHOICE)
    run.status = Run.Status.SUCCEEDED
    run.execution_plan["job"]["skill_source"] = None
    run.save()
    run = Run.objects.get(pk=run.pk)
    with pytest.raises(RunRefused, match="tried from GitHub"):
        scheduling.schedule_skill(run, CHOICE)


def test_the_endpoint_returns_the_draft(
    run: Run, monkeypatch: pytest.MonkeyPatch, client: Client
) -> None:
    publisher(monkeypatch)
    client.force_login(workspace_owner(run.repository.workspace))

    response = client.post(
        f"/api/runs/{run.pk}/schedule",
        CHOICE.model_dump(),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {"number": 31, "url": OPENED.url}


def test_the_run_page_offers_the_pull_request_only_when_it_can_open(run: Run) -> None:
    assert serialize_run_detail(run).schedules_by_pull_request
    Repository.objects.filter(pk=run.repository.pk).update(allow_pull_requests=False)
    run.refresh_from_db()
    assert not serialize_run_detail(run).schedules_by_pull_request
