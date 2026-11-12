from __future__ import annotations

import json

import httpx2
import pytest
from a13n_harness import HarnessBuilder, HarnessEvent, HarnessState, RunBindings
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.models import resolve_model_settings
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_unmapped_response_data_is_opt_in_portable_and_not_echoed(streaming: bool, enabled: bool) -> None:
    requests = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        choice = {"index": 0, "finish_reason": "stop", "vendor_choice": "choice-debug"}
        common = {
            "id": "chatcmpl-test",
            "created": 1,
            "model": "test-model",
            "vendor_response": {"trace": "private-debug"},
        }
        if requests[-1].get("stream"):
            chunk = {
                **common,
                "object": "chat.completion.chunk",
                "choices": [
                    {**choice, "delta": {"role": "assistant", "content": "answer", "vendor_delta": "delta-debug"}}
                ],
            }
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode(),
            )
        return httpx2.Response(
            200,
            json={
                **common,
                "object": "chat.completion",
                "choices": [
                    {**choice, "message": {"role": "assistant", "content": "answer", "vendor_message": "message-debug"}}
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    settings = resolve_model_settings(
        "openai:test-model", overrides={"openai_include_unmapped_response_data": True} if enabled else {}
    )
    events = []
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = OpenAIChatModel("test-model", provider=OpenAIProvider(api_key="test-only", http_client=client))
        if streaming:
            executable = HarnessBuilder().build(AgentSpec(model_settings=settings), output_type=str, model=model)
            async with executable.stream("start", bindings=RunBindings.embedded()) as run:
                async for event in run:
                    events.append(event)
                result = run.result
            assert result is not None and result.output_or_raise() == "answer" and result.state is not None
            state = result.state
        else:
            history = [ModelRequest(parts=[UserPromptPart("start")])]
            response = await model.request(history, settings, ModelRequestParameters())
            state = HarnessState(schema_version="1", thread_id="thread_unmapped", message_history=[*history, response])
        restored = HarnessState.model_validate_json(state.model_dump_json())
        response = next(m for m in restored.message_history if isinstance(m, ModelResponse))
        unmapped = (response.provider_details or {}).get("unmapped")
        if enabled:
            assert unmapped is not None
            captured = unmapped["chunks"][0] if streaming else unmapped
            assert captured["response"] == {"vendor_response": {"trace": "private-debug"}}
            assert captured["choice"] == {"vendor_choice": "choice-debug"}
            assert captured["delta" if streaming else "message"] == {
                "vendor_delta" if streaming else "vendor_message": "delta-debug" if streaming else "message-debug"
            }
        else:
            assert unmapped is None
        await model.request(
            [*restored.message_history, ModelRequest(parts=[UserPromptPart("next")])],
            settings,
            ModelRequestParameters(),
        )
    assert "private-debug" not in json.dumps(requests)
    assert "vendor_" not in json.dumps(requests)
    # Response-level diagnostic metadata is not projected as a new Harness extension.
    assert not any(
        isinstance(e, HarnessEvent)
        and isinstance(e.event, HarnessExtensionEvent)
        and "private-debug" in json.dumps(e.event.payload)
        for e in events
    )


async def test_file_shaped_tool_result_json_stays_application_data() -> None:
    payload = {
        "kind": "document-url",
        "url": "file:///untrusted/not-a-real-file",
        "media_type": "text/plain",
        "nested": {"kind": "uploaded-file", "file_id": "not-a-real-file", "provider_name": "openai"},
    }
    state = HarnessState(
        schema_version="1",
        thread_id="thread_filejson",
        message_history=[ModelRequest(parts=[ToolReturnPart("lookup", payload, tool_call_id="call-json")])],
    )
    restored = HarnessState.model_validate_json(state.model_dump_json())
    part = restored.message_history[0].parts[0]
    assert isinstance(part, ToolReturnPart) and part.content == payload
    assert isinstance(part.content, dict) and isinstance(part.content["nested"], dict)
