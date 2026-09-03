"""Provider-native connection testing and advisory model discovery orchestration."""

from __future__ import annotations

import json
from typing import Protocol

import httpx2

from .domain import ModelApiConfig
from .provider_adapters.base import DiscoveredModelIdentity
from .provider_adapters.types import RuntimeProvider
from .providers import DiscoveredModel, DiscoveredModelCollection, ProviderRegistry

_MAX_DISCOVERY_RESPONSE_BYTES = 4 * 1024 * 1024


class ProviderStateResolver(Protocol):
    async def resolve_provider(
        self,
        *,
        provider_id: str,
        organization_id: str,
        workspace_id: str,
    ) -> RuntimeProvider: ...


class NativeProviderOperations:
    """Use bounded first-party model-list APIs without turning catalogs into authority."""

    def __init__(
        self,
        *,
        provider_resolver: ProviderStateResolver,
        registry: ProviderRegistry,
        http_client: httpx2.AsyncClient,
    ) -> None:
        self._provider_resolver = provider_resolver
        self._registry = registry
        self._http_client = http_client

    async def test(self, *, provider_id: str, organization_id: str, workspace_id: str) -> None:
        provider = await self._resolve(provider_id, organization_id, workspace_id)
        await self._list(provider)

    async def discover(
        self,
        *,
        provider_id: str,
        organization_id: str,
        workspace_id: str,
    ) -> DiscoveredModelCollection:
        provider = await self._resolve(provider_id, organization_id, workspace_id)
        identifiers = await self._list(provider)
        apis = self._registry.definition(provider.type).supported_model_apis
        suggestions = (ModelApiConfig(api=apis[0]),) if len(apis) == 1 else ()
        return DiscoveredModelCollection(
            items=tuple(
                DiscoveredModel(
                    upstream_model=model_id,
                    display_name=display_name,
                    suggested_model_apis=suggestions,
                )
                for model_id, display_name in identifiers[:500]
            )
        )

    async def _resolve(self, provider_id: str, organization_id: str, workspace_id: str) -> RuntimeProvider:
        return await self._provider_resolver.resolve_provider(
            provider_id=provider_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )

    async def _list(self, provider: RuntimeProvider) -> list[DiscoveredModelIdentity]:
        discovery = self._registry.integration(provider.type).model_discovery
        if discovery is None:
            raise ValueError(f"Provider type {provider.type!r} does not support model discovery")
        request = discovery.request(provider)
        async with self._http_client.stream("GET", request.url, headers=request.headers) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _MAX_DISCOVERY_RESPONSE_BYTES:
                    raise ValueError("the Provider model-list response is too large")
                body.extend(chunk)
        return discovery.parse(json.loads(body))
