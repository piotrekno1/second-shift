import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlsplit

import anyio
import httpcore
import httpx
from django.conf import settings


class OutboundUrlError(ValueError):
    pass


def parse_address(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    parsed = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        return parsed.ipv4_mapped
    return parsed


def public_address(address: str) -> bool:
    return parse_address(address).is_global


def link_local_address(address: str) -> bool:
    """Link-local hosts include cloud metadata services such as 169.254.169.254.

    These stay blocked even when local addresses are allowed, because they can
    hand out the host machine's own credentials.
    """
    return parse_address(address).is_link_local


def literal_address(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host.strip("[]").split("%", 1)[0]))
    except ValueError:
        return None


def validate_outbound_url(url: str, noun: str = "URL", resolve: bool = True) -> str:
    """Reject URLs the server must not call. Messages never echo the URL."""
    url = url.strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise OutboundUrlError(f"Enter a valid {noun}.") from error
    allow_local = settings.OUTBOUND_ALLOW_LOCAL
    allowed_schemes = {"https", "http"} if allow_local else {"https"}
    if parts.scheme not in allowed_schemes:
        raise OutboundUrlError(
            f"{noun.capitalize()}s must use HTTPS."
            if not allow_local
            else f"{noun.capitalize()}s must use HTTP or HTTPS."
        )
    if not parts.hostname:
        raise OutboundUrlError(f"Enter a valid {noun}.")
    if parts.username or parts.password:
        raise OutboundUrlError(
            f"{noun.capitalize()}s cannot contain a username or password."
        )
    literal = literal_address(parts.hostname)
    if literal and link_local_address(literal):
        raise OutboundUrlError(
            f"{noun.capitalize()}s cannot point to a link-local address."
        )
    if allow_local or not resolve:
        return url
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(
                parts.hostname, port or 443, proto=socket.IPPROTO_TCP
            )
        }
    except (socket.gaierror, UnicodeError) as error:
        raise OutboundUrlError(f"The {noun} host could not be resolved.") from error
    if not addresses or not all(public_address(item) for item in addresses):
        raise OutboundUrlError(f"{noun.capitalize()}s must point to a public address.")
    return url


def host_label(url: str) -> str:
    """A redacted label safe to show: the host only, never the path or query."""
    parts = urlsplit(url)
    host = parts.hostname or "unknown host"
    return f"{host}:{parts.port}" if parts.port else host


def vetted_addresses(infos: Iterable[tuple[object, ...]]) -> list[str]:
    """The addresses from one DNS answer, if every one of them may be called."""
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))  # type: ignore[index]
    if not addresses:
        raise httpcore.ConnectError("The host could not be resolved.")
    if any(link_local_address(address) for address in addresses):
        raise httpcore.ConnectError("The host resolves to a link-local address.")
    if not settings.OUTBOUND_ALLOW_LOCAL and not all(
        public_address(address) for address in addresses
    ):
        raise httpcore.ConnectError("The host does not resolve to a public address.")
    return addresses


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connects only to addresses that pass the outbound check.

    The check and the connection use the same DNS answer, so a host cannot
    pass validation and then rebind to a private address. TLS still verifies
    the original hostname, which httpcore sends as SNI.
    """

    def __init__(self) -> None:
        self.inner = httpcore.AnyIOBackend()

    async def public_addresses(self, host: str, port: int) -> list[str]:
        try:
            infos = await anyio.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except (OSError, UnicodeError) as error:
            raise httpcore.ConnectError("The host could not be resolved.") from error
        return vetted_addresses(infos)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        last_error: Exception | None = None
        for address in await self.public_addresses(host, port):
            try:
                return await self.inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as error:
                last_error = error
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Unix sockets are not allowed.")

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


class PinnedSyncNetworkBackend(httpcore.NetworkBackend):
    """The blocking twin of PinnedNetworkBackend, for code that is not async."""

    def __init__(self) -> None:
        self.inner = httpcore.SyncBackend()

    def public_addresses(self, host: str, port: int) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except (OSError, UnicodeError) as error:
            raise httpcore.ConnectError("The host could not be resolved.") from error
        return vetted_addresses(infos)

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        last_error: Exception | None = None
        for address in self.public_addresses(host, port):
            try:
                return self.inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as error:
                last_error = error
        assert last_error is not None
        raise last_error

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        raise httpcore.ConnectError("Unix sockets are not allowed.")

    def sleep(self, seconds: float) -> None:
        self.inner.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """An httpx transport that ignores proxy settings and pins vetted addresses."""

    def __init__(self, limits: httpx.Limits = httpx.Limits()) -> None:
        super().__init__(trust_env=False, limits=limits)
        # httpx has no public hook for the network backend; swap the pool it
        # built for one that uses ours, with the same TLS and pool settings.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=PinnedNetworkBackend(),
        )


class PinnedSyncTransport(httpx.HTTPTransport):
    """The blocking twin of PinnedTransport."""

    def __init__(self, limits: httpx.Limits = httpx.Limits()) -> None:
        super().__init__(trust_env=False, limits=limits)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=PinnedSyncNetworkBackend(),
        )
