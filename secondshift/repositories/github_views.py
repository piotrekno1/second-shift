"""Browser authorization verifies installations with the authorizing user's token."""

import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from secondshift.connections.models import Connection
from secondshift.repositories.github_app import (
    API_URL,
    OAUTH_URL,
    STATE_KEY,
    GitHubAppError,
    configured,
    request_json,
)
from secondshift.workspaces.access import AccessDenied, Action, require
from secondshift.workspaces.models import Workspace


@login_required
@require_GET
def github_start(request: HttpRequest) -> HttpResponse:
    try:
        workspace = require(request, Action.ADD_GITHUB_REPOSITORIES)
    except AccessDenied as error:
        raise PermissionDenied(str(error)) from error
    if not configured():
        return HttpResponseBadRequest("GitHub App is not configured on this server.")
    state = secrets.token_urlsafe(32)
    request.session[STATE_KEY] = {
        "state": state,
        "workspace": workspace.pk,
        "time": time.time(),
    }
    if request.GET.get("install"):
        return redirect(installation_url(state))
    return redirect(authorization_url(state))


@login_required
@require_GET
def github_callback(request: HttpRequest) -> HttpResponse:
    try:
        workspace = require(request, Action.ADD_GITHUB_REPOSITORIES)
    except AccessDenied as error:
        raise PermissionDenied(str(error)) from error
    pending = request.session.get(STATE_KEY, {})
    state = request.GET.get("state", "")
    if (
        not state
        or not secrets.compare_digest(state, pending.get("state", ""))
        or pending.get("workspace") != workspace.pk
        or time.time() - pending.get("time", 0) > 600
    ):
        return HttpResponseBadRequest(
            "GitHub authorization expired or does not match this workspace."
        )
    if request.GET.get("setup_action") == "request":
        request.session.pop(STATE_KEY, None)
        return HttpResponseBadRequest(
            "Your organization must approve the GitHub App installation. Connect again after approval."
        )
    if request.GET.get("error"):
        request.session.pop(STATE_KEY, None)
        return HttpResponseBadRequest("GitHub authorization was cancelled.")
    code = request.GET.get("code", "")
    if not code:
        # Installations that already exist still require user authorization.
        return redirect(authorization_url(state))
    request.session.pop(STATE_KEY, None)
    try:
        data = request_json(
            "POST",
            f"{OAUTH_URL}/access_token",
            json={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.FRONTEND_URL.rstrip("/")
                + "/-/github/callback/",
            },
        )
        token = data.get("access_token")
        if not token:
            raise GitHubAppError("GitHub did not authorize this user.")
        if not link_installations(workspace, token):
            state = secrets.token_urlsafe(32)
            request.session[STATE_KEY] = {
                "state": state,
                "workspace": workspace.pk,
                "time": time.time(),
            }
            return redirect(installation_url(state))
    except GitHubAppError as error:
        return HttpResponseBadRequest(str(error))
    return redirect("/repositories?github=connected")


def link_installations(workspace: Workspace, token: str) -> int:
    installations = []
    for page in range(1, 101):
        batch = request_json(
            "GET", f"{API_URL}/user/installations?per_page=100&page={page}", token
        ).get("installations", [])
        installations.extend(batch)
        if len(batch) < 100:
            break
    linked = 0
    for installation in installations:
        if str(installation.get("app_id")) != str(settings.GITHUB_APP_ID):
            continue
        linked += 1
        installation_id = str(installation["id"])
        Connection.objects.update_or_create(
            workspace=workspace,
            provider=Connection.Provider.GITHUB,
            external_account_id=installation_id,
            defaults={
                "kind": Connection.Kind.SOURCE,
                "auth_strategy": Connection.AuthStrategy.MANAGED,
                "display_name": f"GitHub · {installation['account']['login']}",
                "status": Connection.Status.CONNECTED,
            },
        )
    return linked


def authorization_url(state: str) -> str:
    return f"{OAUTH_URL}/authorize?" + urlencode(
        {
            "client_id": settings.GITHUB_APP_CLIENT_ID,
            "state": state,
            "redirect_uri": settings.FRONTEND_URL.rstrip("/") + "/-/github/callback/",
        }
    )


def installation_url(state: str) -> str:
    return (
        f"https://github.com/apps/{settings.GITHUB_APP_SLUG}/installations/new?"
        + urlencode({"state": state})
    )
