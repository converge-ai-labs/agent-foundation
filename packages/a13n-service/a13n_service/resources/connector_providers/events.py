"""Optional event adapters and cached provider-owned event definitions."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import anyio
from a13n_harness.providers.connector.definition import ConnectorProviderDefinition
from a13n_harness.providers.connector.events import EventProvider, EventType, HasEvents
from pydantic import TypeAdapter

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import conflict
from a13n_service.providers.tools.discovery import cache, cached, unavailable
from a13n_service.resources.connector_providers.catalog import open_connector_provider
from a13n_service.resources.providers.service import ResolvedProvider, resolve_provider
from a13n_service.resources.providers.tables import ConnectorProviderRow
from a13n_service.resources.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

_TYPES = TypeAdapter(list[EventType])


@asynccontextmanager
async def open_events(runtime: Runtime, provider: ResolvedProvider) -> AsyncGenerator[EventProvider]:
    definition = runtime.registry.get("connector", provider.type)
    if not isinstance(definition, ConnectorProviderDefinition) or definition.verify_event is None:
        raise conflict("connector_provider", provider.id, "events_unsupported")
    settings = runtime.settings.providers
    with anyio.fail_after(settings.tool_call_seconds):
        async with open_connector_provider(
            provider,
            keys=runtime.keys,
            registry=runtime.registry,
            policy=runtime.endpoint_policy,
            settings=settings,
            timeout=settings.operation_seconds,
        ) as opened:
            if not isinstance(opened, HasEvents):
                raise conflict("connector_provider", provider.id, "events_unsupported")
            yield opened.events


async def definitions(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    app: str,
    *,
    key: str | None = None,
    refresh: bool = False,
) -> list[EventType]:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        provider = await resolve_provider(session, actor, ConnectorProviderRow, scope, provider_id)
    cache_key = f"provider:{provider.type}:events:{provider.id}:{provider.version}:{app}:{key or '*'}"
    found = None if refresh else await cached(runtime.redis, cache_key, _TYPES)
    if found is not None:
        return found
    try:
        async with open_events(runtime, provider) as adapter:
            result = [await adapter.event_type(app, key)] if key else await adapter.event_types(app)
    except Exception as error:
        raise unavailable(f"connector:{provider.type}", error) from None
    await cache(runtime.redis, cache_key, _TYPES, result, ttl=runtime.settings.providers.discovery_ttl)
    return result
