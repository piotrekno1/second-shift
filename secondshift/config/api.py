"""Compose the public API from routers owned by each Django app."""

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI

from secondshift.workspaces.access import AccessDenied

api = NinjaAPI(title="SecondShift API", version="1.0")
for app in (
    "core",
    "jobs",
    "runs",
    "triggers",
    "workspaces",
    "repositories",
    "connections",
):
    api.add_router("", f"secondshift.{app}.api.router")


@api.exception_handler(AccessDenied)
def access_denied(request: HttpRequest, error: AccessDenied) -> HttpResponse:
    return api.create_response(request, {"detail": str(error)}, status=403)
