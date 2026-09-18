from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.requests import LiveProviderModel
from pydantic_ai import Agent


class _AllowEndpoints:
    async def validate(self, endpoint: str, *, resolve_dns: bool = False) -> str:
        return endpoint


def _response(output: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "custom-model",
        "output": output,
        "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    }


def _message() -> dict[str, object]:
    return {
        "type": "message",
        "id": "msg_test",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "OK", "annotations": []}],
    }


def _stream_response() -> httpx2.Response:
    response = _response([_message()])
    events = [
        {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**_message(), "status": "in_progress", "content": []},
        },
        {
            "type": "response.content_part.added",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_test",
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
        {
            "type": "response.output_text.delta",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_test",
            "delta": "OK",
        },
        {"type": "response.output_item.done", "output_index": 0, "item": _message()},
        {"type": "response.completed", "response": response},
    ]
    body = "".join(
        f"event: {event['type']}\ndata: {json.dumps({**event, 'sequence_number': i})}\n\n"
        for i, event in enumerate(events)
    )
    return httpx2.Response(200, text=body + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"})


def _chat_response(*, streaming: bool) -> httpx2.Response:
    if streaming:
        chunk = {
            "id": "chat_test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "my-deepseek-reasoner",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        }
        return httpx2.Response(
            200,
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode(),
            headers={"content-type": "text/event-stream"},
        )
    return httpx2.Response(
        200,
        json={
            "id": "chat_test",
            "object": "chat.completion",
            "created": 1,
            "model": "my-deepseek-reasoner",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        },
    )


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["none", "bearer", "api_key_header"])
async def test_custom_responses_endpoint_inference(mode: str, stream: bool) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        response = _response([_message()])
        if not stream:
            if len(requests) == 1:
                response = _response(
                    [
                        {
                            "type": "function_call",
                            "id": "fc_test",
                            "call_id": "call_test",
                            "name": "check",
                            "arguments": "{}",
                            "status": "completed",
                        }
                    ]
                )
            return httpx2.Response(200, json=response)
        return _stream_response()

    registry = built_in_provider_registry()
    configuration: dict[str, object] = {"base_url": "https://custom.example/v1", "auth_mode": mode}
    if mode == "api_key_header":
        configuration["api_key_header_name"] = "x-model-key"
    validated = registry.validate_provider("openai", configuration, credential_configured=mode != "none")
    provider = (
        built_in_provider_registry()
        .integration("openai")
        .bind(
            {**validated.configuration, "base_url": validated.endpoint},
            None if mode == "none" else {"api_key": "test-secret"},
            extra_headers={"x-gateway-key": "private-routing-value"},
        )
    )
    snapshot = ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef", model_key="custom", upstream_model="custom-model", model_api="openai.responses"
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = await NativeModelFactory(client, registry, _AllowEndpoints()).build(snapshot, provider)
        agent = Agent(model)

        @agent.tool_plain
        def check() -> str:
            return "checked"

        async with model:
            if stream:
                async with agent.run_stream("Check and reply OK") as result:
                    assert await result.get_output() == "OK"
            else:
                assert (await agent.run("Check and reply OK")).output == "OK"

    assert len(requests) == (1 if stream else 2)
    for request in requests:
        assert str(request.url) == "https://custom.example/v1/responses"
        assert request.method == "POST"
        assert request.headers["x-gateway-key"] == "private-routing-value"
        assert b"private-routing-value" not in request.content
        body = json.loads(request.content)
        assert body["model"] == "custom-model"
        assert body["stream"] is stream
        assert "messages" not in body
        assert request.headers.get("authorization") == ("Bearer test-secret" if mode == "bearer" else None)
        assert request.headers.get("x-model-key") == ("test-secret" if mode == "api_key_header" else None)
    if not stream:
        assert any(
            item.get("type") == "function_call_output" and item["output"] == "checked"
            for item in json.loads(requests[1].content)["input"]
        )


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_base_model_profile_preserves_relay_identity_endpoint_and_thinking_across_retry(
    streaming: bool,
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "busy"}})
        return _stream_response() if streaming else httpx2.Response(200, json=_response([_message()]))

    registry = built_in_provider_registry()
    validated = registry.validate_provider(
        "openai",
        {"base_url": "https://relay.example/v1"},
        credential_configured=True,
    )
    provider = (
        built_in_provider_registry()
        .integration("openai")
        .bind({**validated.configuration, "base_url": validated.endpoint}, {"api_key": "relay-secret"})
    )
    resolver = Mock(spec=LiveProviderResolver)
    resolver.resolve = AsyncMock(return_value=provider)
    snapshot = ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef",
        model_key="relay",
        upstream_model="my-gpt-5-5",
        catalog_ref={"provider": "openai", "model": "gpt-5.5"},
        model_api="openai.responses",
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = await LiveProviderModel.create(
            snapshot=snapshot,
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
            provider_resolver=resolver,
            model_factory=NativeModelFactory(client, registry, _AllowEndpoints()),
        )
        assert model.profile.get("supports_thinking") is True
        agent = Agent(model)
        if streaming:
            async with agent.run_stream("Reply OK", model_settings={"thinking": "high"}) as result:
                output = await result.get_output()
        else:
            output = (await agent.run("Reply OK", model_settings={"thinking": "high"})).output

    assert output == "OK"
    assert len(requests) == 2
    assert resolver.resolve.await_count == 3  # prototype plus one fresh Model per attempt
    for request in requests:
        assert str(request.url) == "https://relay.example/v1/responses"
        assert request.headers["authorization"] == "Bearer relay-secret"
        body = json.loads(request.content)
        assert body["model"] == "my-gpt-5-5"
        assert body["reasoning"]["effort"] == "high"


