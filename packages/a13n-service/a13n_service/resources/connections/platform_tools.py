"""Explicit connection lifecycle, non-secret configuration, and tool discovery."""

from typing import Annotated

from pydantic import Field

from a13n_service.infra import reads
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.providers.tools import mcp_catalog
from a13n_service.resources import catalog_reads
from a13n_service.resources.connections import authorization, discovery, service
from a13n_service.resources.connections.platform_inputs import (
    AccessSetup,
    ConnectionChanges,
    ConnectionCreation,
    SafeConnection,
    SafeConnectionPage,
    apply_changes,
    safe_connection,
)
from a13n_service.resources.connections.schemas import ConnectionTest, ToolPage
from a13n_service.resources.runtime import Runtime

type ToolContext = PlatformContext[Runtime]


async def list_mcp_servers(
    ctx: ToolContext,
    *,
    query: Annotated[str | None, Field(max_length=128)] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> mcp_catalog.McpServerPage:
    """Search suggested Remote MCP server definitions and their setup requirements."""
    return mcp_catalog.list_mcp_servers(
        ctx.runtime.settings.providers.mcp_servers, query=query, limit=limit, cursor=cursor
    )


async def create_connection(ctx: ToolContext, *, body: ConnectionCreation) -> SafeConnection | AccessSetup:
    """Create non-secret connection settings. Complete credentials and connector setup in trusted Console configuration."""
    try:
        value = await service.create_connection(
            ctx.runtime.storage,
            ctx.actor,
            ctx.workspace_id,
            body.request(),
            keys=ctx.runtime.keys,
            registry=ctx.runtime.registry,
            policy=ctx.runtime.endpoint_policy,
        )
    except ServiceError as error:
        if error.code != "invalid_argument" or error.details.get("field") != "config.setup":
            raise
        # The authorized create transaction rolled back. Report required trusted
        # setup explicitly; never ask the model to invent or collect setup secrets.
        return AccessSetup(connection_id=None, version=None, proposed_connection=body)
    return safe_connection(value)


async def get_connection(ctx: ToolContext, *, connection_id: str) -> SafeConnection:
    """Read non-secret connection settings, readiness and version before editing."""
    return safe_connection(
        await service.get_connection(ctx.runtime.storage, ctx.actor, ctx.workspace_id, connection_id)
    )


async def list_connections(ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None) -> SafeConnectionPage:
    """List connection readiness without exposing connector setup or credential values."""
    result = await service.list_connections(
        ctx.runtime.storage, ctx.actor, ctx.workspace_id, limit=limit, cursor=cursor
    )
    return SafeConnectionPage(items=[safe_connection(item) for item in result.items], next_cursor=result.next_cursor)


async def update_connection(
    ctx: ToolContext, *, connection_id: str, expected_version: str, body: ConnectionChanges
) -> SafeConnection:
    """Change only specified non-secret fields at the observed version. Endpoint/auth changes use the existing credential invalidation rules."""
    current = await service.get_connection(ctx.runtime.storage, ctx.actor, ctx.workspace_id, connection_id)
    updated = apply_changes(current, body, expected_version)
    value = await service.update_connection(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        connection_id,
        updated,
        if_match=expected_version,
        keys=ctx.runtime.keys,
        registry=ctx.runtime.registry,
        policy=ctx.runtime.endpoint_policy,
    )
    return safe_connection(value)


async def test_connection(ctx: ToolContext, *, connection_id: str) -> ConnectionTest:
    """Test the configured connection. External calls have effects; unknown results must be reconciled, never blindly replayed."""
    return await discovery.test_connection(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        connection_id,
        redis=ctx.runtime.redis,
        keys=ctx.runtime.keys,
        registry=ctx.runtime.registry,
        policy=ctx.runtime.endpoint_policy,
        settings=ctx.runtime.settings.providers,
    )


async def revoke_connection(ctx: ToolContext, *, connection_id: str, expected_version: str) -> SafeConnection:
    """Revoke access using the observed connection version and report local and remote outcomes."""
    result = await authorization.revoke_connection(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        connection_id,
        if_match=expected_version,
        keys=ctx.runtime.keys,
        registry=ctx.runtime.registry,
        policy=ctx.runtime.endpoint_policy,
        settings=ctx.runtime.settings,
    )
    return safe_connection(result)


async def list_connection_tools(ctx: ToolContext, *, connection_id: str) -> ToolPage:
    """Discover the configured connection's tools under its current authorization."""
    return await discovery.list_tools(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        connection_id,
        redis=ctx.runtime.redis,
        keys=ctx.runtime.keys,
        registry=ctx.runtime.registry,
        policy=ctx.runtime.endpoint_policy,
        settings=ctx.runtime.settings.providers,
    )


async def search_connection_tools(
    ctx: ToolContext,
    *,
    connection_id: str,
    query: reads.IndexQuery = None,
    limit: PageLimit = 10,
    cursor: str | None = None,
) -> catalog_reads.CatalogIndex:
    """Search names and descriptions with a cursor bound to this connection's catalog snapshot."""
    values = await list_connection_tools(ctx, connection_id=connection_id)
    return catalog_reads.index(
        values.items, scope=f"/connections/{connection_id}/tools", query=query, limit=limit, cursor=cursor
    )


async def get_connection_tool_definition(
    ctx: ToolContext,
    *,
    connection_id: str,
    name: reads.Names,
    offset: Annotated[int | None, Field(ge=0)] = None,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> reads.Definitions | reads.TextRead:
    """Read exact tool definitions; fragments remain bound to this authorized connection and content version."""
    values = await list_connection_tools(ctx, connection_id=connection_id)
    return reads.definitions(
        {value.name: value for value in values.items},
        name,
        source=f"/connections/{connection_id}/tools/definitions",
        offset=offset,
        limit=limit,
        version=version,
        query=query,
    )


TOOLS = (
    definition(list_mcp_servers, group="connections", effect="read"),
    definition(create_connection, group="connections", effect="write"),
    definition(get_connection, group="connections", effect="read"),
    definition(list_connections, group="connections", effect="read"),
    definition(update_connection, group="connections", effect="write"),
    definition(test_connection, group="connections", effect="write"),
    definition(revoke_connection, group="connections", effect="write"),
    definition(list_connection_tools, group="connections", effect="read"),
    definition(search_connection_tools, group="connections", effect="read"),
    definition(get_connection_tool_definition, group="connections", effect="read"),
)
