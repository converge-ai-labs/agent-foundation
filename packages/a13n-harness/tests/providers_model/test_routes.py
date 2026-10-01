from __future__ import annotations

import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_harness.providers.model import routes
from a13n_harness.providers.model.credentials import ApiKeyCredential, GoogleServiceAccount
from a13n_harness.providers.model.routes import ROUTE_ALIASES, ROUTES, build_api_key_model
from pydantic import ValidationError
from pydantic_ai.models.google import GoogleModel


@pytest.mark.anyio
# The gemini, google-gla and google-vertex aliases share one lookup into google-cloud; keep one alias.
@pytest.mark.parametrize("provider", ["google", "gemini", "google-cloud"])
async def test_google_routes_preserve_developer_and_cloud_transports(provider):
    model = await build_api_key_model(
        f"{provider}:gemini-2.5-pro", ApiKeyCredential(api_key="fixture"), base_url="https://127.0.0.1"
    )
    async with model:
        assert isinstance(model, GoogleModel)
        assert model.client.vertexai is (provider != "google")
        # The SDK deadline follows the shared model transport, not httpx's five-second default.
        assert model.client._api_client.get_read_only_http_options()["timeout"] == 600_000


def test_routes_share_inference_aliases_without_conflating_calling_apis():
    from a13n_harness.models.inference import ROUTE_ALIASES as inference_aliases

    assert ROUTE_ALIASES is inference_aliases
    assert ROUTE_ALIASES == {
        "openai": "openai-responses",
        "gemini": "google-cloud",
        "google-gla": "google-cloud",
        "google-vertex": "google-cloud",
    }
    assert "google" not in ROUTE_ALIASES
    assert "openai-chat" not in ROUTE_ALIASES


def test_credentials_reject_blank_keys_and_invalid_service_account_pem():
    with pytest.raises(ValidationError, match="API key must not be blank"):
        ApiKeyCredential(api_key="  ")
    with pytest.raises(ValidationError, match="valid service-account key"):
        GoogleServiceAccount(project_id="fixture", client_email="fixture@example.com", private_key="invalid-pem")


# The default endpoints are where API keys are sent, so they are pinned here literally.
@pytest.mark.parametrize(
    ("name", "provider_type", "model_api", "default_base_url"),
    [
        ("openai", "openai", "openai.responses", None),
        ("openai-chat", "openai", "openai.chat_completions", None),
        ("anthropic", "anthropic", "anthropic.messages", None),
        ("zai", "zhipu", "openai.chat_completions", "https://api.z.ai/api/paas/v4"),
        ("moonshotai", "moonshot", "openai.chat_completions", "https://api.moonshot.ai/v1"),
        ("fireworks", "fireworks", "openai.chat_completions", None),
        ("together", "together", "openai.chat_completions", None),
        ("cerebras", "cerebras", "openai.chat_completions", None),
        ("sambanova", "sambanova", "openai.chat_completions", None),
        ("vercel", "vercel", "openai.chat_completions", None),
        ("mistral", "mistral", "mistral.chat_completions", None),
        ("grok", "xai", "openai.chat_completions", "https://api.x.ai/v1"),
    ],
)
def test_declared_routes_own_their_provider_api_and_endpoint(name, provider_type, model_api, default_base_url):
    route = ROUTES[ROUTE_ALIASES.get(name, name)]
    assert (route.provider_type, route.model_api, route.default_base_url) == (
        provider_type,
        model_api,
        default_base_url,
    )


class _AllowEndpoint:
    def __init__(self, **kwargs: object) -> None:
        del kwargs

    async def validate(self, endpoint: str) -> str:
        return endpoint


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "model_type"),
    [("zai", "ZaiModel"), ("moonshotai", "OpenAIChatModel"), ("grok", "OpenAIChatModel")],
)
async def test_routes_without_a_host_endpoint_use_their_declared_default(monkeypatch, name, model_type):
    monkeypatch.setattr(routes, "EndpointPolicy", _AllowEndpoint)
    model = await build_api_key_model(f"{name}:model-1", ApiKeyCredential(api_key="fixture"))
    async with model:
        assert type(model).__name__ == model_type
        assert model.provider is not None
        assert str(model.provider.base_url).rstrip("/") == ROUTES[name].default_base_url


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["fireworks", "together", "cerebras", "sambanova", "vercel", "grok", "mistral"])
async def test_hosted_open_model_routes_enforce_allowed_hosts_and_own_client_lifetime(provider):
    configuration = RunConfiguration(allowed_hosts=frozenset({"gateway.example"}))
    with pytest.raises(EndpointPolicyError):
        await build_api_key_model(
            f"{provider}:test-model", ApiKeyCredential(api_key="fixture"), configuration=configuration
        )

    model = await build_api_key_model(
        f"{provider}:test-model",
        ApiKeyCredential(api_key="fixture"),
        base_url="https://gateway.example/v1",
        configuration=configuration,
    )
    assert model.provider is not None
    assert model.provider.name == ("xai" if provider == "grok" else provider)
    assert str(model.provider.base_url).rstrip("/") == "https://gateway.example/v1"
    for _ in range(2):
        async with model:
            if provider == "mistral":
                client = model.provider.client.sdk_configuration.async_client
                assert not client.is_closed
            else:
                client = model.provider.client
                assert not client.is_closed()
        assert client.is_closed if provider == "mistral" else client.is_closed()
