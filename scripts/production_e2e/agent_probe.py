"""Deterministic agent transport probe; never authenticates with a provider."""

import json
import os
import socket
import sys
import urllib.parse
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


def connect_status(proxy: str, host: str) -> int:
    """The status the egress proxy answers a CONNECT to host:443 with."""
    address = urllib.parse.urlsplit(proxy)
    with socket.create_connection((address.hostname, address.port), timeout=20) as sock:
        sock.sendall(
            f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode()
        )
        return int(sock.recv(1024).split(b" ", 2)[1])


def check_egress() -> None:
    """The agent leaves only through the proxy, and only to its providers."""
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    except OSError:
        pass
    else:
        raise RuntimeError("Agent reached the internet without the proxy")
    proxy = os.environ["HTTPS_PROXY"]
    assert connect_status(proxy, "api.anthropic.com") == 200
    assert connect_status(proxy, "example.com") == 403


def check_no_control_plane(broker_url: str) -> None:
    """Tool calls go to the runner's relay through the proxy, and nothing of
    the control plane is on the agents network."""
    proxy = urllib.parse.urlsplit(os.environ["HTTPS_PROXY"]).hostname
    assert urllib.parse.urlsplit(broker_url).hostname == proxy, broker_url
    # Where earlier releases put the gateway.
    try:
        socket.create_connection(("172.30.0.2", 8001), timeout=5).close()
    except OSError:
        pass
    else:
        raise RuntimeError("Agent reached the gateway's old address")


def main() -> None:
    config = json.load(sys.stdin)
    check_egress()
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
        check_no_control_plane(config["broker_url"])
        result = call_tool(config, "trigger_payload", 1)
        payload = json.loads(result["content"][0]["text"])
        assert payload["body"] == "production-agent-payload-probe", payload
        # Workspace tools cross from the worker back to the runner.
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
