"""Gateway stream metering. Every streamed model response passes through it."""

import json

from pytest_benchmark.fixture import BenchmarkFixture

from secondshift.gateway.metering import StreamMeter


def sse(event: dict[str, object]) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


def anthropic_stream(deltas: int, delta_text: str = "token ") -> bytes:
    events = [
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 1200, "output_tokens": 1}},
        },
        *(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": delta_text},
            }
            for _ in range(deltas)
        ),
        {"type": "message_delta", "usage": {"output_tokens": deltas}},
        {"type": "message_stop"},
    ]
    return b"".join(sse(event) for event in events)


def openai_stream(chunks: int) -> bytes:
    events = [
        *(
            {"choices": [{"index": 0, "delta": {"content": "token "}}]}
            for _ in range(chunks)
        ),
        {
            "choices": [],
            "usage": {"prompt_tokens": 1200, "completion_tokens": chunks},
        },
    ]
    lines = [f"data: {json.dumps(event)}\n\n".encode() for event in events]
    return b"".join([*lines, b"data: [DONE]\n\n"])


def split(body: bytes, size: int) -> list[bytes]:
    return [body[start : start + size] for start in range(0, len(body), size)]


def meter(family: str, chunks: list[bytes]) -> StreamMeter:
    meter = StreamMeter(family)
    for chunk in chunks:
        meter.feed(chunk)
    return meter


def test_anthropic_stream(benchmark: BenchmarkFixture) -> None:
    """A 2,000 token answer, read in network-sized chunks."""
    chunks = split(anthropic_stream(2000), 4096)

    result = benchmark(meter, "anthropic", chunks)

    assert result.usage.output_tokens == 2000


def test_anthropic_stream_small_chunks(benchmark: BenchmarkFixture) -> None:
    chunks = split(anthropic_stream(2000), 64)

    result = benchmark(meter, "anthropic", chunks)

    assert result.usage.output_tokens == 2000


def test_openai_stream(benchmark: BenchmarkFixture) -> None:
    chunks = split(openai_stream(2000), 4096)

    result = benchmark(meter, "openai", chunks)

    assert result.usage.output_tokens == 2000


def test_long_line_in_small_chunks(benchmark: BenchmarkFixture) -> None:
    """One 256 KB event, such as a large tool call input, split into 64 byte reads.

    The meter buffers a partial line until its newline arrives. This shows
    whether that buffering stays linear in the line's length.
    """
    chunks = split(anthropic_stream(1, delta_text="x" * 256 * 1024), 64)

    result = benchmark(meter, "anthropic", chunks)

    assert result.usage.output_tokens == 1
