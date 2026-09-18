"""Bounded connection probes, without model discovery or enumeration."""

from __future__ import annotations

from typing import Protocol

import httpx2
from a13n_harness.providers.model.definition import EndpointValidator
from a13n_harness.providers.model.types import ModelConnection

from .providers import ProviderRegistry


class ProviderStateResolver(Protocol):
    async def resolve_provider(
        self, *, provider_id: str, organization_id: str, workspace_id: str | None
    ) -> ModelConnection: ...


class NativeProviderOperations:
    def __init__(
        self,
        *,
        provider_resolver: ProviderStateResolver,
        registry: ProviderRegistry,
        http_client: httpx2.AsyncClient,
        endpoint_policy: EndpointValidator,
    ) -> None:
        self._endpoint_policy = endpoint_policy
        self._provider_resolver = provider_resolver
        self._registry = registry
        self._http_client = http_client

    async def test(self, *, provider_id: str, organization_id: str, workspace_id: str | None) -> None:
        provider = await self._provider_resolver.resolve_provider(
            provider_id=provider_id, organization_id=organization_id, workspace_id=workspace_id
        )
        await self._registry.integration(provider.type).probe(
            provider, http_client=self._http_client, endpoint_policy=self._endpoint_policy
        )
