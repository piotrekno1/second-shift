"""Fixed agent launcher. No repository files or runtime options are accepted."""

import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any

MAX_EVENT_BYTES = 1024 * 1024
MAX_REPORT_CHARS = 200000
BRIDGE = ["python3", "/opt/secondshift/bridge.py"]


def private_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def command(config: dict[str, Any]) -> list[str]:
    if config["backend"] == "claude_code":
        private_json(
            Path("/tmp/mcp.json"),
            {
                "mcpServers": {
                    "secondshift": {
                        "command": BRIDGE[0],
                        "args": BRIDGE[1:],
                    }
                }
            },
        )
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = Path("/auth/claude-token").read_text()
        return [
            "claude",
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            "/tmp/mcp.json",
            "--allowedTools",
            "mcp__secondshift__*",
            "--max-turns",
            str(config["max_turns"]),
            "--setting-sources",
            "",
            "--no-session-persistence",
        ]
    # No repository, user config, hooks, plugins, or inherited MCP configuration.
    # Only auth.json persists; the server rewrites config.toml on every run.
    Path("/auth/config.toml").write_text("""approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
[features]
shell_tool = false
unified_exec = false
js_repl = false
code_mode = false
multi_agent = false
plugins = false
apps = false
computer_use = false
image_generation = false
apply_patch_freeform = false
[mcp_servers.secondshift]
command = "python3"
args = ["/opt/secondshift/bridge.py"]
""")
    return [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--strict-config",
        "--json",
        "-",
    ]


def event_result(
    event: dict[str, Any], backend: str, report: str
) -> tuple[str, dict[str, int], bool]:
    usage: dict[str, int] = {}
    succeeded = False
    if backend == "claude_code" and event.get("type") == "result":
        report = str(event.get("result", ""))[:MAX_REPORT_CHARS]
        usage = {
            key: int(value)
            for key, value in event.get("usage", {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        succeeded = not event.get("is_error", True) and bool(report.strip())
    if backend == "codex":
        item = event.get("item", {})
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "agent_message"
        ):
            report = str(item.get("text", ""))[:MAX_REPORT_CHARS]
        if event.get("type") == "turn.completed":
            usage = {
                key: int(value)
                for key, value in event.get("usage", {}).items()
                if isinstance(value, int) and not isinstance(value, bool)
            }
            succeeded = bool(report.strip())
    return report, usage, succeeded


def failure_reason(event: dict[str, Any]) -> str | None:
    if event.get("type") == "result" and event.get("subtype") == "error_max_turns":
        return "turn_budget_exhausted"
    if event.get("type") == "result" and not event.get("is_error", True):
        return None
    if event.get("type") not in {"result", "error", "turn.failed"}:
        return None
    text = json.dumps(event).lower()
    if any(
        word in text
        for word in (
            "rate_limit",
            "rate limit",
            "usage limit",
            "quota",
            "out of extra usage",
        )
    ):
        return "quota_exhausted"
    if any(
        word in text
        for word in ("unauthorized", "authentication", "oauth", "token expired", "401")
    ):
        return "reconnect_required"
    return None


def watch_lease(
    process: subprocess.Popen[bytes], config: dict[str, Any], stop: threading.Event
) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while not stop.wait(5):
        try:
            request = urllib.request.Request(
                config["broker_url"],
                data=json.dumps(
                    {"jsonrpc": "2.0", "id": "heartbeat", "method": "ping"}
                ).encode(),
                headers={
                    "Authorization": f"Bearer {config['broker_token']}",
                    "Content-Type": "application/json",
                },
            )
            with opener.open(request, timeout=10) as response:
                if "error" in json.load(response):
                    process.kill()
                    return
        except Exception:
            if process.poll() is None:
                process.kill()
            return


def cli_versions() -> dict[str, str]:
    versions = {}
    for binary in ("claude", "codex"):
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        versions[binary] = result.stdout.strip()[:100]
    return versions


def main() -> None:
    os.umask(0o077)
    config = json.loads(sys.stdin.buffer.readline(512000))
    private_json(
        Path("/tmp/secondshift-broker.json"),
        {"url": config["broker_url"], "token": config["broker_token"]},
    )
    versions = cli_versions()
    report = ""
    usage: dict[str, int] = {}
    succeeded = False
    reason = "agent_failed"
    process = subprocess.Popen(
        command(config),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdin is not None
    try:
        process.stdin.write(config["prompt"].encode())
        process.stdin.close()
    except BrokenPipeError:
        pass
    stop = threading.Event()
    watcher = threading.Thread(
        target=watch_lease, args=(process, config, stop), daemon=True
    )
    watcher.start()
    try:
        assert process.stdout is not None
        while line := process.stdout.readline(MAX_EVENT_BYTES + 1):
            if len(line) > MAX_EVENT_BYTES:
                reason = "agent_output_limit"
                process.kill()
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if failure := failure_reason(event):
                reason = failure
            report, observed, finished = event_result(event, config["backend"], report)
            usage.update(observed)
            succeeded = succeeded or finished
            tokens = sum(
                usage.get(key, 0)
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            )
            if tokens >= config["token_budget"]:
                reason = "token_budget_exhausted"
                succeeded = False
                process.kill()
                break
        code = process.wait(timeout=15)
        if code and reason == "agent_failed":
            succeeded = False
        if succeeded:
            reason = "completed"
    finally:
        stop.set()
        if process.poll() is None:
            process.kill()
        process.wait()
    # Raw CLI events and stderr may contain credentials or tool payloads. Return
    # only the final report and numeric usage, never the underlying stream.
    print(
        json.dumps(
            {
                "report": report,
                "succeeded": succeeded,
                "reason": reason,
                "usage": usage,
                "versions": versions,
            }
        )
    )


if __name__ == "__main__":
    main()
