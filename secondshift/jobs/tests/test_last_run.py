import pytest
from django.test import Client
from django.utils import timezone

from secondshift.runs.models import Run
from secondshift.runs.tests.test_run_queries import make_job, make_workspace
from secondshift.workspaces.services import workspace_owner


@pytest.mark.django_db
def test_jobs_list_shows_each_jobs_newest_run(client: Client) -> None:
    workspace = make_workspace("team")
    ran = make_job(workspace, "acme/api", "ran")
    make_job(workspace, "acme/api", "never")
    Run.objects.create(
        job=ran,
        status=Run.Status.FAILED,
        commit_sha="abc",
        scheduled_for=timezone.now(),
    )
    newest = Run.objects.create(
        job=ran,
        status=Run.Status.RUNNING,
        commit_sha="abc",
        scheduled_for=timezone.now(),
    )
    client.force_login(workspace_owner(workspace))

    jobs = {job["name"]: job for job in client.get("/api/jobs").json()}
    detail = client.get(f"/api/jobs/{ran.pk}").json()

    assert jobs["Ran"]["last_run"]["id"] == newest.pk
    assert jobs["Ran"]["last_run"]["status"] == "running"
    assert jobs["Never"]["last_run"] is None
    assert detail["last_run"]["id"] == newest.pk
