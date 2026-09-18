from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from a13n_harness.providers.model.definition import ProviderOperationError
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
from a13n_service.models.provider_runtime import LiveProviderResolver, ModelConnection
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.requests import LiveProviderModel
from a13n_service.models.service import ModelService
from a13n_service.models.service_common import ModelError
from a13n_service.storage import short_session
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
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
        request=CreateModelProviderRequest(type="openrouter", name="Router", credential={"api_key": "secret"}),
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
        request=CreateModelProviderRequest(type="openai", name="OpenAI", credential={"api_key": "first"}),
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
    ).credential.api_key.get_secret_value() == "first"
    provider = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(credential={"api_key": "second"}),
    )
    assert provider.credential_configured
    assert (
        await resolver.resolve(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, snapshot=snapshot)
    ).credential.api_key.get_secret_value() == "second"


@pytest.mark.anyio
async def test_model_snapshot_keeps_accepted_api_after_model_edit(
    provider_service: ModelProviderService,
    model_service: ModelService,
    model_sessions: async_sessionmaker[AsyncSession],
) -> None:
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="OpenAI", credential={"api_key": "secret"}),
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
async def test_factory_routes_openai_base_profile_through_explicit_openai_protocol() -> None:
    snapshot = ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef",
        model_key="relay",
        upstream_model="my-gpt-5",
        catalog_ref={"provider": "openai", "model": "gpt-5"},
        model_api="openai.chat_completions",
    )
    provider = (
        built_in_provider_registry()
        .integration("openai")
        .bind({**{}, "base_url": "https://api.openai.com/v1"}, {"api_key": "secret"})
    )
    async with httpx2.AsyncClient() as client:
        native = await NativeModelFactory(
            client,
            built_in_provider_registry(),
            _AllowEndpoints(),
        ).build(snapshot, provider)
        async with native:
            assert isinstance(native, OpenAIChatModel)
            assert native.model_name == "my-gpt-5"
            assert native.profile.get("supports_thinking") is True


@pytest.mark.anyio
async def test_factory_does_not_copy_anthropic_profile_to_openai_protocol() -> None:
    snapshot = ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef",
        model_key="relay",
        upstream_model="relay-claude-sonnet-4-5",
        catalog_ref={"provider": "anthropic", "model": "claude-sonnet-4-5"},
        model_api="openai.chat_completions",
    )
    provider = (
        built_in_provider_registry()
        .integration("openai")
        .bind({**{}, "base_url": "https://api.openai.com/v1"}, {"api_key": "secret"})
    )
    async with httpx2.AsyncClient() as client:
        native = await NativeModelFactory(
            client,
            built_in_provider_registry(),
            _AllowEndpoints(),
        ).build(snapshot, provider)
        async with native:
            assert isinstance(native, OpenAIChatModel)
            assert native.model_name == "relay-claude-sonnet-4-5"
            assert native.profile.get("supports_thinking") is False


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


def _runtime_providers() -> dict[str, ModelConnection]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        .decode()
    )
    aws_credential = {"aws_access_key_id": "access", "aws_secret_access_key": "secret"}
    return {
        "openai": built_in_provider_registry()
        .integration("openai")
        .bind({**{}, "base_url": "https://api.openai.com/v1"}, {"api_key": "secret"}),
        "anthropic": built_in_provider_registry()
        .integration("anthropic")
        .bind({**{}, "base_url": "https://api.anthropic.com"}, {"api_key": "secret"}),
        "google_gemini": built_in_provider_registry()
        .integration("google_gemini")
        .bind({**{}, "base_url": "https://generativelanguage.googleapis.com"}, {"api_key": "secret"}),
        "google_vertex": built_in_provider_registry()
        .integration("google_vertex")
        .bind(
            {"project_id": "project", "location": "us-central1"},
            {"project_id": "project", "client_email": "fixture@example.com", "private_key": private_key},
        ),
        "azure_openai": built_in_provider_registry()
        .integration("azure_openai")
        .bind(
            {
                **{"resource_endpoint": "https://test.openai.azure.com/openai/v1"},
                "base_url": "https://test.openai.azure.com/openai/v1",
            },
            {"api_key": "secret"},
        ),
        "aws_bedrock": built_in_provider_registry()
        .integration("aws_bedrock")
        .bind({"region": "us-east-1"}, aws_credential),
        "openrouter": built_in_provider_registry()
        .integration("openrouter")
        .bind({**{}, "base_url": "https://openrouter.ai/api/v1"}, {"api_key": "secret"}),
        "ollama": built_in_provider_registry()
        .integration("ollama")
        .bind({**{"base_url": "http://ollama.example/v1"}, "base_url": "http://ollama.example/v1"}, None),
        "alibaba_model_studio": built_in_provider_registry()
        .integration("alibaba_model_studio")
        .bind(
            {
                **{"region": "ap-southeast-1", "domain_type": "international"},
                "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            },
            {"api_key": "secret"},
        ),
        "deepseek": built_in_provider_registry()
        .integration("deepseek")
        .bind({**{}, "base_url": "https://api.deepseek.com"}, {"api_key": "secret"}),
        "moonshot": built_in_provider_registry()
        .integration("moonshot")
        .bind({**{}, "base_url": "https://api.moonshot.cn/v1"}, {"api_key": "secret"}),
        "minimax": built_in_provider_registry()
        .integration("minimax")
        .bind({**{}, "base_url": "https://api.minimax.io/v1"}, {"api_key": "secret"}),
        "zhipu": built_in_provider_registry()
        .integration("zhipu")
        .bind({**{}, "base_url": "https://open.bigmodel.cn/api/paas/v4"}, {"api_key": "secret"}),
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
            credential={"api_key": "private-token"},
        ),
    )
    resolver = LiveProviderResolver(model_sessions, built_in_provider_registry(), _AllowEndpoints(), protector())
    resolved = await resolver.resolve_provider(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
    )
    assert resolved.credential.api_key.get_secret_value() == "private-token"
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
async def test_caller_cannot_supply_even_current_thread_affinity_at_model_dispatch(session_header: str) -> None:
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
    with pytest.raises(ModelError):
        await model.request([], settings, ModelRequestParameters())
    resolver.resolve.assert_not_awaited()
    assert settings == {"extra_headers": {"x-session-id": session_header}}


@pytest.mark.anyio
async def test_credential_replacement_preserves_headers_without_parsing_discarded_primary(
    provider_service, model_sessions
):
    import json

    from a13n_service.storage import transaction

    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(
            type="openai",
            name="Replace primary",
            credential={"api_key": "first"},
            extra_headers={"x-gateway": "retained"},
        ),
    )
    async with transaction(model_sessions) as session:
        record = await session.get(ModelProviderRecord, provider.id)
        assert record is not None
        record.replace_credential(
            json.dumps({"credential": "discarded-string-value", "extra_headers": {"x-gateway": "retained"}}),
            protector(),
        )
    updated = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(credential={"api_key": "replacement"}),
    )
    assert updated.id == provider.id and updated.header_names == ("x-gateway",)
    async with short_session(model_sessions) as session:
        record = await session.get(ModelProviderRecord, provider.id)
        assert record is not None
        secret = record.credential_snapshot()
    assert json.loads(secret.decrypt(protector())) == {
        "credential": {"api_key": "replacement"},
        "extra_headers": {"x-gateway": "retained"},
    }
