from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.repositories.schemas import RepositoryValidationSchema
from secondshift.workspaces.dao import workspace_owner
from secondshift.workspaces.models import Workspace


def valid(repository: Repository) -> RepositoryValidationSchema:
    return RepositoryValidationSchema(
        status="valid",
        message="Repository synchronized.",
        current_revision=repository.config_sha,
        synced_revision=repository.config_sha,
        needs_sync=False,
    )


def test_turning_a_repository_back_on_moves_its_next_runs_to_the_future(
    client: Client, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("secondshift.repositories.api.validate_repository", valid)
    monkeypatch.setattr(
        "secondshift.repositories.serialization.validate_repository", valid
    )
    repository = Repository.objects.create(
        workspace=workspace, full_name="acme/web", config_sha="a" * 40
    )
    stale = timezone.now() - timedelta(days=30)
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        enabled=True,
        next_run_at=stale,
        config_hash="hash",
    )
    client.force_login(workspace_owner(workspace))

    response = client.post(
        f"/api/repositories/{repository.pk}/enabled",
        {"enabled": True},
        content_type="application/json",
    )

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.next_run_at > timezone.now()
