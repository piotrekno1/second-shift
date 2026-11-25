"""The counts the agent image sends while the CLI works."""

import json
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from secondshift.agent.image import agent
from secondshift.agent.image.claude import events as claude_events
from secondshift.agent.image.progress import PROGRESS_METHOD, Progress, snapshot


class Broker:
    """A broker that records what it is sent, and can be slow to answer."""

    def __init__(self, delay: float = 0) -> None:
        self.received: list[tuple[str, dict[str, Any]]] = []
        self.delay = delay
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                broker.received.append((self.headers["Authorization"], body))
                time.sleep(broker.delay)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"jsonrpc": "2.0", "id": 1, "result": {}}')

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/gateway/agent/mcp"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def broker() -> Iterator[Broker]:
    found = Broker()
    yield found
    found.server.shutdown()


def wait_for(condition: Any, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_progress_sends_the_latest_counts_once(broker: Broker) -> None:
    progress = Progress(broker.url, "broker-token", interval=0.05)
    progress.update(snapshot(1, {"output_tokens": 5}, []))
    progress.update(snapshot(2, {"output_tokens": 9}, ["claude-opus-5-5"]))
    wait_for(lambda: broker.received)
    time.sleep(0.2)
    progress.close()

    assert len(broker.received) == 1
    token, body = broker.received[0]
    assert token == "Bearer broker-token"
    assert body["method"] == PROGRESS_METHOD
    assert body["params"] == {
        "turns": 2,
        "usage": {"output_tokens": 9},
        "models": ["claude-opus-5-5"],
    }


def test_a_slow_broker_never_holds_up_the_cli() -> None:
    slow = Broker(delay=2)
    progress = Progress(slow.url, "broker-token", interval=0.01)
    try:
        started = time.monotonic()
        for turn in range(200):
            progress.update(snapshot(turn, {"output_tokens": turn}, []))
            time.sleep(0.001)
        assert time.monotonic() - started < 1.5
    finally:
        progress.close()
        slow.server.shutdown()


def test_an_unreachable_broker_is_ignored() -> None:
    progress = Progress("http://127.0.0.1:9/gateway/agent/mcp", "t", interval=0.01)
    progress.update(snapshot(1, {}, []))
    time.sleep(0.1)
    progress.close()


def test_a_run_sends_only_counts_and_reports_its_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []

    class Recorder:
        def __init__(self, url: str, token: str) -> None:
            assert (url, token) == ("http://broker", "broker-token")

        def update(self, counts: dict[str, Any]) -> None:
            sent.append(counts)

        def close(self) -> None:
            pass

    events = [
        {
            "type": "assistant",
            "message": {
                "id": f"msg_{index}",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "secret reasoning"}],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        }
        for index in range(3)
    ] + [{"type": "result", "is_error": False, "result": "The report", "usage": {}}]
    script = f"import json\nfor event in {events!r}:\n    print(json.dumps(event))\n"
    monkeypatch.setitem(
        agent.BACKENDS,
        "claude_code",
        (
            sys.executable,
            lambda config: ([sys.executable, "-c", script], {}),
            claude_events.read,
        ),
    )
    monkeypatch.setattr(agent, "Progress", Recorder)

    finished = agent.run(
        {
            "backend": "claude_code",
            "prompt": "Audit",
            "max_turns": 10,
            "token_budget": 100_000,
            "broker_url": "http://broker",
            "broker_token": "broker-token",
        }
    )

    assert finished["turns"] == 3
    assert sent[-1] == {
        "turns": 3,
        "usage": {"input_tokens": 30, "output_tokens": 9},
        "models": ["claude-opus-5-5"],
    }
    assert "secret" not in json.dumps(sent)
    assert "report" not in json.dumps(sent).lower()


def test_a_login_check_sends_no_progress() -> None:
    assert (
        agent.progress_for(
            {"backend": "codex", "prompt": "", "max_turns": 1, "token_budget": 1}
        )
        is None
    )
