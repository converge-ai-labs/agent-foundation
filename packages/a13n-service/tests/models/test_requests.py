from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import anyio
import httpx2
import pytest
from a13n_harness.errors import ModelResolutionError
from a13n_service.etags import resource_etag
from a13n_service.models.domain import (
    CreateModelProviderRequest,
    CreateModelRequest,
    ModelExecutionSnapshot,
    UpdateModelProviderRequest,
    UpdateModelRequest,
)
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_adapters.types import RuntimeProvider
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.requests import LiveProviderModel
from a13n_service.models.service_common import ModelError
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition

from .conftest import ORG_ID, WORKSPACE_ID, actor, protector


class _AllowEndpoints:
    async def validate(self, endpoint: str, *, resolve_dns: bool) -> str:
        return endpoint


def _snapshot() -> ModelExecutionSnapshot:
    return ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef",
        model_key="primary",
        upstream_model="example-model",
        model_api="openai.chat_completions",
    )


def _reply(streaming: bool = False) -> httpx2.Response:
    if streaming:
        chunk = {
            "id": "reply",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "example-model",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        }
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode(),
        )
    return httpx2.Response(
        200,
        json={
            "id": "reply",
            "object": "chat.completion",
            "created": 1,
            "model": "example-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        },
    )


async def _request(model, *, streaming=False, settings=None, parameters=None):
    messages = [ModelRequest(parts=[UserPromptPart("test")])]
    parameters = parameters or ModelRequestParameters()
    if streaming:
        async with model.request_stream(messages, settings, parameters) as stream:
            async for _ in stream:
                pass
    else:
        await model.request(messages, settings, parameters)


async def _live_model(client):
    resolver = Mock(spec=LiveProviderResolver)
    resolver.resolve = AsyncMock(return_value=RuntimeProvider("openai", {}, "https://api.openai.com/v1", "test-key"))
    factory = NativeModelFactory(client, built_in_provider_registry(), _AllowEndpoints())
    return await LiveProviderModel.create(
        snapshot=_snapshot(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        provider_resolver=resolver,
        model_factory=factory,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("change", ["rotate", "disable_provider", "disable_model"])
async def test_each_retry_observes_current_database_state(
    provider_service, model_service, model_sessions, streaming, change
):
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="Account", credential="first"),
    )
    saved = await model_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="primary",
            provider_id=provider.id,
            name="Primary",
            upstream_model="example-model",
            model_api="openai.chat_completions",
        ),
    )
    sent = []

    async def handler(request):
        sent.append(request.headers["authorization"])
        if len(sent) == 1:
            if change == "disable_model":
                await model_service.update(
                    actor=actor(),
                    workspace_id=WORKSPACE_ID,
                    model_id=saved.id,
                    if_match=resource_etag(saved.id, saved.updated_at),
                    request=UpdateModelRequest(enabled=False),
                )
            else:
                update = (
                    UpdateModelProviderRequest(credential="second")
                    if change == "rotate"
                    else UpdateModelProviderRequest(enabled=False)
                )
                await provider_service.update(
                    actor=actor(),
                    workspace_id=WORKSPACE_ID,
                    provider_id=provider.id,
                    if_match=resource_etag(provider.id, provider.updated_at),
                    request=update,
                )
            return httpx2.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "busy"}})
        return _reply(streaming)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        registry = built_in_provider_registry()
        model = await LiveProviderModel.create(
            snapshot=ModelExecutionSnapshot.freeze(saved),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            provider_resolver=LiveProviderResolver(model_sessions, registry, _AllowEndpoints(), protector()),
            model_factory=NativeModelFactory(client, registry, _AllowEndpoints()),
        )
        if change == "rotate":
            await _request(model, streaming=streaming)
            assert sent == ["Bearer first", "Bearer second"]
        else:
            with pytest.raises(ModelResolutionError):
                await _request(model, streaming=streaming)
            assert sent == ["Bearer first"]


