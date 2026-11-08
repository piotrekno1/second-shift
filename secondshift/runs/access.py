from django.http import Http404, HttpRequest

from secondshift.runs.dao import find_workspace_run
from secondshift.runs.models import Run
from secondshift.workspaces.access import current_workspace


def run_for_request(request: HttpRequest, run_id: int) -> Run:
    workspace = current_workspace(request)
    run = find_workspace_run(workspace.pk if workspace else None, run_id)
    if run is None:
        raise Http404
    return run
