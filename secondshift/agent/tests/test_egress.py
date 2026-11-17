"""The egress proxy opens tunnels only to allowed public HTTPS hosts."""

import asyncio
import functools
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from secondshift.agent.image import egress
from secondshift.agent.image.egress import Denied, is_public, parse_connect

SECRET = "proxy-secret-value"


@pytest.mark.parametrize(
    ("head", "target"),
    [
        (b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n", ("api.anthropic.com", 443)),
        (
            b"CONNECT API.Anthropic.COM.:443 HTTP/1.1\r\n\r\n",
            ("api.anthropic.com", 443),
        ),
    ],
)
def test_connect_targets_are_normalized(head: bytes, target: tuple[str, int]) -> None:
    assert parse_connect(head) == target


@pytest.mark.parametrize(
    ("head", "reason"),
    [
        (b"GET http://example.com/ HTTP/1.1\r\n\r\n", "method"),
        (b"CONNECT example.com HTTP/1.1\r\n\r\n", "malformed"),
        (b"CONNECT example.com:https HTTP/1.1\r\n\r\n", "malformed"),
        (b"\xff\xfe\r\n\r\n", "malformed"),
    ],
)
def test_other_requests_are_refused(head: bytes, reason: str) -> None:
    with pytest.raises(Denied) as denied:
        parse_connect(head)
    assert denied.value.reason == reason


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("160.79.104.10", True),
        ("2607:6bc0::10", True),
        ("10.0.0.1", False),
        ("172.30.0.2", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("::ffff:127.0.0.1", False),
        ("fe80::1%eth0", False),
    ],
)
def test_only_public_addresses_count(address: str, public: bool) -> None:
    assert is_public(address) is public


async def exchange(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
    request: bytes,
    then: bytes = b"",
) -> bytes:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    reply = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    if then:
        writer.write(then)
        await writer.drain()
        reply += await asyncio.wait_for(reader.read(len(then)), 5)
    writer.close()
    server.close()
    return reply


