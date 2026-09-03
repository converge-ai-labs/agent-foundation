from __future__ import annotations

import httpx2
import pytest
from a13n_service.models.provider_operations import NativeProviderOperations
from a13n_service.models.provider_runtime import RuntimeProvider
from a13n_service.models.providers import built_in_provider_registry


class _ProviderResolver:
    def __init__(self, provider: RuntimeProvider) -> None:
        self.provider = provider

    async def resolve_provider(self, **_: str) -> RuntimeProvider:
        return self.provider


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "payload", "expected", "suggests_api"),
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
        ),
    ],
)
async def test_provider_native_discovery_is_advisory(
    provider: RuntimeProvider,
    payload: dict[str, object],
    expected: str,
    suggests_api: bool,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if provider.type == "openrouter":
            assert request.headers["authorization"] == "Bearer secret"
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
