from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import httpx2
import pytest
from a13n_service.etags import resource_etag
from a13n_service.models.connection_test import NativeModelConnectionTester
from a13n_service.models.connection_test import test_connection as connection_test_result
from a13n_service.models.domain import (
    CreateModelProviderRequest,
    CreateModelRequest,
    ModelExecutionSnapshot,
    UpdateModelProviderRequest,
    UpdateModelRequest,
)
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.models import ModelProviderRecord
from a13n_service.models.provider_adapters.base import ProviderOperationError
from a13n_service.models.provider_runtime import LiveProviderResolver, RuntimeProvider
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.requests import LiveProviderModel
from a13n_service.models.service import ModelService
from a13n_service.models.service_common import ModelError
from a13n_service.storage import short_session
from google.auth.credentials import AnonymousCredentials
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.bedrock_mantle import BedrockMantleChatModel, BedrockMantleResponsesModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import ORG_ID, WORKSPACE_ID, actor, protector


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ModelHTTPError(401, "example", "secret upstream response"), "connection_failed"),
        (ProviderOperationError("secret upstream response"), "connection_failed"),
        (TimeoutError("secret upstream response"), "connection_timeout"),
    ],
)
async def test_connection_failure_returns_safe_result(error: Exception, code: str) -> None:
    async def operation() -> None:
        raise error

    result = await connection_test_result(operation(), timeout_seconds=1, subject="Model API")
    assert result.success is False
    assert result.code == code
    assert "secret" not in result.model_dump_json()


@pytest.mark.anyio
async def test_connection_programming_error_propagates() -> None:
    async def operation() -> None:
        raise TypeError("broken adapter")

    with pytest.raises(TypeError, match="broken adapter"):
        await connection_test_result(operation(), timeout_seconds=1, subject="Provider")


@pytest.mark.anyio
async def test_connection_test_sends_saved_settings_and_single_model_identity(
    provider_service: ModelProviderService,
    model_service: ModelService,
    model_sessions: async_sessionmaker[AsyncSession],
) -> None:
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openrouter", name="Router", credential="secret"),
    )
    model = await model_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="primary",
            provider_id=provider.id,
            name="Primary",
            upstream_model="team/unlisted-model",
            model_api="openrouter.chat_completions",
            settings={"temperature": 0.3, "max_tokens": 42, "openrouter_provider": {"only": ["vendor"]}},
        ),
    )
    requests = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx2.Response(
            200,
            json={
                "id": "reply",
                "object": "chat.completion",
                "created": 1,
                "model": "team/unlisted-model",
                "provider": "vendor",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            },
        )

    registry = built_in_provider_registry()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        tester = NativeModelConnectionTester(
            provider_resolver=LiveProviderResolver(model_sessions, registry, _AllowEndpoints(), protector()),
            model_factory=NativeModelFactory(client, registry, _AllowEndpoints()),
        )
        await tester(
            snapshot=ModelExecutionSnapshot.freeze(model),
            settings=model.settings,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
        )
    assert len(requests) == 1
    assert requests[0]["model"] == "team/unlisted-model"
    assert "models" not in requests[0]
    assert requests[0]["temperature"] == 0.3
    assert requests[0]["max_tokens"] == 42
    assert requests[0]["provider"]["only"] == ["vendor"]


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
            model_api="openai.responses",
        ),
    )
    snapshot = ModelExecutionSnapshot.freeze(model)
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
            model_api="openai.responses",
        ),
    )
    snapshot = ModelExecutionSnapshot.freeze(model)
    await model_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=model.id,
        if_match=resource_etag(model.id, model.updated_at),
        request=UpdateModelRequest(model_api="openai.chat_completions"),
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
        factory = NativeModelFactory(client, built_in_provider_registry(), _AllowEndpoints())
        with patch(
            "a13n_service.models.provider_adapters.google_vertex.parse_google_service_account",
            return_value=AnonymousCredentials(),
        ):
            for definition in built_in_provider_registry().definitions():
                for model_api in definition.supported_model_apis:
                    native = await factory.build(_snapshot(model_api), providers[definition.type])
                    async with native:
                        assert isinstance(native, expected_types[model_api])
                        if isinstance(native, (OpenAIResponsesModel, OpenAIChatModel, AnthropicModel)):
                            assert native.client.max_retries == 0
                        if isinstance(native, BedrockConverseModel):
                            assert native.client.meta.config.retries["total_max_attempts"] == 1


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
    }


@pytest.mark.anyio
async def test_switch_to_unauthenticated_provider_clears_material_and_advances_generation(
    provider_service: ModelProviderService,
    model_sessions: async_sessionmaker[AsyncSession],
) -> None:
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(
            type="openai",
            name="Optional credential",
            configuration={"base_url": "https://models.example/v1"},
            credential="private-token",
        ),
    )
    resolver = LiveProviderResolver(model_sessions, built_in_provider_registry(), _AllowEndpoints(), protector())
    resolved = await resolver.resolve_provider(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
    )
    assert resolved.credential == "private-token"
    renamed = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(name="Renamed"),
    )
    async with short_session(model_sessions) as session:
        stored = await session.get(ModelProviderRecord, provider.id)
        assert stored is not None and stored.credential_generation == 1
    cleared = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(renamed.id, renamed.updated_at),
        request=UpdateModelProviderRequest(
            credential=None,
            configuration={"base_url": "https://models.example/v1", "auth_mode": "none"},
        ),
    )
    assert not cleared.credential_configured
    async with short_session(model_sessions) as session:
        stored = await session.get(ModelProviderRecord, provider.id)
        assert stored is not None and stored.credential_generation == 2
        assert stored.ciphertext is stored.nonce is stored.encryption_key_id is None
    resolved = await resolver.resolve_provider(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
    )
    assert resolved.credential is None


@pytest.mark.anyio
@pytest.mark.parametrize("session_header", ["thread-current", "thread-other"])
async def test_only_current_harness_correlation_is_allowed_at_model_dispatch(session_header: str) -> None:
    native = TestModel()
    resolver = Mock(spec=LiveProviderResolver)
    resolver.resolve = AsyncMock(return_value=Mock())
    factory = Mock(spec=NativeModelFactory)
    factory.build.return_value = native
    model = LiveProviderModel(
        initial=native,
        snapshot=_snapshot("openai.responses"),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        provider_resolver=resolver,
        model_factory=factory,
        harness_thread_id="thread-current",
    )
    settings = {"extra_headers": {"x-session-id": session_header}}
    if session_header == "thread-current":
        await model.request([], settings, ModelRequestParameters())
        resolver.resolve.assert_awaited_once()
    else:
        with pytest.raises(ModelError):
            await model.request([], settings, ModelRequestParameters())
        resolver.resolve.assert_not_awaited()
    assert settings == {"extra_headers": {"x-session-id": session_header}}
