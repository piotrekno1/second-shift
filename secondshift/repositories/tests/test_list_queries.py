import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.repositories.schemas import RepositoryValidationSchema
from secondshift.runs.tests.test_run_queries import make_job, make_workspace
from secondshift.workspaces.dao import workspace_owner

pytestmark = pytest.mark.django_db

SKILL_COLUMN = '"skill_text"'


def valid(repository: Repository) -> RepositoryValidationSchema:
    return RepositoryValidationSchema(
        status="valid",
        message="Repository synchronized.",
        current_revision=repository.config_sha,
        synced_revision=repository.config_sha,
        needs_sync=False,
    )


def list_repositories(client: Client) -> tuple[list[dict], int]:
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/api/repositories")
    assert response.status_code == 200, response.content
    assert not any(SKILL_COLUMN in query["sql"] for query in queries.captured_queries)
    return response.json(), len(queries.captured_queries)


def test_repository_list_counts_jobs_without_loading_them(client: Client) -> None:
    workspace = make_workspace("team")
    make_job(workspace, "acme/api", "review")
    make_job(workspace, "acme/web", "triage")
    client.force_login(workspace_owner(workspace))
    listed, queries = list_repositories(client)

    for index in range(5):
        make_job(workspace, "acme/api", f"extra-{index}")
    Job.objects.update(skill_text="x" * 1000)
    grown, grown_queries = list_repositories(client)

    assert {repo["full_name"]: repo["job_count"] for repo in listed} == {
        "acme/api": 1,
        "acme/web": 1,
    }
    assert {repo["full_name"]: repo["job_count"] for repo in grown} == {
        "acme/api": 6,
        "acme/web": 1,
    }
    assert grown_queries == queries


def test_repository_detail_counts_jobs_without_skill_previews(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "secondshift.repositories.serialization.validate_repository", valid
    )
    workspace = make_workspace("team")
    job = make_job(workspace, "acme/api", "review")
    make_job(workspace, "acme/api", "triage")
    client.force_login(workspace_owner(workspace))

    with CaptureQueriesContext(connection) as queries:
        response = client.get(f"/api/repositories/{job.repository_id}")

    assert response.status_code == 200, response.content
    assert response.json()["job_count"] == 2
    assert not any(SKILL_COLUMN in query["sql"] for query in queries.captured_queries)
