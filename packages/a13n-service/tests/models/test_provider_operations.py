from __future__ import annotations

from dataclasses import replace

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
    ("provider", "payload", "expected", "suggests_api", "expected_url", "expected_headers"),
    [
        (
            RuntimeProvider(
                type="openrouter",
                configuration={},
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
                configuration={"base_url": "http://ollama.example/v1"},
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
                configuration={},
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
            {"authorization": "Bearer secret"},
        ),
        (
            RuntimeProvider(
                "openai",
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
async def test_provider_probe_uses_configured_endpoint_and_headers(
    provider: RuntimeProvider,
    payload: dict[str, object],
    expected: str,
    suggests_api: bool,
    expected_url: str,
    expected_headers: dict[str, str],
) -> None:
    provider = replace(provider, extra_headers={"x-routing-key": "private-routing-value"})

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["x-routing-key"] == "private-routing-value"
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
        await operations.test(
            provider_id="mprov_1234567890abcdef",
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
        )


@pytest.mark.anyio
async def test_probe_stops_after_first_page():
    calls = []

    async def handler(request):
        calls.append(request)
        assert "after" not in request.url.params
        return httpx2.Response(
            200, json={"data": [{"id": "target", "name": "Target"}], "has_more": True, "last_id": "target"}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        ops = NativeProviderOperations(
            provider_resolver=_ProviderResolver(RuntimeProvider("openai", {}, "https://models.example/v1", "secret")),
            registry=built_in_provider_registry(),
            http_client=client,
        )
        await ops.test(provider_id="p", organization_id="o", workspace_id="w")
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("provider_type", ["google_vertex", "aws_bedrock"])
async def test_missing_provider_probe_is_unsupported_not_a_connection_failure(provider_type):
    from a13n_service.models.connection_test import test_connection

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: pytest.fail("must not dispatch"))
    ) as client:
        ops = NativeProviderOperations(
            provider_resolver=_ProviderResolver(RuntimeProvider(provider_type, {}, None, "secret")),
            registry=built_in_provider_registry(),
            http_client=client,
        )
        result = await test_connection(
            ops.test(provider_id="p", organization_id="o", workspace_id="w"), timeout_seconds=1, subject="Provider"
        )
    assert result.code == "connection_test_unsupported"
    assert result.success is False
