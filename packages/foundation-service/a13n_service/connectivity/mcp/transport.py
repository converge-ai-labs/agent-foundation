"""Upstream MCP transport with Foundation endpoint and resource bounds."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx2
from anyio import fail_after, to_thread
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import Tool

from a13n_service.connectivity.bounds import DISCOVERY_MAX_BYTES, DISCOVERY_MAX_PAGES, DISCOVERY_MAX_TOOLS
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.connectivity.tool_validation import validate_tools
from a13n_service.endpoint_policy import EndpointPolicy


class BoundedMCPClient(Client):
    async def list_tools(self, max_pages: int = DISCOVERY_MAX_PAGES) -> list[Tool]:
        tools: list[Tool] = []
        cursor: str | None = None
        cursors: set[str] = set()
        names: set[str] = set()
        size = 0
        with fail_after(30):
            for _ in range(min(max_pages, DISCOVERY_MAX_PAGES)):
                page = await self.list_tools_mcp(cursor=cursor)
                tools.extend(page.tools)
                if len(tools) > DISCOVERY_MAX_TOOLS:
                    raise ValueError("tool_discovery_too_large")
                size += await to_thread.run_sync(validate_tools, page.tools)
                if size > DISCOVERY_MAX_BYTES or names.intersection(tool.name for tool in page.tools):
                    raise ValueError("tool_discovery_invalid")
                names.update(tool.name for tool in page.tools)
                cursor = page.nextCursor
                if cursor is None:
                    return tools
                if cursor in cursors:
                    raise ValueError("tool_discovery_invalid_cursor")
                cursors.add(cursor)
        raise ValueError("tool_discovery_too_large")


class _BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self._stream:
            size += len(chunk)
            if size > DISCOVERY_MAX_BYTES:
                raise ValueError("mcp_response_too_large")
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class RemoteTransport:
    def __init__(
        self, policy: EndpointPolicy, *, timeout_seconds: float = 30, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._transport = transport
        self._policy = policy
        self._timeout = timeout_seconds

    @asynccontextmanager
    async def connect(
        self,
        endpoint: str,
        *,
        headers: dict[str, str],
        refresh_headers: Callable[[], Awaitable[dict[str, str]]] | None = None,
    ) -> AsyncIterator[BoundedMCPClient]:
        endpoint = await self._policy.validate(endpoint, resolve_dns=True)

        async def request_guard(request: httpx2.Request) -> None:
            if str(request.url) != endpoint:
                raise ValueError("mcp_endpoint_changed")
            await self._policy.validate(str(request.url), resolve_dns=True)
            request.headers.pop("cookie", None)
            if refresh_headers is not None:
                current = await refresh_headers()
                for key in headers:
                    request.headers.pop(key, None)
                request.headers.update(current)

        async def response_guard(response: httpx2.Response) -> None:
            if response.is_redirect:
                raise ValueError("mcp_redirect_rejected")
            if not isinstance(response.stream, httpx2.AsyncByteStream):
                raise TypeError("MCP requires an async response stream")
            response.stream = _BoundedStream(response.stream)

        def http_factory(**_: object) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(
                cookies=cookie_free_jar(),
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
                transport=self._transport,
                event_hooks={"request": [request_guard], "response": [response_guard]},
            )

        transport = StreamableHttpTransport(
            endpoint,
            # FastMCP owns the client context; httpx2 implements its asynchronous transport API.
            httpx_client_factory=http_factory,  # type: ignore[arg-type]
        )
        async with BoundedMCPClient(transport, timeout=self._timeout) as client:
            yield client
