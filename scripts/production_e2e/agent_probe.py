"""Deterministic agent transport probe; never authenticates with a provider."""

import json
import os
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any


def call_tool(config: dict[str, Any], name: str, call_id: int) -> dict[str, Any]:
    request = urllib.request.Request(
        config["broker_url"],
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {config['broker_token']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)["result"]
    assert not result["isError"], (name, result)
    return result


def main() -> None:
    config = json.load(sys.stdin)
    assert not Path("/workspace").exists()
    assert not Path("/var/lib/secondshift/artifacts").exists()
    assert not any("ARTIFACT" in name for name in os.environ)
    try:
        socket.getaddrinfo("postgres", 5432)
    except socket.gaierror:
        pass
    else:
        raise RuntimeError("Agent can resolve the application database")
    if config.get("broker_url"):
        result = call_tool(config, "trigger_payload", 1)
        payload = json.loads(result["content"][0]["text"])
        assert payload["body"] == "production-agent-payload-probe", payload
        # Workspace tools cross from the gateway to the runner.
        call_tool(config, "workspace_list_files", 2)
    print(
        json.dumps(
            {
                "succeeded": True,
                "reason": "completed",
                "report": "production-agent-payload-probe",
            }
        )
    )


if __name__ == "__main__":
    main()
