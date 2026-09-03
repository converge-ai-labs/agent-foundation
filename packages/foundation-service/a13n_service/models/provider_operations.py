"""Provider-native connection testing and advisory model discovery."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import httpx2

from .domain import ModelApiConfig
from .provider_runtime import RuntimeProvider
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

    async def _list(self, provider: RuntimeProvider) -> list[tuple[str, str | None]]:
        url, headers = _model_list_request(provider)
        async with self._http_client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _MAX_DISCOVERY_RESPONSE_BYTES:
                    raise ValueError("the Provider model-list response is too large")
                body.extend(chunk)
        payload = json.loads(body)
        return _parse_models(provider.type, payload)


def _model_list_request(provider: RuntimeProvider) -> tuple[str, dict[str, str]]:
    credential = provider.credential
    if provider.endpoint is None:
        raise ValueError("the Provider does not expose HTTP model discovery")
    if provider.type == "anthropic":
        return _join(provider.endpoint, "v1/models"), {
            "x-api-key": _require_credential(credential),
            "anthropic-version": "2023-06-01",
        }
    if provider.type == "google_gemini":
        return _join(provider.endpoint, "v1beta/models"), {"x-goog-api-key": _require_credential(credential)}
    if provider.type == "ollama":
        endpoint = provider.endpoint.removesuffix("/v1")
        return _join(endpoint, "api/tags"), {}
    if provider.type == "azure_openai":
        return _join(provider.endpoint, "models"), {"api-key": _require_credential(credential)}
    headers: dict[str, str] = {}
    if credential:
        if provider.type == "openai_compatible" and provider.config.get("auth_mode") == "api_key_header":
            headers[str(provider.config["api_key_header_name"])] = credential
        else:
            headers["authorization"] = f"Bearer {credential}"
    return _join(provider.endpoint, "models"), headers


def _parse_models(provider_type: str, payload: Any) -> list[tuple[str, str | None]]:
    if not isinstance(payload, Mapping):
        raise ValueError("the Provider model-list response is invalid")
    key = "models" if provider_type in {"google_gemini", "ollama"} else "data"
    values = payload.get(key)
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError("the Provider model-list response is invalid")
    parsed: list[tuple[str, str | None]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        raw_id = value.get("name") if provider_type in {"google_gemini", "ollama"} else value.get("id")
        if not isinstance(raw_id, str):
            continue
        model_id = (raw_id.removeprefix("models/") if provider_type == "google_gemini" else raw_id).strip()
        if not 1 <= len(model_id) <= 256:
            continue
        raw_display = value.get("displayName") or value.get("name")
        display_name = raw_display.strip() if isinstance(raw_display, str) else None
        if display_name == model_id or (display_name is not None and not 1 <= len(display_name) <= 128):
            display_name = None
        parsed.append((model_id, display_name))
    return parsed


def _join(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _require_credential(value: str | None) -> str:
    if not value:
        raise ValueError("the Provider credential is unavailable")
    return value
