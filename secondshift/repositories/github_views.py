"""Browser authorization verifies installations with the authorizing user's token."""

import secrets
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET

from secondshift.connections.github import link_installations
from secondshift.github import installations
from secondshift.github.app import configured
from secondshift.github.errors import GitHubError
from secondshift.workspaces.access import AccessDenied, Action, require

STATE_KEY = "secondshift.github_installation"


@login_required
@require_GET
def github_start(request: HttpRequest) -> HttpResponse:
    try:
        workspace = require(request, Action.ADD_GITHUB_REPOSITORIES)
    except AccessDenied as error:
        raise PermissionDenied(str(error)) from error
    if not configured():
        return redirect("/repositories?add=github")
    state = secrets.token_urlsafe(32)
    request.session[STATE_KEY] = {
        "state": state,
        "workspace": workspace.pk,
        "time": time.time(),
    }
    if request.GET.get("install"):
        return redirect(installations.installation_url(state))
    return redirect(installations.authorization_url(state, callback_url()))


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
        return redirect(installations.authorization_url(state, callback_url()))
    request.session.pop(STATE_KEY, None)
    try:
        token = installations.user_token(code, callback_url())
        if not link_installations(workspace, installations.user_installations(token)):
            state = secrets.token_urlsafe(32)
            request.session[STATE_KEY] = {
                "state": state,
                "workspace": workspace.pk,
                "time": time.time(),
            }
            return redirect(installations.installation_url(state))
    except GitHubError as error:
        return HttpResponseBadRequest(str(error))
    return redirect("/repositories?github=connected")


def callback_url() -> str:
    return settings.FRONTEND_URL.rstrip("/") + reverse("github-callback")
