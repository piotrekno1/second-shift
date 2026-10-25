from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from secondshift.jobs.models import Job
from secondshift.workspaces.access import current_workspace


def job_for_request(request: HttpRequest, job_id: int) -> Job:
    return get_object_or_404(
        Job.objects.select_related("repository", "workspace").prefetch_related(
            "capability_requests__grant__connection"
        ),
        pk=job_id,
        workspace=current_workspace(request),
        repository__removed_at__isnull=True,
    )
