from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock

import anyio
import httpx2
import pytest
from a13n_harness.errors import ModelResolutionError
from a13n_service.etags import resource_etag
from a13n_service.models import requests as model_requests
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
            "provider": "OpenAI",
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
            "provider": "OpenAI",
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


async def _live_model(client, *, provider_type="openai", harness_thread_id=None, configuration=None):
    resolver = Mock(spec=LiveProviderResolver)
    resolver.resolve = AsyncMock(
        return_value=RuntimeProvider(provider_type, configuration or {}, "https://api.openai.com/v1", "test-key")
    )
    factory = NativeModelFactory(client, built_in_provider_registry(), _AllowEndpoints())
    return await LiveProviderModel.create(
        snapshot=_snapshot().model_copy(
            update={
                "model_api": f"{provider_type}.chat_completions",
                "upstream_model": "openai/gpt-4.1-mini" if provider_type == "openrouter" else "example-model",
            }
        ),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        provider_resolver=resolver,
        model_factory=factory,
        harness_thread_id=harness_thread_id,
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
async def test_request_deadline_includes_retry_wait(streaming, monkeypatch):
    sent = []
    active_deadlines = []
    retry_delays = []

    @contextmanager
    def capture_deadline(timeout):
        with anyio.fail_after(timeout) as scope:
            active_deadlines.append(scope)
            try:
                yield scope
            finally:
                active_deadlines.pop()

    async def expire_during_retry(delay):
        retry_delays.append(delay)
        assert len(sent) == 1
        assert active_deadlines
        scope = active_deadlines[-1]
        assert scope.deadline != float("inf")
        # Expire the real request deadline only after reaching retry backoff.
        scope.deadline = anyio.current_time()
        await anyio.sleep(delay)

    monkeypatch.setattr(model_requests, "fail_after", capture_deadline)
    monkeypatch.setattr(model_requests, "sleep", expire_during_retry)

    def handler(request):
        sent.append(request)
        return httpx2.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "busy"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await _live_model(client)
        with pytest.raises(TimeoutError):
            await _request(model, streaming=streaming, settings={"timeout": 30})
    assert retry_delays == [1]
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


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_openrouter_accepts_only_exact_harness_correlation_defaults(streaming):
    sent = []

    def handler(request):
        sent.append(request)
        return _reply(streaming)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await _live_model(
            client,
            provider_type="openrouter",
            harness_thread_id="thr_current",
            configuration={"session_affinity_header": "x-conversation-id"},
        )
        settings = {"openai_prompt_cache_key": "thr_current"}
        await _request(model, streaming=streaming, settings=settings)
        assert sent[0].headers["x-conversation-id"] == "thr_current"
        assert "x-session-id" not in sent[0].headers
        for header in ("x-conversation-id", "x-session-id"):
            with pytest.raises(ModelError):
                await _request(model, settings={"extra_headers": {header: "thr_current"}})
        assert json.loads(sent[0].content)["prompt_cache_key"] == "thr_current"
        assert settings["openai_prompt_cache_key"] == "thr_current"
        with pytest.raises(ModelError):
            await _request(model, streaming=streaming, settings={"openai_prompt_cache_key": "caller-value"})
        unbound = await _live_model(client, provider_type="openrouter")
        with pytest.raises(ModelError):
            await _request(unbound, streaming=streaming, settings=settings)
        assert len(sent) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("replacement", ["x-conversation-id", None])
async def test_retry_refreshes_affinity_header_with_endpoint(
    provider_service, model_service, model_sessions, streaming, replacement
):
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(
            type="openai",
            name="Gateway",
            credential="test-key",
            configuration={"base_url": "https://first.example/v1", "session_affinity_header": "x-session-id"},
        ),
    )
    saved = await model_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="affinity",
            provider_id=provider.id,
            name="Affinity",
            upstream_model="example-model",
            model_api="openai.chat_completions",
        ),
    )
    sent = []

    async def handler(request):
        sent.append(request)
        if len(sent) == 1:
            await provider_service.update(
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                provider_id=provider.id,
                if_match=resource_etag(provider.id, provider.updated_at),
                request=UpdateModelProviderRequest(
                    configuration={
                        "base_url": "https://second.example/v1",
                        "session_affinity_header": replacement,
                    }
                ),
            )
            return httpx2.Response(503, headers={"retry-after": "0"}, json={"error": {"message": "busy"}})
        return _reply(streaming)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        registry = built_in_provider_registry()
        model = await LiveProviderModel.create(
            snapshot=ModelExecutionSnapshot.freeze(saved),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            provider_resolver=LiveProviderResolver(model_sessions, registry, _AllowEndpoints(), protector()),
            model_factory=NativeModelFactory(client, registry, _AllowEndpoints()),
            harness_thread_id="thr_current",
        )
        settings = {"extra_headers": {"http-referer": "https://app.example"}}
        await _request(model, streaming=streaming, settings=settings)
    assert [request.url.host for request in sent] == ["first.example", "second.example"]
    assert sent[0].headers["x-session-id"] == "thr_current"
    assert "x-session-id" not in sent[1].headers
    if replacement:
        assert sent[1].headers[replacement] == "thr_current"
    else:
        assert "x-conversation-id" not in sent[1].headers
    assert settings == {"extra_headers": {"http-referer": "https://app.example"}}
    assert all(request.headers["http-referer"] == "https://app.example" for request in sent)
