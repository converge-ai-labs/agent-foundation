from __future__ import annotations

from unittest.mock import patch

import httpx2
import pytest
from a13n_harness.providers.authentication import Authentication, AuthenticationCase, CredentialMode
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from google.auth.credentials import AnonymousCredentials
from pydantic import BaseModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.bedrock_mantle import BedrockMantleChatModel, BedrockMantleResponsesModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel


class AllowEndpoints:
    async def validate(self, endpoint: str, *, resolve_dns: bool) -> str:
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
    assert len(BUILT_IN_MODEL_PROVIDERS) == 13


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


@pytest.mark.parametrize("kind", ["model", "web"])
def test_optional_setup_metadata_has_one_contract(kind):
    from dataclasses import replace

    from a13n_harness.providers.web.builtins import built_in_web_providers

    original = BUILT_IN_MODEL_PROVIDERS[0] if kind == "model" else built_in_web_providers()[0]
    assert replace(original, setup_url=None, setup_label=None).setup_url is None
    with pytest.raises(ValueError, match="invalid setup label"):
        replace(original, setup_url=None, setup_label="Configure access")
    with pytest.raises(ValueError, match="invalid setup URL"):
        replace(original, setup_url="http://example.com")
