from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

import httpx2
import pytest
from a13n_service.etags import resource_etag
from a13n_service.models.domain import CreateModelProviderRequest, UpdateModelProviderRequest
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.models import ModelProviderRecord
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.storage import short_session
from google.oauth2.credentials import Credentials
from pydantic import ValidationError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import ORG_ID, WORKSPACE_ID, actor, protector
from .test_runtime import _AllowEndpoints, _runtime_providers, _snapshot


@pytest.mark.anyio
async def test_extra_headers_rotate_independently_and_are_not_returned(
    provider_service: ModelProviderService, model_sessions: async_sessionmaker[AsyncSession]
) -> None:
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(
            type="deepseek",
            name="Gateway",
            credential="primary-key",
            configuration={"base_url": "https://gateway.example/deepseek"},
            extra_headers={"X-Gateway-Key": "gateway-secret", "X-Team": "research"},
        ),
    )
    assert "extra_headers" not in provider.configuration
    assert provider.header_names == ("x-gateway-key", "x-team")
    assert "gateway-secret" not in provider.model_dump_json()
    assert "primary-key" not in provider.model_dump_json()
    resolver = LiveProviderResolver(model_sessions, built_in_provider_registry(), _AllowEndpoints(), protector())

    async def resolved():
        return await resolver.resolve_provider(
            organization_id=ORG_ID, workspace_id=WORKSPACE_ID, provider_id=provider.id
        )

    first = await resolved()
    assert first.credential == "primary-key"
    assert first.extra_headers == {"x-team": "research", "x-gateway-key": "gateway-secret"}
    assert "gateway-secret" not in repr(first)
    provider = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(extra_headers={"X-Gateway-Key": "replacement", "X-Second": "second-secret"}),
    )
    assert (await resolved()).credential == "primary-key"
    assert (await resolved()).extra_headers["x-gateway-key"] == "replacement"
    provider = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(credential="rotated-primary"),
    )
    assert (await resolved()).extra_headers["x-second"] == "second-secret"
    provider = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(extra_headers={"x-gateway-key": None}),
    )
    assert provider.header_names == ("x-second", "x-team")
    assert (await resolved()).extra_headers == {"x-team": "research", "x-second": "second-secret"}
    async with short_session(model_sessions) as session:
        stored = await session.get(ModelProviderRecord, provider.id)
        assert stored is not None
        assert stored.credential_generation == 4
        assert b"second-secret" not in stored.ciphertext


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Key": "a", "x-key": "b"},
        {"Host": "evil.example"},
        {"x-key": "a\r\nb"},
        {"x key": "invalid"},
        {f"x-{i}": "value" for i in range(65)},
    ],
)
def test_secret_header_input_is_bounded(headers: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        CreateModelProviderRequest(type="openai", name="Invalid", extra_headers=headers)


@pytest.mark.parametrize(
    ("provider_type", "header"),
    [
        ("openai", "authorization"),
        ("anthropic", "x-api-key"),
        ("google_gemini", "x-goog-api-key"),
        ("aws_bedrock", "x-amz-date"),
        ("azure_openai", "api-key"),
    ],
)
def test_extra_headers_cannot_replace_native_authentication(provider_type: str, header: str) -> None:
    provider = _runtime_providers()[provider_type]
    with pytest.raises(ValueError):
        built_in_provider_registry().validate_provider(
            provider_type,
            provider.configuration,
            credential_configured=True,
            header_names=(header,),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider_type",
    [
        "openai",
        "deepseek",
        "moonshot",
        "minimax",
        "zhipu",
        "alibaba_model_studio",
        "openrouter",
        "ollama",
        "azure_openai",
        "anthropic",
        "google_gemini",
        "google_vertex",
    ],
)
async def test_native_inference_uses_custom_endpoint_and_headers_without_client_leaks(provider_type: str) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if provider_type == "anthropic":
            body = {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "test",
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        elif provider_type.startswith("google"):
            body = {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            }
        else:
            body = {
                "id": "reply",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "provider": "vendor",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            }
        return httpx2.Response(200, json=body)

    registry = built_in_provider_registry()
    integration = registry.integration(provider_type)
    original = _runtime_providers()[provider_type]
    api = next(
        (api for api in integration.supported_model_apis if api.endswith("chat_completions")),
        integration.supported_model_apis[0],
    )
    endpoint = "https://gateway.example/prefix"
    provider = replace(original, endpoint=endpoint, extra_headers={"x-team": "research", "x-gateway-key": "private"})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        factory = NativeModelFactory(client, registry, _AllowEndpoints())
        with patch(
            "a13n_service.models.provider_adapters.google_vertex.parse_google_service_account",
            return_value=Credentials(token="vertex-token"),
        ):
            native = await factory.build(_snapshot(api), provider)
            async with native:
                await native.request(
                    [ModelRequest(parts=[UserPromptPart(content="Hello")])], None, ModelRequestParameters()
                )
            assert "x-gateway-key" not in client.headers
            second = await factory.build(_snapshot(api), original)
            assert native.profile == second.profile
            async with second:
                await second.request(
                    [ModelRequest(parts=[UserPromptPart(content="Hello")])], None, ModelRequestParameters()
                )
    assert len(requests) == 2
    assert str(requests[0].url).startswith(endpoint + "/")
    assert requests[0].headers["x-team"] == "research"
    assert requests[0].headers["x-gateway-key"] == "private"
    assert "x-gateway-key" not in requests[1].headers
    if provider_type == "anthropic":
        assert requests[0].headers["x-api-key"] == "secret"
    elif provider_type == "google_gemini":
        assert requests[0].headers["x-goog-api-key"] == "secret"
    else:
        expected = (
            "vertex-token" if provider_type == "google_vertex" else "ollama" if provider_type == "ollama" else "secret"
        )
        assert requests[0].headers["authorization"] == f"Bearer {expected}"


@pytest.mark.anyio
@pytest.mark.parametrize("api", ["bedrock_mantle.responses", "bedrock_mantle.chat_completions"])
async def test_mantle_keeps_gateway_prefix_and_signed_headers(api: str) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(400, json={"error": {"message": "expected test stop", "type": "invalid_request_error"}})

    original = _runtime_providers()["aws_bedrock"]
    provider = replace(
        original,
        configuration={**original.configuration, "mantle_base_url": "https://gateway.example/aws/openai/v1"},
        extra_headers={"x-gateway-key": "private"},
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        native = await NativeModelFactory(client, built_in_provider_registry(), _AllowEndpoints()).build(
            _snapshot(api), provider
        )
        async with native:
            from pydantic_ai.exceptions import ModelHTTPError

            with pytest.raises(ModelHTTPError):
                await native.request(
                    [ModelRequest(parts=[UserPromptPart(content="Hello")])], None, ModelRequestParameters()
                )
    assert len(requests) == 1
    assert str(requests[0].url).startswith("https://gateway.example/aws/")
    assert requests[0].headers["x-gateway-key"] == "private"
    assert requests[0].headers["authorization"].startswith("AWS4-HMAC-SHA256")


@pytest.mark.anyio
async def test_converse_adds_headers_before_signing_at_custom_endpoint() -> None:
    from botocore.awsrequest import AWSResponse

    class Body:
        def stream(self, *_args, **_kwargs):
            yield json.dumps(
                {
                    "output": {"message": {"role": "assistant", "content": [{"text": "OK"}]}},
                    "stopReason": "end_turn",
                    "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                    "metrics": {"latencyMs": 1},
                }
            ).encode()

    requests = []

    def respond(request):
        requests.append(request)
        return AWSResponse(request.url, 200, {"content-type": "application/json"}, Body())

    provider = replace(
        _runtime_providers()["aws_bedrock"],
        endpoint="https://gateway.example/converse",
        extra_headers={"x-gateway-key": "private"},
    )
    async with httpx2.AsyncClient() as client:
        native = await NativeModelFactory(client, built_in_provider_registry(), _AllowEndpoints()).build(
            _snapshot("bedrock.converse"), provider
        )
        async with native:
            with patch.object(native.client._endpoint.http_session, "send", side_effect=respond):
                await native.request(
                    [ModelRequest(parts=[UserPromptPart(content="Hello")])], None, ModelRequestParameters()
                )
    assert len(requests) == 1
    assert requests[0].url.startswith("https://gateway.example/converse/")
    assert requests[0].headers["x-gateway-key"] == b"private"
    assert b"x-gateway-key" in requests[0].headers["Authorization"]


@pytest.mark.anyio
async def test_configuration_updates_do_not_decrypt_secrets_and_stale_updates_preserve_them(
    provider_service: ModelProviderService,
    model_sessions: async_sessionmaker[AsyncSession],
) -> None:
    from datetime import timedelta

    from a13n_service.models.service_common import ModelError

    from .conftest import NOW

    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(
            type="anthropic",
            name="Protected",
            credential="primary",
            extra_headers={"x-gateway": "secret"},
        ),
    )
    stale_etag = resource_etag(provider.id, provider.updated_at)
    with patch.object(provider_service, "_clock", return_value=NOW + timedelta(seconds=1)):
        with patch(
            "a13n_service.models.provider_service.CredentialSnapshot.decrypt",
            side_effect=AssertionError("must not decrypt"),
        ):
            renamed = await provider_service.update(
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                provider_id=provider.id,
                if_match=stale_etag,
                request=UpdateModelProviderRequest(name="Renamed"),
            )
        with pytest.raises(ModelError, match="changed"):
            await provider_service.update(
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                provider_id=provider.id,
                if_match=stale_etag,
                request=UpdateModelProviderRequest(extra_headers={"x-gateway": "stale"}),
            )
    assert renamed.header_names == ("x-gateway",)
    runtime = await LiveProviderResolver(
        model_sessions, built_in_provider_registry(), _AllowEndpoints(), protector()
    ).resolve_provider(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, provider_id=provider.id)
    assert runtime.extra_headers == {"x-gateway": "secret"}
    assert runtime.credential == "primary"


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://test.openai.azure.com", "https://test.openai.azure.com/openai/v1"),
        ("https://test.models.ai.azure.com", "https://test.models.ai.azure.com/v1"),
    ],
)
def test_azure_official_endpoint_defaults_preserve_native_v1_paths(endpoint: str, expected: str) -> None:
    registry = built_in_provider_registry()
    validated = registry.validate_provider("azure_openai", {"resource_endpoint": endpoint}, credential_configured=True)
    assert validated.endpoint == expected
    assert (
        registry.validate_provider("azure_openai", validated.configuration, credential_configured=True).endpoint
        == expected
    )
    with pytest.raises(ValueError, match="api_version"):
        registry.validate_provider(
            "azure_openai", {"resource_endpoint": endpoint, "api_version": "2024-10-21"}, credential_configured=True
        )


