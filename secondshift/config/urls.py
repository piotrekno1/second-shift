from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, HttpRequest
from django.urls import include, path
from health_check.views import HealthCheckView

from secondshift.core.api import api
from secondshift.core.signin import sign_in
from secondshift.core.views import password_reset
from secondshift.workspaces.views import open_invite


def spa(request: HttpRequest, path: str = "") -> FileResponse:
    index = settings.WEB_DIR / "index.html"
    if not index.exists():
        raise Http404(
            "Dashboard has not been built. Run npm --prefix dashboard run build."
        )
    return FileResponse(index.open("rb"), content_type="text/html")


urlpatterns = [
    path("-/admin/", admin.site.urls),
    path("accounts/password/reset/", password_reset),
    path("accounts/", include("allauth.urls")),
    path("-/sign-in/<str:token>/", sign_in, name="sign-in"),
    path("-/invite/<str:token>/", open_invite, name="invite"),
    path("api/", api.urls),
    path(
        "-/health/",
        HealthCheckView.as_view(
            checks=["health_check.checks.Database", "health_check.checks.Cache"]
        ),
    ),
    path("", spa),
    path("<path:path>", spa),
]
