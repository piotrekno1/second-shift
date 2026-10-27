import socket
from typing import Any

import httpcore
import pytest

from secondshift.connections.outbound import (
    OutboundUrlError,
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
