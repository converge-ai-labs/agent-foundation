from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, ModelRecoveryPolicy, RunBindings
from a13n_harness.providers.model.apis import MODEL_APIS
from a13n_harness.recovery import is_recoverable_model_failure
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.providers.openai import OpenAIProvider

pytestmark = pytest.mark.anyio


def _chunk(delta: dict[str, object], finish_reason: str | None = None) -> str:
    return (
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
        )
        + "\n\n"
    )


@asynccontextmanager
async def _model(body: str) -> AsyncIterator[Model]:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["stream"] is True
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = OpenAIProvider(base_url="https://model.example/v1", api_key="test", http_client=client)
        yield MODEL_APIS["openai.chat_completions"].build("test-model", provider)


@pytest.mark.parametrize("finish_reason", ["stop", "length", "tool_calls"])
async def test_chat_stream_accepts_explicit_finish_reason(finish_reason: str) -> None:
    delta: dict[str, object] = {"content": "answer"}
    if finish_reason == "tool_calls":
        delta = {
            "tool_calls": [
                {"index": 0, "id": "call-test", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
            ]
        }
    async with _model(_chunk(delta) + _chunk({}, finish_reason) + "data: [DONE]\n\n") as model:
        async with model.request_stream([], None, ModelRequestParameters()) as stream:
            async for _ in stream:
                pass
            assert stream.get().state == "complete"
            assert stream.get().finish_reason == ("tool_call" if finish_reason == "tool_calls" else finish_reason)


@pytest.mark.parametrize("done", [False, True], ids=["eof", "done-without-finish"])
@pytest.mark.parametrize(
    "delta",
    [
        {"content": "partial answer"},
        {"refusal": "I cannot"},
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call-test",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q":'},
                }
            ]
        },
    ],
    ids=["text", "refusal", "tool-arguments"],
)
async def test_chat_stream_rejects_missing_finish_reason(delta: dict[str, object], done: bool) -> None:
    async with _model(_chunk(delta) + ("data: [DONE]\n\n" if done else "")) as model:
        async with model.request_stream([], None, ModelRequestParameters()) as stream:
            with pytest.raises(ModelAPIError) as caught:
                async for _ in stream:
                    pass
            assert type(caught.value) is ModelAPIError
            assert caught.value.message == "Streamed response ended without a `finish_reason`"
            assert is_recoverable_model_failure(caught.value)
            assert stream.get().state == "incomplete"


@pytest.mark.parametrize("exit_kind", ["cancel", "early-exit", "task-cancel"])
async def test_chat_stream_preserves_intentional_interruption(exit_kind: str) -> None:
    async with _model(_chunk({"content": "partial answer"})) as model:
        try:
            async with model.request_stream([], None, ModelRequestParameters()) as stream:
                iterator = stream.__aiter__()
                await anext(iterator)
                if exit_kind == "cancel":
                    await stream.cancel()
                    async for _ in iterator:
                        pass
                    assert stream.get().state == "interrupted"
                elif exit_kind == "task-cancel":
                    raise asyncio.CancelledError
        except asyncio.CancelledError:
            assert exit_kind == "task-cancel"
        else:
            assert exit_kind != "task-cancel"


async def test_chat_stream_truncation_fails_harness_and_preserves_partial_text() -> None:
    async with _model(_chunk({"content": "partial answer"})) as model:
        executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=model)
        result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.status == "failed"
    assert any(
        message.state == "interrupted" and TextPart(content="partial answer") in message.parts
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
    )


@pytest.mark.parametrize("recovery_completes", [True, False], ids=["resumed", "exhausted"])
async def test_chat_stream_truncation_uses_harness_recovery(recovery_completes: bool) -> None:
    requests: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        body = _chunk({"content": "partial answer"})
        if len(requests) > 1 and recovery_completes:
            body = _chunk({"content": "resumed answer"}) + _chunk({}, "stop") + "data: [DONE]\n\n"
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = OpenAIProvider(base_url="https://model.example/v1", api_key="test", http_client=client)
        model = MODEL_APIS["openai.chat_completions"].build("test-model", provider)
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=model,
            model_recovery=ModelRecoveryPolicy(
                enabled=True,
                max_attempts=2,
                backoff_initial_seconds=0,
                backoff_max_seconds=0,
            ),
        )
        result = await executable.run("start", bindings=RunBindings.embedded())

    assert result.usage.requests == len(requests) == 2
    assert {"role": "assistant", "content": "partial answer"} in requests[1]["messages"]
    assert any(
        message.state == "interrupted" and TextPart(content="partial answer") in message.parts
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
    )
    if recovery_completes:
        assert result.status == "completed"
        assert result.output_or_raise() == "resumed answer"
    else:
        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.code == "model_recovery_exhausted"
