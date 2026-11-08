from django.http import Http404, HttpRequest

from secondshift.jobs.dao import find_active_job
from secondshift.jobs.models import Job
from secondshift.workspaces.access import current_workspace


def job_for_request(request: HttpRequest, job_id: int) -> Job:
    job = find_active_job(current_workspace(request), job_id)
    if job is None:
        raise Http404
    return job
