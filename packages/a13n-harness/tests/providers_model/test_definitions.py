from __future__ import annotations

import json
from unittest.mock import patch

import httpx2
import pytest
from a13n_harness.providers.authentication import Authentication, AuthenticationCase, CredentialMode
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from google.auth.credentials import AnonymousCredentials
from pydantic import BaseModel
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.bedrock_mantle import BedrockMantleChatModel, BedrockMantleResponsesModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.typesafe import TypeSafeModel
from pydantic_ai.providers.cerebras import CerebrasProvider
from pydantic_ai.providers.fireworks import FireworksProvider
from pydantic_ai.providers.sambanova import SambaNovaProvider
from pydantic_ai.providers.together import TogetherProvider
from pydantic_ai.providers.vercel import VercelProvider


class UnusedOAuthSource:
    async def load(self):
        raise AssertionError("Construction must not load OAuth credentials")

    async def rotate(self, expected, exchange):
        raise AssertionError("Construction must not refresh OAuth credentials")


class AllowEndpoints:
    async def validate(self, endpoint: str) -> str:
        return endpoint


@pytest.mark.anyio
async def test_every_builtin_constructs_its_declared_native_apis():
    expected = {
        "openai.responses": OpenAIResponsesModel,
        "openai.chat_completions": OpenAIChatModel,
        "anthropic.messages": AnthropicModel,
        "google.generate_content": GoogleModel,
        "bedrock.converse": BedrockConverseModel,
        "bedrock_mantle.responses": BedrockMantleResponsesModel,
        "bedrock_mantle.chat_completions": BedrockMantleChatModel,
        "openrouter.chat_completions": OpenRouterModel,
        "ollama.chat_completions": OllamaModel,
        "typesafe.system_one": TypeSafeModel,
        "mistral.chat_completions": MistralModel,
    }
    configurations = {
        "aws_bedrock": {"region": "us-east-1"},
        "azure_openai": {"base_url": "https://example.openai.azure.com/openai/v1"},
        "google_vertex": {"project_id": "project", "location": "us-central1"},
        "ollama": {"base_url": "http://localhost:11434"},
        "alibaba_model_studio": {"region": "ap-southeast-1", "domain_type": "international"},
    }
    credentials = {
        "aws_bedrock": {"aws_access_key_id": "access", "aws_secret_access_key": "secret"},
        "google_vertex": {"project_id": "project", "client_email": "fixture@example.com", "private_key": "fixture-pem"},
        "ollama": None,
        "openai_chatgpt": None,
    }
    async with httpx2.AsyncClient() as client:
        with patch(
            "a13n_harness.providers.model.credentials.GoogleServiceAccount.native_credentials",
            return_value=AnonymousCredentials(),
        ):
            for definition in BUILT_IN_MODEL_PROVIDERS:
                for api in definition.supported_model_apis:
                    name = {
                        "bedrock_mantle.responses": "openai.gpt-5.6-sol",
                        "bedrock_mantle.chat_completions": "openai.gpt-oss-safeguard-20b",
                    }.get(api, "provider/model")
                    model = await definition.build(
                        name,
                        configuration=configurations.get(definition.type, {}),
                        credential=credentials.get(definition.type, {"api_key": "secret"}),
                        credential_source=UnusedOAuthSource() if definition.oauth is not None else None,
                        model_api=api,
                        http_client=client,
                        endpoint_policy=AllowEndpoints(),
                    )
                    async with model:
                        assert isinstance(model, expected[api])
                        if isinstance(model, (OpenAIChatModel, OpenAIResponsesModel, AnthropicModel)):
                            assert model.client.max_retries == 0
                        if isinstance(model, BedrockConverseModel):
                            assert model.client.meta.config.retries["total_max_attempts"] == 1
    assert len(BUILT_IN_MODEL_PROVIDERS) == 22


class Configuration(BaseModel):
    access: str = "public"
    enabled: bool = True


@pytest.mark.parametrize(
    ("mode", "configured", "accepted"),
    [
        (mode, present, mode == CredentialMode.optional or (mode == CredentialMode.required) == present)
        for mode in CredentialMode
        for present in (False, True)
    ],
)
def test_authentication_presence(mode, configured, accepted):
    authentication = Authentication(mode=mode)
    if accepted:
        authentication.validate_presence(Configuration(), configured)
    else:
        with pytest.raises(ValueError):
            authentication.validate_presence(Configuration(), configured)


