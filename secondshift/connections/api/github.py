"""GitHub source connections reached with a personal access token."""

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.api.shared import owned_connection
from secondshift.connections.dao import github_token_sources
from secondshift.connections.github import (
    connect_token_source,
    is_github_source,
    set_attribution,
)
from secondshift.connections.schemas import (
    AttributionSchema,
    ConnectionSchema,
    GitHubTokenConnectSchema,
)
from secondshift.connections.serialization import serialize_connection
from secondshift.github.errors import GitHubError
from secondshift.repositories.github.check import recheck_new_token
from secondshift.workspaces.access import Action, require

router = Router(auth=django_auth, tags=["connections"])


@router.post("/connections/github-token", response=ConnectionSchema, auth=django_auth)
def connect_github_token(
    request: HttpRequest, payload: GitHubTokenConnectSchema
) -> ConnectionSchema:
    """Reach repositories with a personal access token instead of the App."""
    workspace = require(request, Action.ADD_GITHUB_REPOSITORIES)
    replacing = None
    if payload.connection_id is not None:
        replacing = get_object_or_404(
            github_token_sources(workspace.pk), pk=payload.connection_id
        )
    try:
        connection = connect_token_source(workspace, payload.token, replacing)
    except GitHubError as error:
        raise HttpError(422, str(error)) from error
    if replacing is not None:
        recheck_new_token(connection)
    return serialize_connection(connection)


@router.post(
    "/connections/{connection_id}/attribution",
    response=ConnectionSchema,
    auth=django_auth,
)
def update_attribution(
    request: HttpRequest, connection_id: int, payload: AttributionSchema
) -> ConnectionSchema:
    """Whether pull requests, issues and commits through this source name SecondShift."""
    connection = owned_connection(request, connection_id, Action.EDIT_CONNECTIONS)
    if not is_github_source(connection):
        raise HttpError(404, "Not Found")
    return serialize_connection(set_attribution(connection, payload.attribution))
