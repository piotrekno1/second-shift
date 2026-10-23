"""HTTP transport for MCP and OAuth calls: vetted addresses, bounded bodies."""

from collections.abc import AsyncIterator

import httpx

from secondshift.integrations.mcp.base import MCPError
from secondshift.integrations.outbound import PinnedTransport

MCP_RESPONSE_MAX_BYTES = 2 * 1024 * 1024


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream) -> None:
        self.inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.inner:
            size += len(chunk)
            if size > MCP_RESPONSE_MAX_BYTES:
                raise MCPError("The MCP response exceeded the size limit.")
            yield chunk

    async def aclose(self) -> None:
        await self.inner.aclose()


class MCPTransport(PinnedTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise MCPError("Compressed MCP responses are not supported.")
        response.stream = BoundedStream(response.stream)
        return response
