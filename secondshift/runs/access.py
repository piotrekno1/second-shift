from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from secondshift.gateway.usage import with_model_usage
from secondshift.runs.models import Run
from secondshift.workspaces.access import current_workspace


def run_for_request(request: HttpRequest, run_id: int) -> Run:
    return get_object_or_404(
        with_model_usage(Run.objects.select_related("job__repository")),
        pk=run_id,
        job__workspace=current_workspace(request),
    )
