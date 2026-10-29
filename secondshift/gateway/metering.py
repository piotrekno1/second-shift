"""Token usage read from provider responses, normalized across providers."""

import json
from dataclasses import dataclass, field
from typing import Any

from django.db.models import F

from secondshift.gateway.models import ModelUsage, RunToken
from secondshift.gateway.pricing import request_cost

MAX_SSE_LINE_BYTES = 1024 * 1024


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


def token_count(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def apply_anthropic_usage(usage: Usage, data: Any) -> None:
    # message_delta repeats cumulative counts, so later values replace earlier.
    if not isinstance(data, dict):
        return
    fields = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
        "cache_creation_input_tokens": "cache_write_tokens",
    }
    for source, target in fields.items():
        if (value := token_count(data.get(source))) is not None:
            setattr(usage, target, value)


def apply_openai_usage(usage: Usage, data: Any) -> None:
    if not isinstance(data, dict):
        return
    prompt = token_count(data.get("prompt_tokens"))
    if prompt is None:
        prompt = token_count(data.get("input_tokens"))
    completion = token_count(data.get("completion_tokens"))
    if completion is None:
        completion = token_count(data.get("output_tokens"))
    details = data.get("prompt_tokens_details") or data.get("input_tokens_details")
    cached = (
        token_count(details.get("cached_tokens")) if isinstance(details, dict) else None
    )
    cached = cached or 0
    # OpenAI counts cached tokens inside the prompt; store them separately so
    # totals match Anthropic's accounting.
    if prompt is not None:
        usage.input_tokens = max(prompt - cached, 0)
        usage.cache_read_tokens = cached
    if completion is not None:
        usage.output_tokens = completion


def usage_from_json(family: str, body: Any) -> Usage:
    usage = Usage()
    if not isinstance(body, dict):
        return usage
    if family == "anthropic":
        apply_anthropic_usage(usage, body.get("usage"))
    else:
        apply_openai_usage(usage, body.get("usage"))
    return usage


@dataclass
class StreamMeter:
    """Reads usage out of a server-sent event stream without altering it."""

    family: str
    usage: Usage = field(default_factory=Usage)
    # The partial line waiting for its newline. A bytearray grows in place, so
    # a long line read in small chunks costs time linear in its length.
    buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)

    def feed(self, chunk: bytes) -> None:
        self.buffer += chunk
        # Only the new chunk can hold the newline that ends the partial line.
        if b"\n" in chunk:
            *lines, rest = self.buffer.split(b"\n")
            self.buffer = rest
            for line in lines:
                self.read_line(bytes(line).strip())
        if len(self.buffer) > MAX_SSE_LINE_BYTES:
            self.buffer.clear()

    def read_line(self, line: bytes) -> None:
        event = sse_event(line)
        if event is None:
            return
        if self.family == "anthropic":
            apply_anthropic_event(self.usage, event)
        else:
            apply_openai_event(self.usage, event)


def sse_event(line: bytes) -> dict[str, Any] | None:
    """The JSON object in a server-sent `data:` line, or None for anything else."""
    if not line.startswith(b"data:"):
        return None
    data = line[5:].strip()
    if not data or data == b"[DONE]":
        return None
    try:
        event = json.loads(data)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def apply_anthropic_event(usage: Usage, event: dict[str, Any]) -> None:
    """Usage arrives at the message's start and in its final delta."""
    if event.get("type") == "message_start":
        message = event.get("message")
        if isinstance(message, dict):
            apply_anthropic_usage(usage, message.get("usage"))
    elif event.get("type") == "message_delta":
        apply_anthropic_usage(usage, event.get("usage"))


def apply_openai_event(usage: Usage, event: dict[str, Any]) -> None:
    """Responses report usage when complete. Chat completions in a last chunk."""
    if event.get("type") == "response.completed":
        response = event.get("response")
        if isinstance(response, dict):
            apply_openai_usage(usage, response.get("usage"))
    elif event.get("usage"):
        apply_openai_usage(usage, event["usage"])


async def record_usage(
    run_token: RunToken, family: str, model: str, status_code: int, usage: Usage
) -> None:
    await ModelUsage.objects.acreate(
        run_id=run_token.run_id,
        connection_id=run_token.connection_id,
        provider=family,
        model=model[:200],
        status_code=status_code,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cost_usd=request_cost(run_token.connection, model, usage),
    )
    if usage.total:
        await RunToken.objects.filter(pk=run_token.pk).aupdate(
            used_tokens=F("used_tokens") + usage.total
        )
