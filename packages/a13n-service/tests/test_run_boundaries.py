"""Real Harness ordering gates for consumer-owned state/display checkpoints."""

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from a13n_harness import HarnessBuilder, HarnessEvent, HarnessState, RunBindings
from a13n_service.runs.boundaries import Boundaries, SafeBoundary
from a13n_stream_protocol.display import DisplayFold, Tail
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Toolset
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(("queue_size", "delay"), [(1, 0), (1, 0.005), (8, 0.005)])
async def test_parallel_tools_wait_for_matching_display_checkpoint(queue_size: int, delay: float) -> None:
    boundaries = Boundaries(lambda: {"memory": "cursor"})
    display = DisplayFold("run", Tail(), attempt=1, page_items=128, page_bytes=65536)
    effects: list[str] = []
    cuts: list[str] = []
    acknowledged = False

    async def work(name: str) -> str:
        assert acknowledged
        effects.append(name)
        return f"result-{name}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            yield "done"
        else:
            yield {
                i: DeltaToolCall(name="work", json_args=json.dumps({"name": name}), tool_call_id=name)
                for i, name in enumerate(("first", "second"))
            }

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(boundaries, Toolset(FunctionToolset([work]))),
    )
    queue = asyncio.Queue(maxsize=queue_size)
    async with executable.stream("parallel", bindings=RunBindings.embedded()) as stream:

        async def read() -> None:
            async for item in stream:
                await queue.put(item)
            await queue.put(None)

        async def consume() -> None:
            nonlocal acknowledged
            while (item := await queue.get()) is not None:
                if delay:
                    await asyncio.sleep(delay)
                event = item.event if isinstance(item, HarnessEvent) else None
                if isinstance(event, SafeBoundary):
                    staged = boundaries.take(event.token)
                    snapshot = display.snapshot(staged.open_calls)
                    assert staged.cursors == {"memory": "cursor"}
                    if event.at == "tool":
                        tools = [entry for entry in snapshot.tail.items if entry.kind == "tool_call"]
                        assert staged.open_calls == frozenset({"first", "second"})
                        assert {tool.content["toolCallId"] for tool in tools} == {"first", "second"}
                        assert all(tool.state == "in_progress" for tool in tools)
                        assert not effects
                        await asyncio.sleep(0.02)
                        assert not effects
                        acknowledged = True
                    cuts.append(event.at)
                    display.committed(snapshot)
                    boundaries.acknowledge(event.token)
                else:
                    display.fold(display.events(item), source=item)

        await asyncio.wait_for(asyncio.gather(read(), consume()), 10)
        assert stream.result is not None and stream.result.status == "completed"
    assert sorted(effects) == ["first", "second"]
    assert cuts == ["model", "tool", "model"]
    assert not boundaries.states and not boundaries.acknowledged


async def test_same_length_replacement_stages_a_distinct_model_boundary() -> None:
    boundaries = Boundaries(lambda: {})
    markers: list[SafeBoundary] = []

    async def export(messages: list[ModelMessage]) -> HarnessState:
        return HarnessState.new(message_history=messages)

    async def emit(event: SafeBoundary) -> None:
        markers.append(event)

    context = SimpleNamespace(deps=SimpleNamespace(export_state=export), emit=emit)
    first = [ModelRequest(parts=[UserPromptPart("original")]), ModelResponse(parts=[TextPart("answer")])]
    second = [ModelRequest(parts=[UserPromptPart("replacement")]), ModelResponse(parts=[TextPart("summary")])]
    one = await boundaries._stage(context, first, "model")
    two = await boundaries._stage(context, second, "model")
    assert one != two
    assert boundaries.take(one).state.message_history == tuple(first)
    assert boundaries.take(two).state.message_history == tuple(second)
    assert [marker.token for marker in markers] == [one, two]
    boundaries.acknowledge(one)
    boundaries.acknowledge(two)
