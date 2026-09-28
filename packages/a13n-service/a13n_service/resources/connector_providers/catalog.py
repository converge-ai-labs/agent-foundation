"""A connector provider's app catalogue, read with the provider's credential and cached briefly."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import anyio
from a13n_harness.providers.connector.contracts import ConnectorProviderRuntime, DiscoveredConnector
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_logging import exception_details, get_logger
from pydantic import TypeAdapter
from redis.asyncio import Redis

from a13n_service.infra import cursors
from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.providers.registry import Registry
from a13n_service.providers.tools import ToolInfo
from a13n_service.providers.tools.connectors import list_connector_tools, open_connector
from a13n_service.providers.tools.discovery import cache, cached, unavailable
from a13n_service.resources.connector_providers.schemas import ConnectorActionPage, ConnectorApp, ConnectorAppPage
from a13n_service.resources.providers.service import ResolvedProvider, resolve_provider
from a13n_service.resources.providers.tables import ConnectorProviderRow
from a13n_service.settings import Providers
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

logger = get_logger(__name__)

_APPS = TypeAdapter(list[ConnectorApp])
_ACTIONS = TypeAdapter(list[ToolInfo])


@asynccontextmanager
async def open_connector_provider(
    provider: ResolvedProvider,
    *,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
    timeout: float,
) -> AsyncIterator[ConnectorProviderRuntime]:
    """The provider's runtime with its credential; transports close with the context."""
    async with open_connector(
        registry.get("connector", provider.type),
        provider.config,
        provider.reveal_credential(keys),
        policy=policy,
        timeout=timeout,
        max_bytes=settings.response_bytes,
    ) as runtime:
        yield runtime


async def list_apps(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    *,
    query: str | None,
    limit: int,
    cursor: str | None,
    refresh: bool,
    redis: Redis,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> ConnectorAppPage:
    """Apps in key order whose key or name contains `query`, which a cursor stays bound to; `refresh` reads anew."""
    provider = await _resolve(storage, actor, workspace_id, provider_id)
    key = f"provider:{provider.type}:apps:{provider.id}:{provider.version}"
    apps = None if refresh else await cached(redis, key, _APPS)
    if apps is None:
        apps = await _read(provider, _apps, keys=keys, registry=registry, policy=policy, settings=settings)
        await cache(redis, key, _APPS, apps, ttl=settings.discovery_ttl)
    needle = (query or "").casefold()
    matching = [app for app in apps if needle in app.key.casefold() or needle in app.name.casefold()]
    items, next_cursor = cursors.key_page(
        matching,
        lambda app: app.key,
        kind="connector_apps",
        owner=cursors.query_owner(provider.id, needle),
        cursor=cursor,
        limit=limit,
    )
    return ConnectorAppPage(items=items, next_cursor=next_cursor)


async def get_app(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    app: str,
    *,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> ConnectorApp:
    provider = await _resolve(storage, actor, workspace_id, provider_id)

    async def read(runtime: ConnectorProviderRuntime) -> ConnectorApp:
        return _app(await runtime.discover_connector(app))

    return await _read(provider, read, keys=keys, registry=registry, policy=policy, settings=settings)


async def list_actions(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    app: str,
    *,
    redis: Redis,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> ConnectorActionPage:
    provider = await _resolve(storage, actor, workspace_id, provider_id)
    actions = await read_actions(
        provider, app, redis=redis, keys=keys, registry=registry, policy=policy, settings=settings
    )
    return ConnectorActionPage(items=actions, next_cursor=None)


async def read_actions(
    provider: ResolvedProvider,
    app: str,
    *,
    redis: Redis,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> list[ToolInfo]:
    """Every action the app offers, before any account is connected."""
    key = f"provider:{provider.type}:actions:{provider.id}:{provider.version}:{app}"
    actions = await cached(redis, key, _ACTIONS)
    if actions is None:
        actions = await _read(
            provider,
            lambda runtime: list_connector_tools(runtime.tool_catalog(app)),
            keys=keys,
            registry=registry,
            policy=policy,
            settings=settings,
        )
        await cache(redis, key, _ACTIONS, actions, ttl=settings.discovery_ttl)
    return actions


async def _read[T](
    provider: ResolvedProvider,
    read: Callable[[ConnectorProviderRuntime], Awaitable[T]],
    *,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> T:
    """One bounded read with the provider's credential; a failure is `unavailable` with safe text."""
    try:
        with anyio.fail_after(settings.tool_call_seconds):
            async with open_connector_provider(
                provider,
                keys=keys,
                registry=registry,
                policy=policy,
                settings=settings,
                timeout=settings.tool_call_seconds,
            ) as runtime:
                return await read(runtime)
    except Exception as error:
        logger.warning(
            "Connector catalog read failed",
            extra={"provider_id": provider.id, "exception_details": exception_details(error)},
        )
        raise unavailable(f"connector:{provider.type}", error) from None


async def _apps(runtime: ConnectorProviderRuntime) -> list[ConnectorApp]:
    return sorted((_app(found) for found in await runtime.discover_connectors()), key=lambda app: app.key)


async def _resolve(storage: Storage, actor: Principal, workspace_id: str, provider_id: str) -> ResolvedProvider:
    # Reading the catalogue presents the provider's credential, as using the provider does.
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        return await resolve_provider(session, actor, ConnectorProviderRow, scope, provider_id, verb="run")


def _app(found: DiscoveredConnector) -> ConnectorApp:
    return ConnectorApp(
        key=found.key,
        name=found.name,
        description=found.description,
        logo_url=found.logo_url,
        unavailable_reason=found.unavailable_reason,
        authentication_methods=list(found.authentication_methods),
        setup_schema=dict(found.setup_schema),
    )
