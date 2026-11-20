import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from secondshift.jobs.models import Job
from secondshift.runs.tests.test_run_queries import make_job, make_workspace
from secondshift.workspaces.dao import workspace_owner

pytestmark = pytest.mark.django_db


def test_job_list_leaves_skill_previews_in_the_database(client: Client) -> None:
    workspace = make_workspace("team")
    job = make_job(workspace, "acme/api", "review")
    Job.objects.filter(pk=job.pk).update(skill_text="# Review\n")
    client.force_login(workspace_owner(workspace))

    with CaptureQueriesContext(connection) as queries:
        listed = client.get("/api/jobs").json()
    # The next request clears the query log, so read it first.
    statements = [query["sql"] for query in queries.captured_queries]
    detail = client.get(f"/api/jobs/{job.pk}").json()

    assert [item["name"] for item in listed] == ["Review"]
    assert statements
    assert not any('"skill_text"' in sql for sql in statements)
    assert detail["skill_text"] == "# Review\n"
