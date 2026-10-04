"""Producer display cuts and pre-effect acknowledgement remain independent of delivery."""

from collections.abc import AsyncIterator

import pytest
from a13n_harness import HarnessBuilder, HarnessEvent, RunBindings
from a13n_service.runs.boundaries import Boundaries, SafeBoundary
from a13n_service.runs.display import DisplayFold, Snapshot, Tail, open_tool_calls
from pydantic_ai import Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


async def test_producer_freezes_matching_display_before_parallel_tool_acknowledgement() -> None:
    fold = DisplayFold("run", Tail(), attempt=1, page_items=2, page_bytes=65536)
    boundaries = Boundaries(lambda: {})
    effects: list[int] = []
    calls = 0

    def freeze(open_calls: frozenset[str]) -> Snapshot:
        return fold.snapshot(open_calls)

    boundaries.freeze_display = freeze

    def capture(item: HarnessEvent) -> None:
        if not isinstance(item.event, SafeBoundary):
            fold.fold(fold.events(item), item)

    async def effect(value: int) -> str:
        effects.append(value)
        return str(value)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                index: DeltaToolCall(name="effect", json_args=f'{{"value":{index}}}', tool_call_id=f"call-{index}")
                for index in (0, 1)
            }
        else:
            assert sorted(effects) == [0, 1]
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(boundaries, Capability(tools=[Tool(effect)])),
    )
    cuts: list[Snapshot] = []
    async with executable.stream("Input", bindings=RunBindings.embedded(producer_observer=capture)) as run:
        async for item in run:
            if not isinstance(item, HarnessEvent) or not isinstance(item.event, SafeBoundary):
                continue
            staged = boundaries.take(item.event.token)
            assert staged.display is not None
            cuts.append(staged.display)
            if len(cuts) == 1:
                assert effects == []
                items = [entry for page in staged.display.pages for entry in page.items] + staged.display.tail.items
                assert any(entry.content.get("text") == "Input" for entry in items)
                assert not any(entry.kind == "tool_call" for entry in items)
                # Keep consuming transport while delaying this cut's publication.
                # The bounded native event mux does not promise unbounded progress
                # when the public iterator itself is paused.
                continue
            if item.event.at == "tool":
                assert any(entry.kind == "tool_call" for entry in fold.items.values())
                assert not any(entry.kind == "tool_call" for entry in cuts[0].tail.items)
                fold.committed(fold.pending(cuts[0]))
                boundaries.acknowledge(1)
                assert effects == []
                assert open_tool_calls(staged.state.message_history) == {"call-0", "call-1"}
                items = [entry for page in staged.display.pages for entry in page.items] + staged.display.tail.items
                assert {
                    entry.content["toolCallId"]
                    for entry in items
                    if entry.kind == "tool_call" and entry.state == "in_progress"
                } == {"call-0", "call-1"}
            selected = fold.pending(staged.display)
            fold.committed(selected)
            boundaries.acknowledge(item.event.token)
        assert run.result is not None and run.result.output_or_raise() == "done"
    assert len(cuts) == 3
    assert len(effects) == 2