def logged(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def connect(host: str, port: int = 443) -> bytes:
    return (
        f"CONNECT {host}:{port} HTTP/1.1\r\nProxy-Authorization: Basic {SECRET}\r\n\r\n"
    ).encode()


@pytest.mark.parametrize(
    ("host", "port", "status", "reason"),
    [
        ("example.com", 443, 403, "host"),
        ("api.anthropic.com", 22, 403, "port"),
        ("172.30.0.2", 443, 403, "host"),
    ],
)
def test_the_proxy_refuses_and_logs_other_destinations(
    capsys: pytest.CaptureFixture[str], host: str, port: int, status: int, reason: str
) -> None:
    reply = asyncio.run(exchange(egress.handle_connect, connect(host, port)))
    assert reply.startswith(f"HTTP/1.1 {status} ".encode())
    [entry] = logged(capsys)
    assert entry == {
        "event": "egress",
        "decision": "denied",
        "host": host,
        "port": port,
        "client": "127.0.0.1",
        "reason": reason,
    }


def test_an_allowed_name_that_resolves_inside_is_refused(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(egress, "ALLOWED_HOSTS", frozenset({"localhost"}))
    reply = asyncio.run(exchange(egress.handle_connect, connect("localhost")))
    assert reply.startswith(b"HTTP/1.1 403 ")
    assert logged(capsys)[0]["reason"] == "address"


def test_a_registry_proxy_refuses_the_agents_providers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registries = egress.allowed_hosts(egress.REGISTRY_HOSTS)
    handler = functools.partial(egress.handle_connect, hosts=registries)

    reply = asyncio.run(exchange(handler, connect("api.anthropic.com")))

    assert reply.startswith(b"HTTP/1.1 403 ")
    assert logged(capsys)[0]["reason"] == "host"


def test_allowed_hosts_replace_the_providers() -> None:
    assert egress.allowed_hosts([]) == egress.ALLOWED_HOSTS
    assert egress.allowed_hosts(["PyPI.org.", "registry.npmjs.org"]) == {
        "pypi.org",
        "registry.npmjs.org",
    }


async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(await reader.read(1024))
    await writer.drain()
    writer.close()


def test_an_allowed_host_gets_a_tunnel_and_no_headers_are_logged(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> bytes:
        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = upstream.sockets[0].getsockname()[1]

        async def resolve(host: str, target: int) -> list[str]:
            return ["127.0.0.1"]

        async def open_first(
            addresses: list[str], target: int
        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            return await asyncio.open_connection(addresses[0], port)

        monkeypatch.setattr(egress, "public_addresses", resolve)
        monkeypatch.setattr(egress, "open_first", open_first)
        try:
            return await exchange(
                egress.handle_connect, connect("api.anthropic.com"), b"hello"
            )
        finally:
            upstream.close()

    reply = asyncio.run(scenario())
    assert reply == b"HTTP/1.1 200 Connection established\r\n\r\nhello"
    output = capsys.readouterr().out
    assert SECRET not in output
    assert json.loads(output)["decision"] == "allowed"


def test_the_forward_port_passes_bytes_to_its_fixed_target() -> None:
    async def scenario() -> bytes:
        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = upstream.sockets[0].getsockname()[1]
        try:
            server = await asyncio.start_server(
                egress.forwarder("127.0.0.1", port), "127.0.0.1", 0
            )
            listen = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", listen)
            writer.write(b"POST /gateway/agent/mcp HTTP/1.1\r\n\r\n")
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(1024), 5)
            writer.close()
            server.close()
            return reply
        finally:
            upstream.close()

    assert asyncio.run(scenario()) == b"POST /gateway/agent/mcp HTTP/1.1\r\n\r\n"


def test_a_full_proxy_refuses_new_tunnels(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(egress, "SLOTS", egress.Slots(0))
    reply = asyncio.run(exchange(egress.handle_connect, connect("api.anthropic.com")))
    assert reply.startswith(b"HTTP/1.1 503 ")
    assert logged(capsys)[0]["reason"] == "busy"


def test_a_full_proxy_refuses_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(egress, "SLOTS", egress.Slots(0))

    async def scenario() -> bytes:
        server = await asyncio.start_server(
            egress.forwarder("127.0.0.1", 9), "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        reply = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        server.close()
        return reply

    assert asyncio.run(scenario()) == b""


def test_slots_are_returned_when_a_connection_ends() -> None:
    slots = egress.Slots(1)
    with slots.take() as first:
        assert first
        with slots.take() as second:
            assert not second
    with slots.take() as again:
        assert again


async def tunnel_through(
    upstream_handler: Callable[
        [asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]
    ],
    seconds: float,
    idle_seconds: float,
) -> bytes:
    """Open a forward to an upstream and read for a while without sending."""
    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    target = upstream.sockets[0].getsockname()[1]
    server = await asyncio.start_server(
        egress.forwarder("127.0.0.1", target, idle_seconds), "127.0.0.1", 0
    )
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    received = b""
    try:
        while data := await asyncio.wait_for(reader.read(64), seconds):
            received += data
    except TimeoutError:
        received += b"<open>"
    writer.close()
    server.close()
    upstream.close()
    return received


def test_an_idle_tunnel_is_closed() -> None:
    async def silent(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await asyncio.sleep(5)

    # Closed by the proxy well before the client gives up after 3 seconds.
    assert asyncio.run(tunnel_through(silent, 3, idle_seconds=0.2)) == b""


def test_a_stream_from_one_side_keeps_the_tunnel_open() -> None:
    async def streaming(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        for chunk in [b"."] * 6:
            writer.write(chunk)
            await writer.drain()
            await asyncio.sleep(0.1)
        writer.close()

    # 0.6 seconds of data, twice the idle limit, arrives in full.
    assert asyncio.run(tunnel_through(streaming, 3, idle_seconds=0.3)) == b"......"


def test_a_stalled_download_is_closed_so_the_client_can_retry() -> None:
    async def stalls_midway(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Part of a body, then nothing, with the connection left open.
        writer.write(b"partial")
        await writer.drain()
        await asyncio.sleep(5)

    # The client sees the connection end instead of waiting the whole time.
    assert asyncio.run(tunnel_through(stalls_midway, 3, idle_seconds=0.2)) == (
        b"partial"
    )


@pytest.mark.parametrize(
    ("argv", "idle"),
    [
        ([], egress.IDLE_SECONDS),
        (["--allow-registries"], egress.SETUP_IDLE_SECONDS),
        (["--allow-host", "pypi.example.com"], egress.SETUP_IDLE_SECONDS),
        (["--allow-registries", "--idle-seconds", "30"], 30),
    ],
)
def test_package_hosts_get_the_short_idle_limit(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], idle: float
) -> None:
    started: list[float] = []

    async def serve(
        port: int, forwards: object, hosts: object, idle_seconds: float
    ) -> None:
        started.append(idle_seconds)

    monkeypatch.setattr(egress, "serve", serve)
    egress.main(argv)
    assert started == [idle]
