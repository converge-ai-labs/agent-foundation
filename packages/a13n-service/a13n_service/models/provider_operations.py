"""Bounded connection probes, without model discovery or enumeration."""

from __future__ import annotations

from typing import Protocol

import httpx2

from .provider_adapters.base import ProviderOperationError, ProviderOperationUnsupported
from .provider_adapters.types import RuntimeProvider
from .providers import ProviderRegistry


class ProviderStateResolver(Protocol):
    async def resolve_provider(
        self, *, provider_id: str, organization_id: str, workspace_id: str | None
    ) -> RuntimeProvider: ...


class NativeProviderOperations:
    def __init__(
        self, *, provider_resolver: ProviderStateResolver, registry: ProviderRegistry, http_client: httpx2.AsyncClient
    ) -> None:
        self._provider_resolver = provider_resolver
        self._registry = registry
        self._http_client = http_client

    async def test(self, *, provider_id: str, organization_id: str, workspace_id: str | None) -> None:
        provider = await self._provider_resolver.resolve_provider(
            provider_id=provider_id, organization_id=organization_id, workspace_id=workspace_id
        )
        probe = self._registry.integration(provider.type).connection_probe
        if probe is None:
            raise ProviderOperationUnsupported("Test a saved Model to verify this connection")
        request = probe(provider)
        try:
            async with self._http_client.stream(
                "GET", request.url, headers={**provider.extra_headers, **request.headers}, timeout=10
            ) as response:
                response.raise_for_status()
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise ProviderOperationError("Provider probe response exceeds its size limit")
        except httpx2.HTTPError as error:
            raise ProviderOperationError("Provider connection probe failed") from error