@pytest.mark.anyio
async def test_header_patch_can_replace_a_full_set_atomically(provider_service: ModelProviderService) -> None:
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(
            type="openai",
            name="Full headers",
            credential="primary",
            extra_headers={f"x-old-{i}": "value" for i in range(32)},
        ),
    )
    updated = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(
            extra_headers={**dict.fromkeys(provider.header_names), **{f"x-new-{i}": "value" for i in range(32)}},
        ),
    )
    assert set(updated.header_names) == {f"x-new-{i}" for i in range(32)}


@pytest.mark.parametrize("header", ["x-session-id", "x-litellm-session-id", "X-Custom-Affinity"])
def test_provider_affinity_is_optional_normalized_and_schema_has_presets(header):
    from a13n_service.models.provider_adapters.types import ProviderConfiguration

    registry = built_in_provider_registry()
    parsed = registry.validate_provider("openai", {"session_affinity_header": header}, credential_configured=True)
    assert parsed.configuration["session_affinity_header"] == header.lower()
    assert (
        "session_affinity_header"
        not in registry.validate_provider("openai", {}, credential_configured=True).configuration
    )
    schema = ProviderConfiguration.model_json_schema()
    assert "x-litellm-session-id" in json.dumps(schema)


@pytest.mark.parametrize(
    "configuration,headers",
    [
        ({"session_affinity_header": "x-title"}, ()),
        ({"session_affinity_header": "authorization"}, ()),
        ({"session_affinity_header": "x-custom", "auth_mode": "api_key_header", "api_key_header_name": "x-custom"}, ()),
        ({"session_affinity_header": "x-custom"}, ("x-custom",)),
        ({"session_affinity_header": "bad\r\nname"}, ()),
    ],
)
def test_affinity_cannot_collide_with_static_auth_or_protocol_headers(configuration, headers):
    with pytest.raises(ValueError):
        built_in_provider_registry().validate_provider(
            "openai",
            configuration,
            credential_configured=True,
            header_names=headers,
        )
