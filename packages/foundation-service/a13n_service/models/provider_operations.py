"""Provider-native connection checks and complete, bounded catalog enumeration."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx2

from .descriptions import describe_model
from .provider_adapters.base import DiscoveredModelIdentity
from .provider_adapters.types import RuntimeProvider
from .providers import ModelDescription, ModelDescriptionCollection, ProviderRegistry

_MAX_DISCOVERY_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_DISCOVERY_PAGES = 100


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
        return ModelDescriptionCollection(
            items=tuple(
                describe_model(
                    self._registry,
                    provider.type,
                    item.upstream_model,
                    display_name=item.display_name,
                    metadata=item.metadata,
                )
                for item in identities
            )
        )

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
        except Exception:
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
            raise ValueError("Provider model discovery is unsupported")
        request = discovery.request(provider)
        params: dict[str, str] = {}
        seen_tokens: set[str] = set()
        indexed: dict[str, DiscoveredModelIdentity] = {}
        for _ in range(_MAX_DISCOVERY_PAGES):
            async with self._http_client.stream(
                "GET", request.url, headers=request.headers, params=params, timeout=10
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > _MAX_DISCOVERY_RESPONSE_BYTES:
                        raise ValueError("the Provider model-list response is too large")
                    body.extend(chunk)
            payload = json.loads(body)
            for item in discovery.parse(payload):
                indexed[item.upstream_model] = item
                if len(indexed) > 10000:
                    raise ValueError("the Provider catalog exceeds the bounded model count")
            params = _next_page(payload, provider.type)
            if not params:
                return [indexed[key] for key in sorted(indexed)]
            token = json.dumps(params, sort_keys=True)
            if token in seen_tokens:
                raise ValueError("the Provider repeated its continuation token")
            seen_tokens.add(token)
        raise ValueError("the Provider catalog exceeds the bounded enumeration budget")


def _next_page(payload: dict[str, Any], provider_type: str) -> dict[str, str]:
    # Reuse the adapter-owned URL, never an upstream next-page URL carrying credentials.
    token = payload.get("nextPageToken")
    if isinstance(token, str) and token:
        return {"pageToken": token}
    if payload.get("has_more"):
        last = payload.get("last_id")
        if not isinstance(last, str) or not last:
            raise ValueError("the Provider omitted its continuation token")
        return {"after_id" if provider_type == "anthropic" else "after": last}
    if payload.get("next") or payload.get("next_cursor") or payload.get("nextLink"):
        raise ValueError("the Provider returned an unsupported continuation format")
    return {}
