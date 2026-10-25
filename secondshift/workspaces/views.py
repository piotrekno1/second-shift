from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from secondshift.workspaces.invites import (
    INVITE_UNUSABLE_MESSAGE,
    find_invite,
    remember_invite,
)


@never_cache
@require_GET
def open_invite(request: HttpRequest, token: str) -> HttpResponse:
    """Remember the invite, then send the person to sign up or to the app.

    Opening a link never joins a workspace. The dashboard asks the signed-in
    person to confirm first.
    """
    if find_invite(token) is None:
        return render(
            request,
            "workspaces/invite_unusable.html",
            {"message": INVITE_UNUSABLE_MESSAGE},
            status=400,
        )
    remember_invite(request, token)
    if request.user.is_authenticated:
        return HttpResponseRedirect("/")
    return HttpResponseRedirect(reverse("account_signup"))
