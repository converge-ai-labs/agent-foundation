"""Provider-native connection checks and complete, bounded catalog enumeration."""

from __future__ import annotations

import json
from typing import Protocol

import httpx2
from a13n_harness.errors import ModelResolutionError

from .descriptions import describe_model
from .domain import ModelDescription, ModelDescriptionCollection
from .provider_adapters.base import DiscoveredModelIdentity, ProviderOperationError
from .provider_adapters.types import RuntimeProvider
from .providers import ProviderRegistry

_MAX_DISCOVERY_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_DISCOVERY_PAGES = 100
_MAX_DISCOVERY_MODELS = 10_000
_MAX_DISCOVERY_OUTPUT_BYTES = 32 * 1024 * 1024


class ProviderStateResolver(Protocol):
    async def resolve_provider(
        self, *, provider_id: str, organization_id: str, workspace_id: str
    ) -> RuntimeProvider: ...


class NativeProviderOperations:
    def __init__(
        self, *, provider_resolver: ProviderStateResolver, registry: ProviderRegistry, http_client: httpx2.AsyncClient
    ) -> None:
        self._provider_resolver = provider_resolver
        self._registry = registry
        self._http_client = http_client

    async def test(self, *, provider_id: str, organization_id: str, workspace_id: str) -> None:
        provider = await self._resolve(provider_id, organization_id, workspace_id)
        await self._list(provider)

    async def discover(
        self, *, provider_id: str, organization_id: str, workspace_id: str
    ) -> ModelDescriptionCollection:
        provider = await self._resolve(provider_id, organization_id, workspace_id)
        identities = await self._list(provider)
        items: list[ModelDescription] = []
        output_bytes = len(b'{"items":[]}')
        for item in identities:
            description = describe_model(
                self._registry,
                provider.type,
                item.upstream_model,
                display_name=item.display_name,
                metadata=item.metadata,
            )
            output_bytes += len(description.model_dump_json().encode()) + bool(items)
            if output_bytes > _MAX_DISCOVERY_OUTPUT_BYTES:
                raise ProviderOperationError("the Provider catalog exceeds the output byte limit")
            items.append(description)
        return ModelDescriptionCollection(items=tuple(items))

    async def describe(
        self,
        *,
        provider_id: str,
        organization_id: str,
        workspace_id: str,
        provider_type: str,
        upstream_model: str,
        model_api: str | None,
    ) -> ModelDescription:
        # Validate the requested binding before attempting optional remote metadata.
        local = describe_model(self._registry, provider_type, upstream_model, model_api=model_api)
        try:
            provider = await self._resolve(provider_id, organization_id, workspace_id)
            identities = await self._list(provider)
            match = next((item for item in identities if item.upstream_model == upstream_model), None)
            if match is not None:
                return describe_model(
                    self._registry,
                    provider_type,
                    upstream_model,
                    model_api=model_api,
                    display_name=match.display_name,
                    metadata=match.metadata,
                )
        except (ProviderOperationError, ModelResolutionError, TimeoutError):
            # Metadata is advisory; cancellation (BaseException) still propagates.
            return local
        return local

    async def _resolve(self, provider_id: str, organization_id: str, workspace_id: str) -> RuntimeProvider:
        return await self._provider_resolver.resolve_provider(
            provider_id=provider_id, organization_id=organization_id, workspace_id=workspace_id
        )

    async def _list(self, provider: RuntimeProvider) -> list[DiscoveredModelIdentity]:
        discovery = self._registry.integration(provider.type).model_discovery
        if discovery is None:
            raise ProviderOperationError("Provider model discovery is unsupported")
        request = discovery.request(provider)
        params: dict[str, str] = {}
        seen_tokens: set[str] = set()
        indexed: dict[str, DiscoveredModelIdentity] = {}
        for _ in range(_MAX_DISCOVERY_PAGES):
            try:
                async with self._http_client.stream(
                    "GET", request.url, headers=request.headers, params=params, timeout=10
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > _MAX_DISCOVERY_RESPONSE_BYTES:
                            raise ProviderOperationError("the Provider model-list response is too large")
                        body.extend(chunk)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ProviderOperationError("the Provider model-list response is invalid")
            except (httpx2.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ProviderOperationError("the Provider model-list request failed") from error
            for item in discovery.parse(payload):
                indexed[item.upstream_model] = item
                if len(indexed) > _MAX_DISCOVERY_MODELS:
                    raise ProviderOperationError("the Provider catalog exceeds the bounded model count")
            params = discovery.next_page(payload)
            if not params:
                return [indexed[key] for key in sorted(indexed)]
            token = json.dumps(params, sort_keys=True)
            if token in seen_tokens:
                raise ProviderOperationError("the Provider repeated its continuation token")
            seen_tokens.add(token)
        raise ProviderOperationError("the Provider catalog exceeds the bounded enumeration budget")
