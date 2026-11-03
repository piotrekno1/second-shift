"""Minimal stdio-to-HTTP MCP bridge. Runs only inside the agent image."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAX_MESSAGE_BYTES = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def main() -> None:
    config = json.loads(Path("/tmp/secondshift-broker.json").read_text())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for line in sys.stdin.buffer:
        if len(line) > MAX_MESSAGE_BYTES:
            raise ValueError("MCP request too large")
        message = json.loads(line)
        request = urllib.request.Request(
            config["url"],
            data=line,
            headers={
                "Authorization": f"Bearer {config['token']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with opener.open(request, timeout=910) as response:
                body = response.read(MAX_MESSAGE_BYTES + 1)
                if len(body) > MAX_MESSAGE_BYTES:
                    raise ValueError("MCP response too large")
        except (urllib.error.URLError, ValueError):
            if "id" not in message:
                continue
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {
                        "code": -32000,
                        "message": "SecondShift broker unavailable.",
                    },
                }
            ).encode()
        if body and "id" in message:
            sys.stdout.buffer.write(body + b"\n")
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
