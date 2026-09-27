"""Observe SDK session EOF without replacing transport or request semantics."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, Unpack

from fastmcp.client.transports import ClientTransport, StreamableHttpTransport
from fastmcp.client.transports.base import SessionKwargs, TransportOptions
from mcp import ClientSession
from mcp.shared.exceptions import MCPError
from mcp.shared.jsonrpc_dispatcher import JSONRPCDispatcher
from mcp.types import INVALID_REQUEST


class ObservedTransport(ClientTransport):
    """The public dispatcher lifecycle is provisional in MCP 2.x; test this seam on upgrades."""

    def __init__(self, wrapped: ClientTransport, on_closed: Callable[[], None]) -> None:
        self.wrapped = wrapped
        self.on_closed = on_closed
        self.legacy_only = wrapped.legacy_only

    @asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncIterator[ClientSession]:
        options = transport_options or TransportOptions()
        on_closed = self.on_closed
        wrapped = self.wrapped

        class ObservedDispatcher(JSONRPCDispatcher):
            async def run(self, *args: Any, **kwargs: Any) -> None:
                try:
                    await super().run(*args, **kwargs)
                finally:
                    on_closed()

        class ObservedSession(options.session_class):
            def __init__(self, read_stream: Any, write_stream: Any, **kwargs: Any) -> None:
                super().__init__(dispatcher=ObservedDispatcher(read_stream, write_stream), **kwargs)

            async def send_request(self, *args: Any, **kwargs: Any) -> Any:
                try:
                    return await super().send_request(*args, **kwargs)
                except MCPError as exc:
                    # The HTTP SDK reports a lost established session without ending
                    # its dispatcher. Do not mistake a request-stream interruption,
                    # a stateless 404, or an ordinary business error for session loss.
                    if (
                        isinstance(wrapped, StreamableHttpTransport)
                        and wrapped.get_session_id() is not None
                        and exc.error.code == INVALID_REQUEST
                        and exc.error.message == "Session terminated"
                    ):
                        on_closed()
                    raise

        async with self.wrapped.connect_session(
            transport_options=replace(options, session_class=ObservedSession), **session_kwargs
        ) as session:
            yield session

    async def close(self) -> None:
        await self.wrapped.close()

    def get_session_id(self) -> str | None:
        return self.wrapped.get_session_id()
