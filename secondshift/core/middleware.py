from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

LIVENESS_PATH = "/-/health/up/"


class LivenessMiddleware:
    """Answer the proxy's liveness probe before host validation and SSL redirects.

    Kamal's proxy probes the container directly over HTTP with an internal Host
    header, so this must run ahead of SecurityMiddleware and ALLOWED_HOSTS.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path == LIVENESS_PATH:
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)