@pytest.mark.anyio
@pytest.mark.parametrize("status,attempts", [(429, 3), (503, 3), (401, 1), (500, 1)])
async def test_native_sdk_has_no_hidden_retries(status, attempts):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx2.Response(status, headers={"retry-after": "0"}, json={"error": {"message": "failure"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await _live_model(client)
        with pytest.raises(ModelHTTPError):
            await _request(model)
    assert len(sent) == attempts


@pytest.mark.anyio
async def test_free_extensions_reach_wire_but_cannot_override_tool_control():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return _reply()

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await _live_model(client)
        parameters = ModelRequestParameters(
            output_mode="tool",
            allow_text_output=False,
            output_tools=[ToolDefinition(name="final_result", kind="output")],
        )
        settings = {
            "temperature": 0.2,
            "extra_body": {"temperature": 0.7, "new_parameter": {"model": "nested-label", "schema": "opaque"}},
        }
        await _request(model, settings=settings, parameters=parameters)
        with pytest.raises(ModelError):
            await _request(model, settings={"extra_body": {"tool_choice": "none"}}, parameters=parameters)
    assert len(sent) == 1
    assert sent[0]["temperature"] == 0.7
    assert sent[0]["new_parameter"] == settings["extra_body"]["new_parameter"]
    assert sent[0]["tool_choice"] == "required"


@pytest.mark.anyio
async def test_stream_failure_after_handoff_is_not_replayed():
    native = TestModel()
    resolver = Mock(spec=LiveProviderResolver)
    resolver.resolve = AsyncMock(return_value=Mock())
    factory = Mock(spec=NativeModelFactory)
    factory.build.return_value = native
    model = LiveProviderModel(
        initial=native,
        snapshot=_snapshot(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        provider_resolver=resolver,
        model_factory=factory,
    )
    with pytest.raises(ModelHTTPError):
        async with model.request_stream([], {}, ModelRequestParameters()):
            raise ModelHTTPError(503, "example-model")
    resolver.resolve.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_request_deadline_includes_retry_wait(streaming):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx2.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "busy"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await _live_model(client)
        with pytest.raises(TimeoutError):
            await _request(model, streaming=streaming, settings={"timeout": 0.05})
    assert len(sent) == 1


@pytest.mark.anyio
async def test_unknown_transport_outcome_is_not_replayed():
    sent = []

    def handler(request):
        sent.append(request)
        raise httpx2.ReadTimeout("unknown outcome", request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await _live_model(client)
        with pytest.raises(ModelAPIError):
            await _request(model)
    assert len(sent) == 1


@pytest.mark.anyio
async def test_cancellation_during_retry_wait_stops_further_dispatch():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx2.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "busy"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await _live_model(client)
        with anyio.move_on_after(0.05) as scope:
            await _request(model)
        assert scope.cancel_called
    assert len(sent) == 1


@pytest.mark.anyio
async def test_long_retry_after_is_not_retried_early():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx2.Response(429, headers={"retry-after": "120"}, json={"error": {"message": "busy"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await _live_model(client)
        with pytest.raises(ModelHTTPError):
            await _request(model)
    assert len(sent) == 1


@pytest.mark.anyio
async def test_native_endpoint_validation_closes_bedrock_client_on_rejection():
    import threading
    from unittest.mock import patch

    from a13n_service.endpoint_policy import EndpointPolicyError
    from a13n_service.models.provider_adapters import aws_bedrock

    main_thread = threading.get_ident()
    client = Mock()
    client.meta.endpoint_url = "https://blocked.example"
    threads = []

    def build(*args, **kwargs):
        threads.append(threading.get_ident())
        return client

    policy = Mock()
    policy.validate = AsyncMock(side_effect=EndpointPolicyError("blocked"))
    snapshot = _snapshot().model_copy(update={"model_api": "bedrock.converse"})
    provider = RuntimeProvider(
        "aws_bedrock",
        {"region": "us-east-1"},
        None,
        json.dumps({"aws_access_key_id": "test", "aws_secret_access_key": "test"}),
    )
    session = Mock()
    session.create_client.side_effect = build
    with patch.object(aws_bedrock, "get_session", return_value=session):
        async with httpx2.AsyncClient() as http_client:
            factory = NativeModelFactory(http_client, built_in_provider_registry(), policy)
            with pytest.raises(ModelResolutionError):
                await factory.build(snapshot, provider)
    assert threads and threads[0] != main_thread
    policy.validate.assert_awaited_once_with("https://blocked.example", resolve_dns=True)
    client.close.assert_called_once()
