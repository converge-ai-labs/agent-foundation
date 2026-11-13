"""Bounded retries for MCP connection initialization, never business requests."""

from __future__ import annotations

import json
from typing import Self

import httpx2
from a13n_logging import get_logger
from anyio import BrokenResourceError, CancelScope, ClosedResourceError, EndOfStream, sleep
from fastmcp import Client
from fastmcp.client.transports import ClientTransport
from mcp import MCPError

_TRANSIENT_HTTP_STATUS = {408, 429, 502, 503, 504}


async def raise_initialization_status(response: httpx2.Response) -> None:
    """Keep temporary HTTP status errors before the SDK loses their status code."""
    if response.status_code not in _TRANSIENT_HTTP_STATUS or response.request.method != "POST":
        return
    try:
        payload = json.loads(response.request.content)
    except ValueError:
        return
    if isinstance(payload, dict) and payload.get("method") in {"initialize", "server/discover"}:
        response.raise_for_status()


def _transient_initialization(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return all(_transient_initialization(item) for item in error.exceptions)
    if isinstance(error, httpx2.HTTPStatusError):
        return error.response.status_code in _TRANSIENT_HTTP_STATUS
    if isinstance(error, MCPError):
        return error.error.code == 408
    if isinstance(
        error,
        (TimeoutError, ConnectionError, httpx2.TransportError, BrokenResourceError, ClosedResourceError, EndOfStream),
    ):
        return True
    # FastMCP's exception-group flattening can discard the TimeoutError cause.
    # This exact SDK message is emitted only by its negotiation timeout handler.
    if isinstance(error, RuntimeError) and str(error) == "Failed to initialize server session":
        return True
    # FastMCP wraps negotiation timeouts and transport failures in RuntimeError.
    return (
        isinstance(error, RuntimeError) and error.__cause__ is not None and _transient_initialization(error.__cause__)
    )


class InitializingClient(Client[ClientTransport]):
    """Retry only entry into an unconnected SDK client, with SDK-owned cleanup."""

    async def __aenter__(self) -> Self:
        for attempt in range(3):
            try:
                return await super().__aenter__()
            except Exception as exc:
                with CancelScope(shield=True):
                    try:
                        await self.close()
                    except Exception as cleanup:
                        # SDK close can re-raise the failed session task. Still
                        # release the transport, but do not hide a new cleanup error.
                        if cleanup is not exc and cleanup is not exc.__cause__:
                            raise
                        await self.transport.close()
                if attempt == 2 or not _transient_initialization(exc):
                    raise
                # Log types only: SDK exceptions can contain credential-bearing URLs.
                get_logger(__name__).warning(
                    "MCP initialization failed (%s); retrying attempt %s/3", type(exc).__name__, attempt + 2
                )
                await sleep(2**attempt)
        raise AssertionError("unreachable")
