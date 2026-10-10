from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from a13n_harness import state as state_module
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


async def test_runs_decode_initial_history_once_and_keep_mutations_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = HarnessState.new(
        message_history=[ModelRequest(parts=[UserPromptPart("original")]), ModelResponse(parts=[TextPart("old")])]
    )
    original_bytes = previous.message_history_json
    original_decode = state_module.decode_messages
    decodes = 0

    def counted_decode(value: bytes) -> tuple[ModelMessage, ...]:
        nonlocal decodes
        decodes += 1
        return original_decode(value)

    monkeypatch.setattr(state_module, "decode_messages", counted_decode)
    ready = asyncio.Event()
    entered = 0

    def build(label: str):
        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            del info
            nonlocal entered
            part = messages[0].parts[0]
            assert isinstance(part, UserPromptPart) and part.content == "original"
            part.content = label
            entered += 1
            if entered == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=2)
            assert part.content == label
            yield label

        return HarnessBuilder(instrumentation=None, self_healing_enabled=False).build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=model)
        )

    async def run(label: str) -> None:
        async with build(label).stream("continue", previous_state=previous) as stream:
            async for _item in stream:
                pass
        assert _item.result.output_or_raise() == label

    await asyncio.gather(run("first"), run("second"))
    assert decodes == 2
    assert previous.message_history_json == original_bytes
    assert previous.message_history[0].parts[0].content == "original"
