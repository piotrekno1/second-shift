"""The only way out for agent containers.

Agent containers sit on an internal Docker network with no route out. This
proxy sits on that network and one that can reach the internet. It opens
HTTPS tunnels only to the hosts below, and only to public addresses. It can
also pass one fixed port straight through to the gateway, for local mode,
where the gateway runs on the Docker host.

A second instance serves the setup phase, which installs a repository's
dependencies before the agent starts. It runs with --allow-host for each
package registry, so it never reaches the agents' providers.

It logs one JSON line per tunnel it opens or refuses. The lines hold the
destination and the reason, never request headers or content.

It uses only the standard library, so it runs from the agent image as well as
the application image.
"""

import argparse
import asyncio
import ipaddress
import json
import socket
import sys
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

PROXY_PORT = 3128
HTTPS_PORT = 443
# What the subscription CLIs need: Claude Code's API, and Codex's ChatGPT
# backend and its sign-in refresh. The runner turns off Claude Code's
# telemetry and update checks, so their hosts are left out.
ALLOWED_HOSTS = frozenset({"api.anthropic.com", "chatgpt.com", "auth.openai.com"})
# What the setup phase may reach by default: the Python and npm registries.
REGISTRY_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "registry.yarnpkg.com",
)
HEADER_LIMIT = 8192
HEADER_SECONDS = 10
CONNECT_SECONDS = 10
CHUNK_BYTES = 65536
# Tunnels and forwards together. Each holds two sockets, so one runaway agent
# can't use up the proxy's file descriptors and block every other agent.
MAX_CONNECTIONS = 256
# A tunnel with no bytes either way for this long is closed. Model streams
# send data or keep-alive pings well within it.
IDLE_SECONDS = 600
# Package downloads don't pause. A registry connection that goes quiet has
# stalled, and closing it lets npm or pip retry instead of waiting out setup.
SETUP_IDLE_SECONDS = 60


class Denied(Exception):
    """A tunnel the proxy refuses, with the reason it logs."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Slots:
    """Counts open connections. The proxy runs on one event loop, so a plain
    counter is enough."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    @contextmanager
    def take(self) -> Iterator[bool]:
        if self.used >= self.limit:
            yield False
            return
        self.used += 1
        try:
            yield True
        finally:
            self.used -= 1


SLOTS = Slots(MAX_CONNECTIONS)


@dataclass
class Activity:
    last: float


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


def is_public(address: str) -> bool:
    parsed = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    return parsed.is_global


def parse_connect(head: bytes) -> tuple[str, int]:
    """The host and port of a CONNECT request. Anything else is refused."""
    try:
        method, target, version = head.split(b"\r\n", 1)[0].decode().split(" ")
    except (UnicodeDecodeError, ValueError):
        raise Denied(400, "malformed") from None
    if method != "CONNECT" or not version.startswith("HTTP/1."):
        raise Denied(405, "method")
    host, separator, port = target.rpartition(":")
    if not separator or not port.isdigit():
        raise Denied(400, "malformed")
    return host.lower().rstrip("."), int(port)


