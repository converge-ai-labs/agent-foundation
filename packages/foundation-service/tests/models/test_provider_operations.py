from __future__ import annotations

import httpx2
import pytest
from a13n_service.models.provider_adapters.registry import BUILT_IN_PROVIDER_ADAPTERS
from a13n_service.models.provider_operations import NativeProviderOperations
from a13n_service.models.provider_runtime import RuntimeProvider
from a13n_service.models.providers import built_in_provider_registry


class _ProviderResolver:
    def __init__(self, provider: RuntimeProvider) -> None:
        self.provider = provider

    async def resolve_provider(self, **_: str) -> RuntimeProvider:
        return self.provider


def test_provider_registry_and_adapters_stay_in_sync() -> None:
    definitions = {definition.key: definition for definition in built_in_provider_registry().definitions()}

    assert BUILT_IN_PROVIDER_ADAPTERS.keys() == definitions.keys()
    assert {
        provider_type
        for provider_type, adapter in BUILT_IN_PROVIDER_ADAPTERS.items()
        if adapter.model_discovery is not None
    } == {provider_type for provider_type, definition in definitions.items() if definition.supports_model_discovery}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "payload", "expected", "suggests_api", "expected_url", "expected_headers"),
    [
        (
            RuntimeProvider(
                type="openrouter",
                config={},
                endpoint="https://openrouter.ai/api/v1",
                credential="secret",
            ),
            {"data": [{"id": "anthropic/claude-next", "name": "Claude Next"}]},
            "anthropic/claude-next",
            True,
            "https://openrouter.ai/api/v1/models",
            {"authorization": "Bearer secret"},
        ),
        (
            RuntimeProvider(
                type="ollama",
                config={"base_url": "http://ollama.example/v1"},
                endpoint="http://ollama.example/v1",
                credential=None,
            ),
            {"models": [{"name": "llama-next"}]},
            "llama-next",
            True,
            "http://ollama.example/api/tags",
            {},
        ),
        (
            RuntimeProvider(
                type="openai",
                config={},
                endpoint="https://api.openai.com/v1",
                credential="secret",
            ),
            {"data": [{"id": "gpt-next"}]},
            "gpt-next",
            False,
            "https://api.openai.com/v1/models",
            {"authorization": "Bearer secret"},
        ),
        (
            RuntimeProvider("anthropic", {}, "https://api.anthropic.com", "secret"),
            {"data": [{"id": "claude-next", "display_name": "Claude Next"}]},
            "claude-next",
            True,
            "https://api.anthropic.com/v1/models",
            {"x-api-key": "secret", "anthropic-version": "2023-06-01"},
        ),
        (
            RuntimeProvider("google_gemini", {}, "https://generativelanguage.googleapis.com", "secret"),
            {"models": [{"name": "models/gemini-next", "displayName": "Gemini Next"}]},
            "gemini-next",
            True,
            "https://generativelanguage.googleapis.com/v1beta/models",
            {"x-goog-api-key": "secret"},
        ),
        (
            RuntimeProvider(
                "azure_openai",
                {"resource_endpoint": "https://example.openai.azure.com/openai/v1"},
                "https://example.openai.azure.com/openai/v1",
                "secret",
            ),
            {"data": [{"id": "gpt-next"}]},
            "gpt-next",
            False,
            "https://example.openai.azure.com/openai/v1/models",
            {"api-key": "secret"},
        ),
        (
            RuntimeProvider(
                "openai_compatible",
                {
                    "base_url": "https://models.example/v1",
                    "auth_mode": "api_key_header",
                    "api_key_header_name": "x-model-key",
                },
                "https://models.example/v1",
                "secret",
            ),
            {"data": [{"id": "custom-next"}]},
            "custom-next",
            False,
            "https://models.example/v1/models",
            {"x-model-key": "secret"},
        ),
    ],
)
async def test_provider_native_discovery_is_advisory(
    provider: RuntimeProvider,
    payload: dict[str, object],
    expected: str,
    suggests_api: bool,
    expected_url: str,
    expected_headers: dict[str, str],
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == expected_url
        for name, value in expected_headers.items():
            assert request.headers[name] == value
        if not expected_headers:
            assert "authorization" not in request.headers
        return httpx2.Response(200, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        operations = NativeProviderOperations(
            provider_resolver=_ProviderResolver(provider),
            registry=built_in_provider_registry(),
            http_client=client,
        )
        result = await operations.discover(
            provider_id="mprov_1234567890abcdef",
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
        )

    assert result.items[0].upstream_model == expected
    assert bool(result.items[0].suggested_model_apis) is suggests_api
