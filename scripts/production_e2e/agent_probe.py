"""Deterministic agent transport probe; never authenticates with a provider."""

import json
import os
import socket
import sys
import urllib.request
from pathlib import Path


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
        request = urllib.request.Request(
            config["broker_url"],
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "trigger_payload", "arguments": {}},
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {config['broker_token']}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)["result"]
        assert not result["isError"], result
        payload = json.loads(result["content"][0]["text"])
        assert payload["body"] == "production-agent-payload-probe", payload
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
