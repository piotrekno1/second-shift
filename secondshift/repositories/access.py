from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404

from secondshift.repositories.dao import active_repositories
from secondshift.repositories.models import Repository
from secondshift.workspaces.access import current_workspace


def repository_for_request(request: HttpRequest, repository_id: int) -> Repository:
    workspace = current_workspace(request)
    if workspace is None:
        raise Http404
    return get_object_or_404(active_repositories(workspace.pk), pk=repository_id)
