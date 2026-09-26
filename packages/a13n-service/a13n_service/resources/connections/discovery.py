"""Tool discovery: what a connection's server or account lists now, cached briefly for editors and runs.

The cache is only an accelerator under `provider:{type}:`, keyed by the connection's version, so a change to the
connection or its credential reads fresh tools, while renewed OAuth tokens keep it. A test always discovers
anew with the credential and records its outcome on the connection, never replacing one for a later version.
Before an account is bound, a connector connection's tools are its app's catalogue, and its test fails.
"""

from typing import Literal

import anyio
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_logging import exception_details, get_logger
from pydantic import TypeAdapter
from redis.asyncio import Redis

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, lock, now, short_session, transaction
from a13n_service.providers.registry import Registry
from a13n_service.providers.tools import ToolInfo
from a13n_service.providers.tools.connectors import list_connector_tools
from a13n_service.providers.tools.discovery import cache, cached, failure_message, unavailable
from a13n_service.providers.tools.mcp import list_mcp_tools
from a13n_service.resources.connections.access import ConnectorConnection, ResolvedConnection, resolve_connection
from a13n_service.resources.connections.account import open_account
from a13n_service.resources.connections.oauth import open_mcp_client
from a13n_service.resources.connections.schemas import ConnectionTest, ConnectionTestOutcome, ToolPage
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.connector_providers.catalog import read_actions
from a13n_service.settings import Providers
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

logger = get_logger(__name__)

_TOOLS = TypeAdapter(list[ToolInfo])


async def cached_tools(redis: Redis, connection: ResolvedConnection) -> list[ToolInfo] | None:
    return await cached(redis, _key(connection), _TOOLS)


async def cache_tools(redis: Redis, connection: ResolvedConnection, tools: list[ToolInfo], *, ttl: int) -> None:
    await cache(redis, _key(connection), _TOOLS, tools, ttl=ttl)


async def discover_tools(
    connection: ResolvedConnection,
    *,
    storage: Storage,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> list[ToolInfo]:
    """The tools the connection's server or account lists now, presenting its credential."""
    with anyio.fail_after(settings.tool_call_seconds):
        if isinstance(connection, ConnectorConnection):
            async with open_account(
                connection,
                keys=keys,
                registry=registry,
                policy=policy,
                settings=settings,
                timeout=settings.tool_call_seconds,
            ) as account:
                return await list_connector_tools(account)
        async with open_mcp_client(connection, storage=storage, keys=keys, policy=policy, settings=settings) as client:
            return await list_mcp_tools(
                connection.config.url, connection.id, client, timeout=settings.tool_call_seconds
            )


async def list_tools(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    *,
    redis: Redis,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> ToolPage:
    connection = await _resolve(storage, actor, workspace_id, connection_id)
    if isinstance(connection, ConnectorConnection) and connection.credential is None:
        actions = await read_actions(
            connection.provider,
            connection.config.app,
            redis=redis,
            keys=keys,
            registry=registry,
            policy=policy,
            settings=settings,
        )
        return ToolPage(items=actions, next_cursor=None)
    tools = await cached_tools(redis, connection)
    if tools is None:
        try:
            tools = await discover_tools(
                connection, storage=storage, keys=keys, registry=registry, policy=policy, settings=settings
            )
        except Exception as error:
            logger.warning(
                "Connection tool discovery failed",
                extra={"connection_id": connection.id, "exception_details": exception_details(error)},
            )
            raise unavailable(f"connection:{connection.type}", error) from None
        await cache_tools(redis, connection, tools, ttl=settings.discovery_ttl)
    return ToolPage(items=tools, next_cursor=None)


async def test_connection(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    *,
    redis: Redis,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> ConnectionTest:
    """Discover the tools with the current configuration and credential, and record the outcome."""
    connection = await _resolve(storage, actor, workspace_id, connection_id)
    tools: list[ToolInfo] = []
    message = None
    try:
        tools = await discover_tools(
            connection, storage=storage, keys=keys, registry=registry, policy=policy, settings=settings
        )
    except Exception as error:
        logger.warning(
            "Connection test failed",
            extra={"connection_id": connection.id, "exception_details": exception_details(error)},
        )
        message = failure_message(error)
    else:
        await cache_tools(redis, connection, tools, ttl=settings.discovery_ttl)
    outcome = await _record(storage, connection, "succeeded" if message is None else "failed", message)
    return ConnectionTest(connection_id=connection.id, tools=tools, **outcome.model_dump())


async def _record(
    storage: Storage, connection: ResolvedConnection, status: Literal["succeeded", "failed"], message: str | None
) -> ConnectionTestOutcome:
    async with transaction(storage) as session:
        outcome = ConnectionTestOutcome(
            connection_version=connection.version, status=status, message=message, tested_at=await now(session)
        )
        row = await lock(session, ConnectionRow, connection.id)
        if row is not None and (row.last_test is None or row.last_test["connection_version"] <= connection.version):
            row.last_test = outcome.model_dump(mode="json")
    return outcome


async def _resolve(storage: Storage, actor: Principal, workspace_id: str, connection_id: str) -> ResolvedConnection:
    # Discovery presents the connection's credential, so it needs the same verb as a run.
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        return await resolve_connection(session, actor, scope, connection_id, verb="run")


def _key(connection: ResolvedConnection) -> str:
    return f"provider:{connection.type}:tools:{connection.id}:{connection.version}"