@pytest.mark.anyio
async def test_openai_base_profile_routes_thinking_through_explicit_chat_override() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "chat_test",
                "object": "chat.completion",
                "created": 1,
                "model": "my-gpt-5",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            },
        )

    registry = built_in_provider_registry()
    validated = registry.validate_provider(
        "openai",
        {"base_url": "https://relay.example/v1"},
        credential_configured=True,
    )
    provider = (
        built_in_provider_registry()
        .integration("openai")
        .bind({**validated.configuration, "base_url": validated.endpoint}, {"api_key": "relay-secret"})
    )
    snapshot = ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef",
        model_key="relay-chat",
        upstream_model="my-gpt-5",
        catalog_ref={"provider": "openai", "model": "gpt-5"},
        model_api="openai.chat_completions",
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = await NativeModelFactory(client, registry, _AllowEndpoints()).build(snapshot, provider)
        result = await Agent(model).run("Reply OK", model_settings={"thinking": "high"})

    assert result.output == "OK"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://relay.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer relay-secret"
    body = json.loads(request.content)
    assert body["model"] == "my-gpt-5"
    assert body["reasoning_effort"] == "high"


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_deepseek_base_profile_survives_openai_relay_agent_lifecycle(streaming: bool) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _chat_response(streaming=streaming)

    registry = built_in_provider_registry()
    validated = registry.validate_provider(
        "openai",
        {"base_url": "https://relay.example/v1"},
        credential_configured=True,
    )
    provider = (
        built_in_provider_registry()
        .integration("openai")
        .bind({**validated.configuration, "base_url": validated.endpoint}, {"api_key": "relay-secret"})
    )
    snapshot = ModelExecutionSnapshot(
        model_id="mdl_1234567890abcdef",
        model_key="relay-deepseek",
        upstream_model="my-deepseek-reasoner",
        catalog_ref={"provider": "deepseek", "model": "deepseek-reasoner"},
        model_api="openai.chat_completions",
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = await NativeModelFactory(client, registry, _AllowEndpoints()).build(snapshot, provider)
        assert model.profile["supports_thinking"] is True
        assert model.profile["thinking_always_enabled"] is True
        assert dict(model.profile)["openai_chat_thinking_field"] == "reasoning_content"
        assert dict(model.profile)["openai_supports_tool_choice_required"] is False
        agent = Agent(model)
        async with model:
            if streaming:
                async with agent.run_stream("Reply OK", model_settings={"thinking": "high"}) as result:
                    output = await result.get_output()
            else:
                output = (await agent.run("Reply OK", model_settings={"thinking": "high"})).output

    assert output == "OK"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://relay.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer relay-secret"
    body = json.loads(request.content)
    assert body["model"] == "my-deepseek-reasoner"
    assert body["reasoning_effort"] == "high"
