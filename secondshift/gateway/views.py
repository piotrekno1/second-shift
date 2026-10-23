from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from secondshift.gateway.proxy import forward


@csrf_exempt
async def model_gateway(request: HttpRequest, family: str, path: str) -> HttpResponse:
    # Authenticated by a per-run token, never a browser session, so CSRF does
    # not apply.
    return await forward(request, family, path)
