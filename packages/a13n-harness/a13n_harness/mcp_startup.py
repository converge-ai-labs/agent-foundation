"""Opt-in local MCP startup recovery over native clients and Toolsets.

Hosts prepare authority and credential bindings before returning an opener. Only
that opener and native Toolset entry participate in startup failure isolation.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal, Self

import httpx2
from a13n_logging import get_logger
from anyio import BrokenResourceError, CancelScope, ClosedResourceError, EndOfStream, sleep
from fastmcp import Client
from fastmcp.client.transports import ClientTransport
from mcp import MCPError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP, AbstractCapability
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError

_TRANSIENT_HTTP_STATUS = {408, 429, 502, 503, 504}
type MCPOnError = Literal["skip", "raise"]
type MCPToolsetOpener = Callable[[], Awaitable[MCPToolset[AgentContext]]]
type MCPPrepare = Callable[[RunContext[AgentContext]], Awaitable[MCPToolsetOpener]]
type MCPProject = Callable[[MCPToolset[AgentContext]], MCP[AgentContext]]


async def raise_initialization_status(response: httpx2.Response) -> None:
    """Preserve transient initialize/discovery HTTP status before SDK conversion."""
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
    # FastMCP's exception-group flattening can discard the timeout cause.
    if isinstance(error, RuntimeError) and str(error) == "Failed to initialize server session":
        return True
    return (
        isinstance(error, RuntimeError) and error.__cause__ is not None and _transient_initialization(error.__cause__)
    )


class InitializingClient(Client[ClientTransport]):
    """At most three initialization attempts, with 1s/2s cancellable backoff.

    Ready business requests retain upstream semantics and are never replayed by
    this wrapper. Each attempt retains the native client's initialization timeout.
    """

    async def __aenter__(self) -> Self:
        for attempt in range(3):
            try:
                return await super().__aenter__()
            except Exception as exc:
                with CancelScope(shield=True):
                    try:
                        await self.close()
                    except Exception as cleanup:
                        if cleanup is not exc and cleanup is not exc.__cause__:
                            raise
                        await self.transport.close()
                if attempt == 2 or not _transient_initialization(exc):
                    raise
                get_logger(__name__).warning(
                    "MCP initialization failed (%s); retrying attempt %s/3", type(exc).__name__, attempt + 2
                )
                await sleep(2**attempt)
        raise AssertionError("unreachable")


class UnavailableMCPToolset(FunctionToolset[AgentContext]):
    """An omitted inventory, retaining its configured tool-name ceiling."""

    def __init__(self, server_id: str, selected_tools: tuple[str, ...] | None) -> None:
        super().__init__(id=server_id)
        self.selected_tools = selected_tools


class _UnavailableMCP(MCP[AgentContext]):
    def __init__(self, server_id: str, selected_tools: tuple[str, ...] | None) -> None:
        super().__init__(id=server_id, native=False, local=UnavailableMCPToolset(server_id, selected_tools))

    def get_instructions(self) -> str:
        # Capability instructions survive ToolSearch deferral of Toolset guidance.
        return (
            f"MCP server {self.id} is unavailable after startup failure. "
            "Its tools are omitted for this Run. Continue with the remaining capabilities; "
            "do not claim operations on this server succeeded. A later Run can try connecting again."
        )


class MCPStartup(MCP[AgentContext]):
    """Run-local startup isolation for a Host-prepared local MCP source.

    ``prepare`` resolves authority, configuration and credentials outside isolation,
    then returns an opener for the native Toolset. Use ``InitializingClient`` in
    that opener for bounded connection retries. ``project`` can retain Host-owned
    filtering or MCP Apps hooks; its errors are not classified as startup failure.
    The entered projection is borrowed until logical-Run cleanup, independently
    of an enclosing Host-owned client lifetime.
    """

    def __init__(
        self,
        *,
        id: str,
        prepare: MCPPrepare,
        on_error: MCPOnError = "raise",
        tools: tuple[str, ...] | None = None,
        project: MCPProject | None = None,
    ) -> None:
        if not id or on_error not in {"skip", "raise"}:
            raise DefinitionError("Invalid MCP startup policy.", code="mcp_definition_invalid")
        super().__init__(id=id, native=False, local=FunctionToolset(id=id))
        self._prepare = prepare
        self._on_error = on_error
        self._selected_tools = tools
        self._project = project

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        assert self.id is not None
        cache_id = f"a13n.mcp.startup.{self.id}"
        existing = ctx.deps._run_capability(cache_id)
        if existing is not None:
            if not isinstance(existing, MCP):
                raise DefinitionError(
                    "MCP has an incompatible logical-Run replacement.", code="capability_type_mismatch"
                )
            return existing

        opener = await self._prepare(ctx)
        try:
            toolset = await opener()
            await toolset.__aenter__()
        except Exception as exc:
            if self._on_error == "raise":
                raise
            get_logger(__name__).warning(
                "MCP server skipped for this Run after startup failure",
                extra={"server_id": self.id, "error_type": type(exc).__name__},
            )
            replacement = _UnavailableMCP(self.id, self._selected_tools)
        else:
            # Register cleanup before projecting, so a projection error cannot
            # leak an otherwise successfully entered Toolset.
            async def close_projection() -> None:
                await toolset.__aexit__(None, None, None)

            ctx.deps._register_run_cleanup(cache_id, close_projection)
            replacement = (
                self._project(toolset)
                if self._project is not None
                else MCP[AgentContext](
                    id=self.id,
                    native=False,
                    local=toolset,
                    allowed_tools=list(self._selected_tools) if self._selected_tools is not None else None,
                )
            )
        ctx.deps._record_run_capability(cache_id, replacement)
        return replacement
