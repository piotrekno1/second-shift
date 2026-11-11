"""Run again for one-off tasks."""

from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from secondshift.config.constants import RunStart
from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.repositories.tests.test_external_skills import PATH, REPOSITORY
from secondshift.repositories.tests.test_external_skills import (
    vendor as vendor_fixture,
)
from secondshift.runs.models import Run, Task
from secondshift.runs.submissions import SkillSubmission, submit_skill
from secondshift.runs.tests.test_submissions import SKILL
from secondshift.runs.tests.test_submissions import owner as owner_fixture
from secondshift.runs.tests.test_submissions import queued as queued_fixture
from secondshift.runs.tests.test_submissions import (
    repository as repository_fixture,
)

pytestmark = pytest.mark.django_db(transaction=True)
owner = owner_fixture
queued = queued_fixture
repository = repository_fixture
vendor = vendor_fixture


def submit(repository: Repository, owner: User, definition: dict[str, Any]) -> Run:
    submission = SkillSubmission.model_validate(
        {
            "repository_id": repository.pk,
            "definition": definition,
            "input": "Focus on the install steps.",
            "revision": "main",
            "idempotency_key": "first",
        }
    )
    return submit_skill(repository, submission, owner, RunStart.MANUAL).run


def again(owner: User, run: Run, key: str = "again-1") -> Any:
    client = Client()
    client.force_login(owner)
    return client.post(
        f"/api/runs/{run.pk}/again",
        {"idempotency_key": key},
        content_type="application/json",
    )


def test_a_task_runs_again_as_it_was_submitted(
    repository: Repository, owner: User
) -> None:
    first = submit(repository, owner, {"name": "Review", "skill": SKILL})
    Run.objects.filter(pk=first.pk).update(status=Run.Status.FAILED)

    response = again(owner, first)

    assert response.status_code == 200, response.content
    second = Run.objects.get(pk=response.json()["run"]["id"])
    assert second.pk != first.pk and second.task_id != first.task_id
    assert second.inputs.definition == first.inputs.definition
    assert second.inputs.submission_input == "Focus on the install steps."
    assert second.inputs.requested_revision == "main"
    replay = again(owner, first)
    assert replay.json()["run"]["id"] == second.pk
    assert replay.json()["started"] is False
    assert Task.objects.count() == 2


def test_a_skill_from_another_repository_runs_again(
    repository: Repository, owner: User, vendor: Path
) -> None:
    source = {"repository": REPOSITORY, "ref": "main"}
    first = submit(
        repository,
        owner,
        {"name": "Triage", "skill": PATH, "skill_source": source},
    )

    response = again(owner, first)

    assert response.status_code == 200, response.content
    second = Run.objects.get(pk=response.json()["run"]["id"])
    assert second.plan.job.skill_source == first.plan.job.skill_source


def test_a_job_run_is_started_from_the_job(repository: Repository, owner: User) -> None:
    job = Job.objects.create(
        workspace=repository.workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=SKILL,
        config_hash="hash",
    )
    run = submit(repository, owner, {"name": "Review", "skill": SKILL})
    Run.objects.filter(pk=run.pk).update(job=job)

    response = again(owner, run)

    assert response.status_code == 409
    assert "from the job" in response.json()["detail"]
