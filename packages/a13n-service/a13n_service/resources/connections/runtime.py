"""What a worker calls for a run's connections: resolve in its short session, then open outside any session.

Opening yields one Harness capability per selected connection for the run's agent definition: a
native `MCP` whose caller headers are the run's frozen copy of its thread's `mcp_headers` for that
connection, or a toolset over the connector account. Before every tool call the connection must still be
enabled and ready, and the worker's `DispatchCheck` must pass; otherwise the call is never sent.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from functools import partial

import httpx2
from a13n_harness import AgentContext
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic_ai.capabilities import AbstractCapability, Toolset
from pydantic_ai.exceptions import ToolFailed
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import conflict
from a13n_service.providers.registry import Registry
from a13n_service.providers.tools import DispatchCheck, ToolDispatch
from a13n_service.providers.tools.connectors import connector_toolset, list_connector_tools
from a13n_service.providers.tools.mcp import mcp_capability
from a13n_service.resources.connections.access import McpConnection, ResolvedConnection, resolve_connection
from a13n_service.resources.connections.account import open_account
from a13n_service.resources.connections.discovery import cache_tools, cached_tools
from a13n_service.resources.connections.oauth import open_mcp_client
from a13n_service.resources.connections.schemas import ConnectionSelection, exposed_tools
from a13n_service.resources.connections.service import check_caller_headers
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.settings import Providers
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope


@dataclass(frozen=True, slots=True)
class SelectedConnection:
    connection: ResolvedConnection
    # The agent's tools that the connection still exposes; None is everything an MCP server lists.
    tools: tuple[str, ...] | None
    # The run's frozen caller headers for this connection; empty for connectors.
    caller_headers: Mapping[str, str]
    # MCP only: the model finds the tools through tool search instead of seeing every definition upfront.
    defer_loading: bool


async def resolve_connections(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    selections: Sequence[ConnectionSelection],
    mcp_headers: Mapping[str, Mapping[str, str]],
    *,
    authority: ExecutionAuthority,
) -> tuple[SelectedConnection, ...]:
    """Each selected connection, enabled, ready and usable under the run's authority; no external I/O."""
    selected: list[SelectedConnection] = []
    for selection in selections:
        connection = await resolve_connection(
            session, actor, scope, selection.connection_id, verb="run", authority=authority
        )
        if connection.status != "ready":
            raise conflict("connection", connection.id, connection.status)
        headers: dict[str, str] = {}
        if isinstance(connection, McpConnection):
            headers = dict(mcp_headers.get(connection.id, {}))
            # Rechecked at use: the connection's headers may have changed since the thread was written.
            check_caller_headers(connection.id, connection.config, headers)
        exposed = exposed_tools(connection.config)
        tools = selection.tools
        if tools is None:
            tools = exposed
        elif exposed is not None:
            tools = tuple(name for name in tools if name in exposed)
        selected.append(SelectedConnection(connection, tools, headers, selection.defer_loading))
    return tuple(selected)


@asynccontextmanager
async def open_connections(
    connections: Sequence[SelectedConnection],
    check: DispatchCheck,
    *,
    storage: Storage,
    redis: Redis,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> AsyncIterator[tuple[AbstractCapability[AgentContext], ...]]:
    """The run's capabilities, with fallback cleanup for every native MCP client's setup."""
    async with AsyncExitStack() as stack:

        async def client_for(connection: McpConnection) -> httpx2.AsyncClient:
            return await stack.enter_async_context(
                open_mcp_client(connection, storage=storage, keys=keys, policy=policy, settings=settings)
            )

        capabilities: list[AbstractCapability[AgentContext]] = []
        for selected in connections:
            connection = selected.connection
            checked = _checked(storage, connection.id, check)
            if isinstance(connection, McpConnection):
                capabilities.append(
                    mcp_capability(
                        connection.config.url,
                        connection.id,
                        partial(client_for, connection),
                        tools=selected.tools,
                        caller_headers=selected.caller_headers,
                        defer_loading=selected.defer_loading,
                        check=checked,
                        timeout=settings.tool_call_seconds,
                    )
                )
                continue
            account = await stack.enter_async_context(
                open_account(
                    connection,
                    keys=keys,
                    registry=registry,
                    policy=policy,
                    settings=settings,
                    timeout=settings.tool_call_seconds,
                )
            )
            tools = await cached_tools(redis, connection)
            if tools is None:
                tools = await list_connector_tools(account)
                await cache_tools(redis, connection, tools, ttl=settings.discovery_ttl)
            chosen = [tool for tool in tools if selected.tools is None or tool.name in selected.tools]
            toolset = connector_toolset(
                connection.id,
                connection.provider.id,
                account,
                chosen,
                check=checked,
                timeout=settings.tool_call_seconds,
            )
            capabilities.append(Toolset(toolset))
        yield tuple(capabilities)


def _checked(storage: Storage, connection_id: str, check: DispatchCheck) -> DispatchCheck:
    async def live(dispatch: ToolDispatch) -> None:
        async with short_session(storage) as session:
            usable = await session.scalar(
                select(ConnectionRow.id).where(
                    ConnectionRow.id == connection_id, ConnectionRow.enabled, ConnectionRow.status == "ready"
                )
            )
        if usable is None:
            raise ToolFailed("The connection was disabled or needs reauthorization; the call was not sent.")
        await check(dispatch)

    return live
