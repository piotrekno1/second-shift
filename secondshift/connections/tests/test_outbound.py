import socket
from typing import Any

import httpcore
import httpx
import pytest
from asgiref.sync import async_to_sync

from secondshift.connections.outbound import (
    OutboundUrlError,
    PinnedNetworkBackend,
    outbound_client,
    outbound_sync_client,
    validate_outbound_url,
    vetted_addresses,
)

LINK_LOCAL_URLS = [
    "https://169.254.169.254/latest/meta-data/",
    "https://2852039166/latest/meta-data/",
    "https://0xa9fea9fe/latest/meta-data/",
    "https://169.254.43518/latest/meta-data/",
    "https://[fe80::1]/x",
    "https://[::ffff:169.254.169.254]/x",
]


def answer(*addresses: str) -> list[tuple[object, ...]]:
    return [(2, 1, 6, "", (address, 443)) for address in addresses]


@pytest.mark.parametrize("allow_local", [True, False])
@pytest.mark.parametrize("url", LINK_LOCAL_URLS)
def test_link_local_urls_are_always_rejected(
    settings: Any, url: str, allow_local: bool
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = allow_local

    with pytest.raises(OutboundUrlError, match="link-local"):
        validate_outbound_url(url)


@pytest.mark.parametrize("url", LINK_LOCAL_URLS)
def test_link_local_urls_are_rejected_without_resolving(
    settings: Any, url: str
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True

    with pytest.raises(OutboundUrlError):
        validate_outbound_url(url, resolve=False)


def test_local_addresses_stay_allowed_in_development(settings: Any) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True

    assert validate_outbound_url("http://127.0.0.1:9000/x") == "http://127.0.0.1:9000/x"
    assert vetted_addresses(answer("127.0.0.1", "10.0.0.5")) == [
        "127.0.0.1",
        "10.0.0.5",
    ]


@pytest.mark.parametrize("allow_local", [True, False])
@pytest.mark.parametrize(
    "address", ["169.254.169.254", "fe80::1", "::ffff:169.254.1.1"]
)
def test_names_resolving_to_link_local_are_refused_at_connect(
    settings: Any, address: str, allow_local: bool
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = allow_local

    with pytest.raises(httpcore.ConnectError):
        vetted_addresses(answer("93.184.216.34", address))


def test_names_resolving_to_link_local_are_refused_in_development(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: answer("169.254.169.254")
    )

    with pytest.raises(OutboundUrlError, match="link-local"):
        validate_outbound_url("http://metadata.example.test/")


def test_messages_keep_url_in_capitals(settings: Any) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False

    with pytest.raises(OutboundUrlError, match=r"^Base URLs must use HTTPS"):
        validate_outbound_url("http://example.com", "base URL")


def refuse_private_answers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """DNS answers with a private address. Returns each host a socket opened to."""

    async def fake_getaddrinfo(host: str, port: int, **_: Any) -> list[Any]:
        return answer("10.0.0.5")

    connected: list[str] = []

    async def fake_connect(self: Any, host: str, port: int, **_: Any) -> None:
        connected.append(host)

    def fake_sync_connect(self: Any, host: str, port: int, **_: Any) -> None:
        connected.append(host)

    monkeypatch.setattr(
        "secondshift.connections.outbound.anyio.getaddrinfo", fake_getaddrinfo
    )
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answer("10.0.0.5"))
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", fake_sync_connect)
    return connected


def test_clients_for_configured_services_refuse_private_answers(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False
    connected = refuse_private_answers(monkeypatch)

    async def send() -> None:
        async with outbound_client({"Accept": "application/json"}) as client:
            await client.get("https://rebound.example/")

    with pytest.raises(httpx.ConnectError):
        async_to_sync(send)()
    with pytest.raises(httpx.ConnectError), outbound_sync_client() as client:
        client.get("https://rebound.example/")
    assert connected == []


def test_clients_for_configured_services_follow_no_redirects() -> None:
    async_client = outbound_client({"Accept": "application/json"})
    sync_client = outbound_sync_client()

    assert async_client.follow_redirects is False
    assert sync_client.follow_redirects is False
    assert async_client.headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize(
    "addresses", [["10.0.0.5"], ["93.184.215.14", "127.0.0.1"], ["::1"]]
)
def test_pinned_backend_refuses_private_answers(
    settings: Any, monkeypatch: pytest.MonkeyPatch, addresses: list[str]
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False

    async def fake_getaddrinfo(host: str, port: int, **_: Any) -> list[Any]:
        return [(None, None, None, "", (address, port)) for address in addresses]

    connected: list[str] = []

    async def fake_connect(self: Any, host: str, port: int, **_: Any) -> None:
        connected.append(host)

    monkeypatch.setattr(
        "secondshift.connections.outbound.anyio.getaddrinfo", fake_getaddrinfo
    )
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)

    with pytest.raises(httpcore.ConnectError):
        async_to_sync(PinnedNetworkBackend().connect_tcp)("rebound.example", 443)
    assert connected == []


def test_pinned_backend_connects_to_the_vetted_address(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False
    answers = iter([["93.184.215.14"], ["127.0.0.1"]])

    async def fake_getaddrinfo(host: str, port: int, **_: Any) -> list[Any]:
        return [(None, None, None, "", (address, port)) for address in next(answers)]

    connected: list[str] = []

    async def fake_connect(self: Any, host: str, port: int, **_: Any) -> str:
        connected.append(host)
        return "stream"

    monkeypatch.setattr(
        "secondshift.connections.outbound.anyio.getaddrinfo", fake_getaddrinfo
    )
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    backend = PinnedNetworkBackend()

    assert async_to_sync(backend.connect_tcp)("api.example", 443) == "stream"
    # A later answer that rebinds to a private address is refused.
    with pytest.raises(httpcore.ConnectError):
        async_to_sync(backend.connect_tcp)("api.example", 443)
    assert connected == ["93.184.215.14"]