async def public_addresses(host: str, port: int) -> list[str]:
    """Every address the host resolves to, when all of them are public."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise Denied(502, "resolve") from None
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    # One private answer refuses the host, so DNS can't point it inside.
    if not addresses or not all(is_public(address) for address in addresses):
        raise Denied(403, "address")
    return addresses


async def open_first(
    addresses: Sequence[str], port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    for address in addresses:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(address, port), CONNECT_SECONDS
            )
        except (OSError, TimeoutError):
            continue
    raise Denied(502, "connect")


async def pipe(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, activity: Activity
) -> None:
    loop = asyncio.get_running_loop()
    try:
        while data := await reader.read(CHUNK_BYTES):
            activity.last = loop.time()
            writer.write(data)
            await writer.drain()
    except OSError:
        pass
    finally:
        writer.close()


async def splice(
    client: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    idle_seconds: float,
) -> None:
    """Copy both ways until either side closes or the tunnel goes idle.

    Idle means no bytes in either direction, so a long stream from the
    provider keeps the tunnel open while the client sends nothing.
    """
    loop = asyncio.get_running_loop()
    activity = Activity(loop.time())
    pending = {
        asyncio.create_task(pipe(client[0], upstream[1], activity)),
        asyncio.create_task(pipe(upstream[0], client[1], activity)),
    }
    while pending:
        quiet = loop.time() - activity.last
        if quiet >= idle_seconds:
            for task in pending:
                task.cancel()
            client[1].close()
            upstream[1].close()
            await asyncio.gather(*pending, return_exceptions=True)
            return
        pending = (await asyncio.wait(pending, timeout=idle_seconds - quiet))[1]


def client_address(writer: asyncio.StreamWriter) -> str:
    peer = writer.get_extra_info("peername")
    return str(peer[0]) if peer else ""


def refuse(writer: asyncio.StreamWriter, denied: Denied, host: str, port: int) -> None:
    log(
        "egress",
        decision="denied",
        host=host,
        port=port,
        client=client_address(writer),
        reason=denied.reason,
    )
    writer.write(f"HTTP/1.1 {denied.status} Refused\r\n\r\n".encode())
    writer.close()


async def handle_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    hosts: frozenset[str] | None = None,
    idle_seconds: float = IDLE_SECONDS,
) -> None:
    with SLOTS.take() as taken:
        if not taken:
            refuse(writer, Denied(503, "busy"), "", 0)
            return
        await open_tunnel(
            reader, writer, ALLOWED_HOSTS if hosts is None else hosts, idle_seconds
        )


async def open_tunnel(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    hosts: frozenset[str],
    idle_seconds: float,
) -> None:
    client = client_address(writer)
    host, port = "", 0
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_SECONDS)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            raise Denied(400, "malformed") from None
        except TimeoutError:
            raise Denied(408, "timeout") from None
        host, port = parse_connect(head)
        if host not in hosts:
            raise Denied(403, "host")
        if port != HTTPS_PORT:
            raise Denied(403, "port")
        upstream = await open_first(await public_addresses(host, port), port)
    except Denied as denied:
        refuse(writer, denied, host, port)
        return
    log("egress", decision="allowed", host=host, port=port, client=client)
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await splice((reader, writer), upstream, idle_seconds)


Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def forwarder(
    target_host: str, target_port: int, idle_seconds: float = IDLE_SECONDS
) -> Handler:
    """Pass every connection through to one fixed, server-owned address."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        with SLOTS.take() as taken:
            if not taken:
                writer.close()
                return
            try:
                upstream = await asyncio.wait_for(
                    asyncio.open_connection(target_host, target_port),
                    CONNECT_SECONDS,
                )
            except (OSError, TimeoutError):
                writer.close()
                return
            await splice((reader, writer), upstream, idle_seconds)

    return handle


def parse_forward(value: str) -> tuple[int, str, int]:
    """LISTEN_PORT:HOST:PORT, such as 8001:host.docker.internal:8001."""
    listen, host, port = value.split(":")
    return int(listen), host, int(port)


async def serve(
    port: int,
    forwards: Sequence[tuple[int, str, int]],
    hosts: frozenset[str],
    idle_seconds: float = IDLE_SECONDS,
) -> None:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await handle_connect(reader, writer, hosts, idle_seconds)

    servers = [await asyncio.start_server(handle, "0.0.0.0", port, limit=HEADER_LIMIT)]
    for listen, host, target in forwards:
        servers.append(
            await asyncio.start_server(
                forwarder(host, target, idle_seconds), "0.0.0.0", listen
            )
        )
    log(
        "egress_started",
        port=port,
        hosts=sorted(hosts),
        idle_seconds=idle_seconds,
        forwards=[f"{listen}:{host}:{target}" for listen, host, target in forwards],
    )
    await asyncio.gather(*(server.serve_forever() for server in servers))


def allowed_hosts(names: Sequence[str]) -> frozenset[str]:
    """The hosts the options name, or the agents' providers when they name none."""
    return frozenset(name.lower().rstrip(".") for name in names) or ALLOWED_HOSTS


def idle_limit(names: Sequence[str]) -> float:
    """Package hosts get the short limit. The agents' providers stream slowly."""
    return SETUP_IDLE_SECONDS if names else IDLE_SECONDS


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PROXY_PORT)
    parser.add_argument(
        "--forward",
        type=parse_forward,
        action="append",
        default=[],
        help="LISTEN_PORT:HOST:PORT to pass through, such as the local gateway.",
    )
    parser.add_argument(
        "--allow-registries",
        action="store_true",
        help="Allow the package registries in place of the agents' providers. "
        "The setup phase's proxy runs with this.",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Another host to allow in place of the agents' providers, such as "
        "a private package index. Repeat it for each one.",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        help=f"Close a tunnel with no bytes either way for this long. Defaults to "
        f"{SETUP_IDLE_SECONDS} for package hosts and {IDLE_SECONDS} for the agents' "
        "providers.",
    )
    arguments = parser.parse_args(argv)
    names = [
        *(REGISTRY_HOSTS if arguments.allow_registries else ()),
        *arguments.allow_host,
    ]
    idle_seconds = arguments.idle_seconds or idle_limit(names)
    try:
        asyncio.run(
            serve(arguments.port, arguments.forward, allowed_hosts(names), idle_seconds)
        )
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
