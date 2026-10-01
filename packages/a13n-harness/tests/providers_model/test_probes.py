from dataclasses import replace

import httpx2
import pytest
from a13n_harness.providers.model import definition as model_definition
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.model.definition import ProviderOperationError
from a13n_harness.providers.model.types import ModelConnection
from anyio import sleep


def definition(kind):
    return next(item for item in BUILT_IN_MODEL_PROVIDERS if item.type == kind)


class _AllowEndpoints:
    async def validate(self, endpoint: str) -> str:
        return endpoint


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "payload", "expected_url", "expected_headers"),
    [
        *[
            (
                definition(kind).bind({}, {"api_key": "secret"}),
                {"data": [{"id": "fixture-model"}]},
                endpoint,
                {"authorization": "Bearer secret"},
            )
            for kind, endpoint in [
                ("cerebras", "https://api.cerebras.ai/v1/models"),
                ("sambanova", "https://api.sambanova.ai/v1/models"),
                ("mistral", "https://api.mistral.ai/v1/models"),
                ("xai", "https://api.x.ai/v1/models"),
            ]
        ],
        (
            definition("fireworks").bind({}, {"api_key": "secret"}),
            {"data": [{"id": "accounts/fireworks/models/llama-v3p3-70b-instruct"}]},
            "https://api.fireworks.ai/inference/v1/models",
            {"authorization": "Bearer secret"},
        ),
        (
            definition("together").bind({}, {"api_key": "secret"}),
            [{"id": "meta-llama/Llama-3.3-70B-Instruct-Turbo"}],
            "https://api.together.xyz/v1/models",
            {"authorization": "Bearer secret"},
        ),
        (
            definition("openrouter").bind({"base_url": "https://openrouter.ai/api/v1"}, {"api_key": "secret"}),
            {"data": [{"id": "anthropic/claude-next", "name": "Claude Next"}]},
            "https://openrouter.ai/api/v1/models",
            {"authorization": "Bearer secret"},
        ),
        (
            definition("ollama").bind({"base_url": "http://ollama.example/v1"}, None),
            {"models": [{"name": "llama-next"}]},
            "http://ollama.example/api/tags",
            {},
        ),
        (
            definition("openai").bind({"base_url": "https://api.openai.com/v1"}, {"api_key": "secret"}),
            {"data": [{"id": "gpt-next"}]},
            "https://api.openai.com/v1/models",
            {"authorization": "Bearer secret"},
        ),
        (
            definition("anthropic").bind({"base_url": "https://api.anthropic.com"}, {"api_key": "secret"}),
            {"data": [{"id": "claude-next", "display_name": "Claude Next"}]},
            "https://api.anthropic.com/v1/models",
            {"x-api-key": "secret", "anthropic-version": "2023-06-01"},
        ),
        (
            definition("google_gemini").bind(
                {"base_url": "https://generativelanguage.googleapis.com"}, {"api_key": "secret"}
            ),
            {"models": [{"name": "models/gemini-next", "displayName": "Gemini Next"}]},
            "https://generativelanguage.googleapis.com/v1beta/models",
            {"x-goog-api-key": "secret"},
        ),
        (
            definition("azure_openai").bind(
                {
                    "base_url": "https://example.openai.azure.com/openai/v1",
                },
                {"api_key": "secret"},
            ),
            {"data": [{"id": "gpt-next"}]},
            "https://example.openai.azure.com/openai/v1/models",
            {"authorization": "Bearer secret"},
        ),
        (
            definition("openai").bind(
                {
                    "base_url": "https://models.example/v1",
                    "auth_mode": "api_key_header",
                    "api_key_header_name": "x-model-key",
                },
                {"api_key": "secret"},
            ),
            {"data": [{"id": "custom-next"}]},
            "https://models.example/v1/models",
            {"x-model-key": "secret"},
        ),
    ],
)
async def test_provider_probe_uses_configured_endpoint_and_headers(
    provider: ModelConnection,
    payload: object,
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
        await definition(provider.type).probe(provider, http_client=client, endpoint_policy=_AllowEndpoints())


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
        provider = definition("openai").bind({"base_url": "https://models.example/v1"}, {"api_key": "secret"})
        await definition("openai").probe(provider, http_client=client, endpoint_policy=_AllowEndpoints())
    assert len(calls) == 1


@pytest.mark.anyio
async def test_probe_refuses_redirects_even_when_injected_client_follows_them():
    requests = []
    validated = []

    class Policy:
        async def validate(self, endpoint):
            validated.append(endpoint)
            return endpoint

    async def handler(request):
        requests.append(request)
        return httpx2.Response(302, headers={"location": "http://127.0.0.1/internal"})

    selected = definition("anthropic")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(ProviderOperationError, match=r"^Provider connection probe failed$"):
            await selected.probe(selected.bind({}, {"api_key": "secret"}), http_client=client, endpoint_policy=Policy())
    assert validated == ["https://api.anthropic.com/v1/models"]
    assert len(requests) == 1
    assert str(requests[0].url) == validated[0]
    assert requests[0].headers["x-api-key"] == "secret"


@pytest.mark.anyio
@pytest.mark.parametrize("slow_policy", [False, True])
async def test_probe_total_deadline_covers_policy_and_slow_drip_stream(monkeypatch, slow_policy):
    monkeypatch.setattr(model_definition, "_PROBE_TIMEOUT_SECONDS", 0.05)
    requests = []

    class Stream(httpx2.AsyncByteStream):
        closed = False
        chunks = 0

        async def __aiter__(self):
            for _ in range(1000):
                await sleep(0.01)
                self.chunks += 1
                yield b"x"

        async def aclose(self):
            self.closed = True

    stream = Stream()

    class Policy:
        async def validate(self, endpoint):
            if slow_policy:
                await sleep(10)
            return endpoint

    async def handler(request):
        requests.append(request)
        return httpx2.Response(200, stream=stream)

    selected = definition("anthropic")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ProviderOperationError, match=r"^Provider connection probe timed out$"):
            await selected.probe(selected.bind({}, {"api_key": "secret"}), http_client=client, endpoint_policy=Policy())
    if slow_policy:
        assert requests == []
    else:
        assert len(requests) == 1
        assert 0 < stream.chunks < 1000
        assert stream.closed
