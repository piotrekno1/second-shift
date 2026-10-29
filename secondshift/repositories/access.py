from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from secondshift.repositories.models import Repository
from secondshift.workspaces.access import current_workspace


def repository_for_request(request: HttpRequest, repository_id: int) -> Repository:
    return get_object_or_404(
        Repository,
        pk=repository_id,
        workspace=current_workspace(request),
        removed_at__isnull=True,
    )