def test_authentication_conditions_use_defaults_and_exact_json_types():
    authentication = Authentication(
        cases=(
            AuthenticationCase(field="access", equals="public", mode=CredentialMode.forbidden),
            AuthenticationCase(field="enabled", equals=1, mode=CredentialMode.optional),
        )
    )
    authentication.validate_configuration_model(Configuration)
    assert authentication.resolve(Configuration()) is CredentialMode.forbidden
    assert authentication.resolve(Configuration(access="private")) is CredentialMode.required
    with pytest.raises(ValueError, match="duplicate"):
        Authentication(cases=(authentication.cases[0], authentication.cases[0]))
    with pytest.raises(ValueError, match="fields"):
        Authentication(
            cases=(AuthenticationCase(field="missing", equals="value", mode=CredentialMode.optional),)
        ).validate_configuration_model(Configuration)


def test_authentication_condition_uses_public_configuration_alias():
    from pydantic import Field

    class AliasedConfiguration(BaseModel):
        internal: str = Field(default="public", alias="access")

    authentication = Authentication(
        cases=(AuthenticationCase(field="access", equals="public", mode=CredentialMode.forbidden),)
    )
    authentication.validate_configuration_model(AliasedConfiguration)
    assert authentication.resolve(AliasedConfiguration()) is CredentialMode.forbidden


@pytest.mark.anyio
@pytest.mark.parametrize("value,expected", [(None, None), ("true", None), ("false", False)])
async def test_owned_bedrock_client_respects_operator_tls_without_overriding_default_ca_selection(
    monkeypatch, value, expected
):
    from botocore.session import Session

    monkeypatch.delenv("A13N_OUTBOUND_TLS_VERIFY", raising=False)
    if value is not None:
        monkeypatch.setenv("A13N_OUTBOUND_TLS_VERIFY", value)
    definition = next(item for item in BUILT_IN_MODEL_PROVIDERS if item.type == "aws_bedrock")
    original = Session.create_client
    captured = []

    def create(session, *args, **kwargs):
        captured.append(kwargs["verify"])
        return original(session, *args, **kwargs)

    monkeypatch.setattr(Session, "create_client", create)
    async with httpx2.AsyncClient() as client:
        model = await definition.build(
            "fixture-model",
            configuration={"region": "us-east-1"},
            credential={"aws_access_key_id": "fixture", "aws_secret_access_key": "fixture"},
            model_api="bedrock.converse",
            http_client=client,
            endpoint_policy=AllowEndpoints(),
        )
        async with model:
            assert isinstance(model, BedrockConverseModel)
    assert captured == [expected]


@pytest.mark.anyio
@pytest.mark.parametrize("base_url", [None, "https://gateway.example/custom/v1"])
@pytest.mark.parametrize(
    ("provider_type", "native_type", "model_name", "default_endpoint"),
    [
        ("cerebras", CerebrasProvider, "gpt-oss-120b", "https://api.cerebras.ai/v1"),
        ("sambanova", SambaNovaProvider, "DeepSeek-R1", "https://api.sambanova.ai/v1"),
        ("vercel", VercelProvider, "anthropic/claude-sonnet-4.6", "https://ai-gateway.vercel.sh/v1"),
        (
            "fireworks",
            FireworksProvider,
            "accounts/fireworks/models/deepseek-r1",
            "https://api.fireworks.ai/inference/v1",
        ),
        (
            "together",
            TogetherProvider,
            "deepseek-ai/DeepSeek-R1",
            "https://api.together.xyz/v1",
        ),
    ],
)
async def test_hosted_open_models_preserve_native_profiles_and_request_wiring(
    provider_type, native_type, model_name, default_endpoint, base_url
):
    definition = next(item for item in BUILT_IN_MODEL_PROVIDERS if item.type == provider_type)
    endpoint = base_url or default_endpoint
    requests = []

    async def handler(request):
        requests.append(request)
        assert str(request.url) == f"{endpoint}/chat/completions"
        assert request.headers["authorization"] == "Bearer fixture-key"
        assert request.headers["x-routing-key"] == "team-1"
        assert json.loads(request.content)["model"] == model_name
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": model_name,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await definition.build(
            model_name,
            configuration={"base_url": base_url} if base_url else {},
            credential={"api_key": "fixture-key"},
            http_client=client,
            extra_headers={"x-routing-key": "team-1"},
        )
        assert isinstance(model, OpenAIChatModel)
        assert isinstance(model.provider, native_type)
        assert model.system == provider_type
        assert str(model.provider.base_url).rstrip("/") == endpoint
        native_profile = native_type.model_profile(model_name)
        assert native_profile
        assert model.profile == OpenAIChatModel(model_name, provider=native_type(openai_client=model.client)).profile
        assert model.client.max_retries == 0
        async with model:
            response = await model.request([ModelRequest(parts=[UserPromptPart("Hi")])], None, ModelRequestParameters())
        assert response.parts[0].content == "Hello"
        assert not client.is_closed
    assert len(requests) == 1
