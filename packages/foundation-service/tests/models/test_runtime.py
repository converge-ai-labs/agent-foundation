from __future__ import annotations

import json
from unittest.mock import patch

import httpx2
import pytest
from a13n_service.etags import resource_etag
from a13n_service.models.domain import (
    CreateModelProviderRequest,
    CreateModelRequest,
    ModelExecutionSnapshot,
    UpdateModelProviderRequest,
    UpdateModelRequest,
)
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_runtime import LiveProviderResolver, RuntimeProvider
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.service import ModelService
from google.auth.credentials import AnonymousCredentials
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.bedrock_mantle import BedrockMantleChatModel, BedrockMantleResponsesModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import ORG_ID, WORKSPACE_ID, actor, protector


class _AllowEndpoints:
    async def validate(self, value: str, *, resolve_dns: bool) -> str:
        del resolve_dns
        return value


@pytest.mark.anyio
async def test_provider_credential_rotation_is_visible_to_same_model_snapshot(
    provider_service: ModelProviderService,
    model_service: ModelService,
    model_sessions: async_sessionmaker[AsyncSession],
) -> None:
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="OpenAI", credential="first"),
    )
    model = await model_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="primary",
            provider_id=provider.id,
            name="Primary",
            upstream_model="gpt-current",
            model_apis=({"api": "openai.responses"},),
        ),
    )
    snapshot = ModelExecutionSnapshot.freeze(model, "openai.responses")
    resolver = LiveProviderResolver(
        model_sessions,
        built_in_provider_registry(),
        _AllowEndpoints(),
        protector(),
    )

    assert (
        await resolver.resolve(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, snapshot=snapshot)
    ).credential == "first"
    provider = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(credential="second"),
    )
    assert provider.credential_configured
    assert (
        await resolver.resolve(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, snapshot=snapshot)
    ).credential == "second"


@pytest.mark.anyio
async def test_model_snapshot_keeps_accepted_api_after_model_edit(
    provider_service: ModelProviderService,
    model_service: ModelService,
    model_sessions: async_sessionmaker[AsyncSession],
) -> None:
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="OpenAI", credential="secret"),
    )
    model = await model_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="primary",
            provider_id=provider.id,
            name="Primary",
            upstream_model="gpt-current",
            model_apis=({"api": "openai.responses"},),
        ),
    )
    snapshot = ModelExecutionSnapshot.freeze(model, "openai.responses")
    await model_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=model.id,
        if_match=resource_etag(model.id, model.updated_at),
        request=UpdateModelRequest(model_apis=({"api": "openai.chat_completions"},)),
    )
    resolver = LiveProviderResolver(
        model_sessions,
        built_in_provider_registry(),
        _AllowEndpoints(),
        protector(),
    )

    resolved = await resolver.resolve(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, snapshot=snapshot)

    assert resolved.type == "openai"


@pytest.mark.anyio
async def test_factory_uses_explicit_calling_api_binding() -> None:
    expected_types = {
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
    providers = _runtime_providers()
    async with httpx2.AsyncClient() as client:
        factory = NativeModelFactory(client, built_in_provider_registry())
        with patch(
            "a13n_service.models.provider_adapters.google_vertex.parse_google_service_account",
            return_value=AnonymousCredentials(),
        ):
            for definition in built_in_provider_registry().definitions():
                for model_api in definition.supported_model_apis:
                    assert isinstance(
                        factory.build(_snapshot(model_api), providers[definition.key]), expected_types[model_api]
                    )


def _snapshot(api: str) -> ModelExecutionSnapshot:
    upstream_model = "provider/model"
    if api == "bedrock_mantle.responses":
        upstream_model = "openai.gpt-5.6-sol"
    elif api == "bedrock_mantle.chat_completions":
        upstream_model = "openai.gpt-oss-safeguard-20b"
    return ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef",
        model_key="primary",
        upstream_model=upstream_model,
        model_api=api,
    )


def _runtime_providers() -> dict[str, RuntimeProvider]:
    aws_credential = json.dumps({"aws_access_key_id": "access", "aws_secret_access_key": "secret"})
    return {
        "openai": RuntimeProvider("openai", {}, "https://api.openai.com/v1", "secret"),
        "anthropic": RuntimeProvider("anthropic", {}, "https://api.anthropic.com", "secret"),
        "google_gemini": RuntimeProvider("google_gemini", {}, "https://generativelanguage.googleapis.com", "secret"),
        "google_vertex": RuntimeProvider(
            "google_vertex", {"project_id": "project", "location": "us-central1"}, None, "{}"
        ),
        "azure_openai": RuntimeProvider(
            "azure_openai",
            {"resource_endpoint": "https://test.openai.azure.com/openai/v1"},
            "https://test.openai.azure.com/openai/v1",
            "secret",
        ),
        "aws_bedrock": RuntimeProvider("aws_bedrock", {"region": "us-east-1"}, None, aws_credential),
        "openrouter": RuntimeProvider("openrouter", {}, "https://openrouter.ai/api/v1", "secret"),
        "ollama": RuntimeProvider("ollama", {"base_url": "http://ollama.example/v1"}, "http://ollama.example/v1", None),
        "alibaba_model_studio": RuntimeProvider(
            "alibaba_model_studio",
            {"region": "ap-southeast-1", "domain_type": "international"},
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "secret",
        ),
        "deepseek": RuntimeProvider("deepseek", {}, "https://api.deepseek.com", "secret"),
        "moonshot": RuntimeProvider("moonshot", {}, "https://api.moonshot.cn/v1", "secret"),
        "zhipu": RuntimeProvider("zhipu", {}, "https://open.bigmodel.cn/api/paas/v4", "secret"),
        "openai_compatible": RuntimeProvider(
            "openai_compatible",
            {"base_url": "https://models.example/v1", "auth_mode": "bearer"},
            "https://models.example/v1",
            "secret",
        ),
    }
