"""Observe transport loss and authorize SDK requests without reproducing MCP."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, Unpack

from fastmcp.client.transports import ClientTransport, StreamableHttpTransport
from fastmcp.client.transports.base import SessionKwargs, TransportOptions
from mcp import ClientSession
from mcp.shared.exceptions import MCPError
from mcp.shared.jsonrpc_dispatcher import JSONRPCDispatcher
from mcp.types import INVALID_REQUEST
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS


class ObservedTransport(ClientTransport):
    """Use the SDK2 dispatcher/session extension points; never retry a business call."""

    def __init__(
        self,
        wrapped: ClientTransport,
        on_closed: Callable[[], None],
        before_request: Callable[[Any], Awaitable[None]] | None = None,
    ) -> None:
        self.wrapped = wrapped
        self.on_closed = on_closed
        self.before_request = before_request
        self.legacy_only = wrapped.legacy_only

    @asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncIterator[ClientSession]:
        options = transport_options or TransportOptions()
        on_closed, before_request, wrapped = self.on_closed, self.before_request, self.wrapped

        class ObservedDispatcher(JSONRPCDispatcher):
            async def run(self, *args: Any, **kwargs: Any) -> None:
                try:
                    await super().run(*args, **kwargs)
                finally:
                    on_closed()

        class ObservedSession(options.session_class):
            def __init__(self, read_stream: Any, write_stream: Any, **kwargs: Any) -> None:
                super().__init__(dispatcher=ObservedDispatcher(read_stream, write_stream), **kwargs)

            async def send_request(self, request: Any, *args: Any, **kwargs: Any) -> Any:
                if before_request is not None:
                    await before_request(request)
                try:
                    return await super().send_request(request, *args, **kwargs)
                except MCPError as exc:
                    # Session termination exists only in the handshake era. A modern
                    # stateless 404, per-request interruption or business error is not
                    # evidence that the owning client generation has been lost.
                    if (
                        self.protocol_version in HANDSHAKE_PROTOCOL_VERSIONS
                        and isinstance(wrapped, StreamableHttpTransport)
                        and wrapped.get_session_id() is not None
                        and exc.error.code == INVALID_REQUEST
                        and exc.error.message == "Session terminated"
                    ):
                        on_closed()
                    raise

        async with wrapped.connect_session(
            transport_options=replace(options, session_class=ObservedSession), **session_kwargs
        ) as session:
            yield session

    async def close(self) -> None:
        await self.wrapped.close()

    def get_session_id(self) -> str | None:
        return self.wrapped.get_session_id()
