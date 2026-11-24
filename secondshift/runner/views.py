"""The agent relay: the one endpoint agents reach on the runner."""

from django.http import Http404, HttpRequest, HttpResponse

from secondshift.runner.relay import (
    RelayFull,
    on_relay_port,
    submit_call,
    wait_for_answer,
)
from secondshift.runner_protocol.relay import MAX_CALL_BYTES


def agent_relay(request: HttpRequest) -> HttpResponse:
    """Take an agent's tool call and answer with the worker's answer.

    Served only on the relay port, and the runner API never is.
    """
    if not on_relay_port(request):
        raise Http404
    if request.method != "POST":
        return HttpResponse(status=405)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    # Read the stream: the runner's upload limit is far smaller than a call.
    body = request.read(MAX_CALL_BYTES + 1)
    if len(body) > MAX_CALL_BYTES:
        return HttpResponse(status=413)
    try:
        call_id = submit_call(token, body)
    except RelayFull:
        return HttpResponse(status=429)
    if call_id is None:
        return HttpResponse(status=401)
    answer = wait_for_answer(call_id)
    if answer is None:
        return HttpResponse(status=504)
    return HttpResponse(
        answer.body, status=answer.status, content_type="application/json"
    )
