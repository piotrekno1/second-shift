"""Tell SecondShift how far the CLI has got, while it works.

The agent image ships this file next to agent.py, so it may use only the
standard library. Only counts and model names leave the container: turns so
far, token usage and the models that answered. Never a prompt, a reply or a
tool's arguments.

A thread sends the latest snapshot to the run's broker, one at a time, so a
slow broker or a relay that waits for its worker never holds up reading the
CLI. Failures are ignored: the run's final result carries the same counts.
"""

import json
import threading
import urllib.request
from typing import Any

# The broker method. The control plane answers it in runs.execution.agent_broker.
PROGRESS_METHOD = "secondshift/progress"
# How often a changed snapshot is sent.
PROGRESS_SECONDS = 10.0
SEND_TIMEOUT_SECONDS = 30.0


class Progress:
    def __init__(
        self, url: str, token: str, interval: float = PROGRESS_SECONDS
    ) -> None:
        self.url = url
        self.token = token
        self.interval = interval
        self.latest: dict[str, Any] | None = None
        self.sent: dict[str, Any] | None = None
        self.sends = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def update(self, snapshot: dict[str, Any]) -> None:
        with self.lock:
            self.latest = snapshot

    def loop(self) -> None:
        while not self.stopped.wait(self.interval):
            with self.lock:
                snapshot = self.latest
            if snapshot is None or snapshot == self.sent:
                continue
            self.send(snapshot)
            self.sent = snapshot

    def send(self, snapshot: dict[str, Any]) -> None:
        self.sends += 1
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": f"progress-{self.sends}",
                "method": PROGRESS_METHOD,
                "params": snapshot,
            }
        ).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS):
                pass
        except Exception:
            # Best effort. The final result has the same counts.
            return

    def close(self) -> None:
        """Stop without sending again. The final result has the last counts."""
        self.stopped.set()


def snapshot(
    turns: int | None, usage: dict[str, int], models: list[str]
) -> dict[str, Any]:
    return {"turns": turns, "usage": dict(usage), "models": list(models)}
