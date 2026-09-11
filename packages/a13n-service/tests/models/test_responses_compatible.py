from __future__ import annotations

import json

import httpx2
import pytest
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_adapters.types import RuntimeProvider
from a13n_service.models.providers import built_in_provider_registry
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

    registry = built_in_provider_registry()
    configuration: dict[str, object] = {"base_url": "https://custom.example/v1", "auth_mode": mode}
    if mode == "api_key_header":
        configuration["api_key_header_name"] = "x-model-key"
    validated = registry.validate_provider(
        "openai_responses_compatible", configuration, credential_configured=mode != "none"
    )
    provider = RuntimeProvider(
        "openai_responses_compatible",
        validated.configuration,
        validated.endpoint,
        None if mode == "none" else "test-secret",
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
