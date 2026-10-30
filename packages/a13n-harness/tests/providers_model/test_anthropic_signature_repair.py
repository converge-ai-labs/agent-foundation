"""Exercise history repair through the real native adapter and SDK transport."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from a13n_harness.models import SelfHealingModel
from a13n_harness.models.self_healing import DEFAULT_MODEL_RECOVERY_RULES
from anthropic import AsyncAnthropic
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider

pytestmark = pytest.mark.anyio

SIGNATURE_ERROR = "Invalid `signature` in `thinking` block"
SETTINGS = AnthropicModelSettings(anthropic_thinking={"type": "enabled", "budget_tokens": 1024}, max_tokens=2048)


def _history():
    return [
        ModelRequest(parts=[UserPromptPart(["Inspect this", BinaryContent(b"image", media_type="image/png")])]),
        ModelResponse(
            parts=[
                ThinkingPart("old reasoning", signature="old-signature", provider_name="anthropic"),
                TextPart("Ready"),
            ],
            model_name="claude-test",
            provider_name="anthropic",
        ),
        ModelRequest(parts=[UserPromptPart("Use the result")]),
        ModelResponse(
            parts=[
                ThinkingPart("", id="redacted_thinking", signature="opaque", provider_name="anthropic"),
                ThinkingPart("later reasoning", signature="bad-signature", provider_name="anthropic"),
                TextPart("Checking"),
                ToolCallPart("lookup", {"key": "answer"}, tool_call_id="toolu_lookup"),
            ],
            model_name="claude-test",
            provider_name="anthropic",
            provider_response_id="msg_history",
        ),
        ModelRequest(parts=[ToolReturnPart("lookup", "314159", tool_call_id="toolu_lookup")]),
    ]


def _error(message=SIGNATURE_ERROR):
    return {"type": "error", "error": {"type": "invalid_request_error", "message": message}}


def _message():
    return {
        "id": "msg_answer",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "314159"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 2},
    }


def _events():
    return [
        {"type": "message_start", "message": {**_message(), "content": [], "stop_reason": None}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "314159"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    ]


def _sse(events):
    content = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=content)


def _success(streaming):
    return _sse(_events()) if streaming else httpx2.Response(200, json=_message())


@asynccontextmanager
async def _model_with_responses(responses):
    requests = []

    async def handler(request):
        requests.append(request)
        assert len(requests) <= len(responses), "unexpected additional request"
        return responses[len(requests) - 1]

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        sdk = AsyncAnthropic(api_key="test-key", max_retries=0, http_client=client)
        native = AnthropicModel("claude-test", provider=AnthropicProvider(anthropic_client=sdk))
        yield SelfHealingModel(native), requests


async def _request(model, history, streaming):
    if streaming:
        async with model.request_stream(history, SETTINGS, ModelRequestParameters()) as stream:
            async for _ in stream:
                pass
            return stream.get()
    return await model.request(history, SETTINGS, ModelRequestParameters())


def _assert_only_thinking_removed(requests, history, original):
    assert len(requests) == 2
    before, after = (json.loads(request.content) for request in requests)
    removed = []
    expected = deepcopy(before)
    for message in expected["messages"]:
        if message["role"] == "assistant":
            removed.extend(
                block["type"] for block in message["content"] if block["type"] in {"thinking", "redacted_thinking"}
            )
            message["content"] = [
                block for block in message["content"] if block["type"] not in {"thinking", "redacted_thinking"}
            ]
    assert removed == ["thinking", "redacted_thinking", "thinking"]
    assert after == expected
    assert after["thinking"] == SETTINGS["anthropic_thinking"]
    assert requests[0].url == requests[1].url
    assert requests[0].headers.get("anthropic-beta") == requests[1].headers.get("anthropic-beta")
    for message in original:
        if isinstance(message, ModelResponse):
            message.parts = [part for part in message.parts if not isinstance(part, ThinkingPart)]
    assert history == original


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "status,body",
    [
        (400, _error()),
        (400, _error("Invalid 'signature' in 'thinking' block")),
        (400, _error('Invalid "signature" in "thinking" block')),
        (400, _error("INVALID signature IN thinking BLOCK")),
        (400, _error(SIGNATURE_ERROR + ": the signature must be bound to the preceding message prefix.")),
        (502, {"code": 5005, "message": "upstream error", "upstream_error": _error()}),
        (502, {"message": json.dumps(_error())}),
        (502, {"message": json.dumps(_error("Invalid 'signature' in 'thinking' block"))}),
        (502, {"message": json.dumps(_error('Invalid "signature" in "thinking" block'))}),
    ],
)
async def test_signature_repair_replays_only_the_model_request(streaming, status, body):
    history = _history()
    original = deepcopy(history)
    async with _model_with_responses([httpx2.Response(status, json=body), _success(streaming)]) as (model, requests):
        response = await _request(model, history, streaming)
    assert response.parts == [TextPart("314159")]
    _assert_only_thinking_removed(requests, history, original)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "message",
    [
        "upstream error",
        "request signature is invalid",
        "Invalid authentication signature; thinking block was supplied",
        "thinking block signature is valid; invalid thinking budget",
        "thinking budget_tokens must be less than max_tokens",
    ],
)
async def test_unrelated_provider_errors_preserve_history(streaming, message):
    history = _history()
    original = deepcopy(history)
    async with _model_with_responses([httpx2.Response(502, json={"code": 5005, "message": message})]) as (
        model,
        requests,
    ):
        with pytest.raises(ModelHTTPError):
            await _request(model, history, streaming)
    assert len(requests) == 1
    assert history == original


@pytest.mark.parametrize("streaming", [False, True])
async def test_signature_repair_requires_a_history_change(streaming):
    history = [ModelRequest(parts=[UserPromptPart("hello")])]
    original = deepcopy(history)
    async with _model_with_responses([httpx2.Response(400, json=_error())]) as (model, requests):
        with pytest.raises(ModelHTTPError, match="Invalid"):
            await _request(model, history, streaming)
    assert len(requests) == 1
    assert history == original


@pytest.mark.parametrize("streaming", [False, True])
async def test_signature_repair_stops_after_second_rejection_without_rollback(streaming):
    history = _history()
    original = deepcopy(history)
    second_error = _error(SIGNATURE_ERROR + ": second rejection")
    async with _model_with_responses(
        [httpx2.Response(400, json=_error()), httpx2.Response(400, json=second_error)]
    ) as (model, requests):
        with pytest.raises(ModelHTTPError, match="second rejection") as caught:
            await _request(model, history, streaming)
    assert caught.value.body == second_error
    _assert_only_thinking_removed(requests, history, original)


@pytest.mark.parametrize("streaming", [False, True])
async def test_successful_request_keeps_original_thinking(streaming):
    history = _history()
    original = deepcopy(history)
    async with _model_with_responses([_success(streaming)]) as (model, requests):
        await _request(model, history, streaming)
    assert len(requests) == 1
    assert history == original


async def test_leading_sse_error_is_repaired_before_stream_is_yielded():
    history = _history()
    original = deepcopy(history)
    async with _model_with_responses([_sse([_error()]), _success(True)]) as (model, requests):
        response = await _request(model, history, True)
    assert response.parts == [TextPart("314159")]
    _assert_only_thinking_removed(requests, history, original)


@pytest.mark.parametrize("prefix_length", [1, 3], ids=["message-start-only", "text-emitted"])
async def test_error_after_stream_establishment_does_not_repair(prefix_length):
    history = _history()
    original = deepcopy(history)
    async with _model_with_responses([_sse([*_events()[:prefix_length], _error()])]) as (model, requests):
        async with model.request_stream(history, SETTINGS, ModelRequestParameters()) as stream:
            # Even message_start alone has crossed the native adapter's establishment boundary.
            assert len(requests) == 1
            with pytest.raises(ModelAPIError, match="Invalid"):
                async for _ in stream:
                    pass
    assert len(requests) == 1
    assert history == original


@pytest.mark.parametrize(
    "error",
    [
        ModelAPIError("test", SIGNATURE_ERROR),
        ModelAPIError("test", "upstream error", body={"errors": [_error()]}),
        ModelHTTPError(400, "test", body=_error()),
        ModelHTTPError(502, "test", body={"message": json.dumps(_error())}),
    ],
)
def test_named_signature_rule_matches_provider_error_text(error):
    rule = next(rule for rule in DEFAULT_MODEL_RECOVERY_RULES if rule.name == "anthropic_invalid_thinking_signature")
    assert rule.matches(error)
    assert not rule.matches(ValueError(SIGNATURE_ERROR))


@pytest.mark.parametrize("project_images", [False, True])
@pytest.mark.parametrize("second_rejection", [False, True])
async def test_harness_export_respects_request_history_ownership(project_images, second_rejection, png_image_bytes):
    history = _history()
    history[0] = ModelRequest(
        parts=[
            UserPromptPart(
                ["Inspect", BinaryContent(png_image_bytes, media_type="image/png")] if project_images else "Inspect"
            )
        ]
    )
    previous = HarnessState.new(message_history=history)
    before = previous.message_history_json
    second = httpx2.Response(400, json=_error()) if second_rejection else _success(True)
    async with _model_with_responses([httpx2.Response(400, json=_error()), second]) as (model, requests):
        executable = HarnessBuilder().build(AgentSpec(model_settings=SETTINGS), output_type=str, model=model.wrapped)
        result = await executable.run("Continue", previous_state=previous)
    assert result.status == ("failed" if second_rejection else "completed")
    if not second_rejection:
        assert result.output_or_raise() == "314159"
    assert len(requests) == 2
    assert previous.message_history_json == before
    assert result.state is not None
    remaining = [
        part for message in result.state.message_history for part in message.parts if isinstance(part, ThinkingPart)
    ]
    # Image projection detaches every request envelope. Otherwise the native
    # request list shares response objects with live history and later exports.
    assert len(remaining) == (3 if project_images else 0)
    outbound = json.loads(requests[1].content)
    assert not any(
        block["type"] in {"thinking", "redacted_thinking"}
        for message in outbound["messages"]
        for block in message["content"]
    )
