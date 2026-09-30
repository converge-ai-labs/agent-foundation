"""Producer capture is paired with canonical state, independently of consumer delay."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from a13n_harness import HarnessBuilder, HarnessEvent, HarnessState, RunBindings
from a13n_service.runs.boundaries import Boundaries, SafeBoundary
from a13n_service.runs.display import Display
from a13n_stream_protocol import DisplayCapture, DisplayProjector, DisplayState
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


async def test_stalled_consumer_cannot_pair_a_boundary_with_newer_output() -> None:
    initial = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("Imported input")]),
            ModelResponse(parts=[TextPart("Imported answer")]),
        ]
    )
    baseline = Display.empty("service-run", attempt=1).snapshot
    delivered = DisplayState(baseline)
    emitted = asyncio.Event()

    def publish(delta):
        delivered.apply(delta)
        if any(block.content.get("text") == "Later answer" for block in delivered.blocks.values()):
            emitted.set()

    projector = DisplayProjector(baseline, publish=publish, ignored_capabilities=frozenset({"a13n.service.boundary"}))
    capture = DisplayCapture(projector, include_initial=False)
    boundaries = Boundaries(lambda: {"memory": "cursor-1"}, capture)
    release = asyncio.Event()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "Later answer"
        await release.wait()

    executable = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(capture, boundaries)
    )
    stream = executable.stream("Current input", previous_state=initial, bindings=RunBindings.embedded())
    async with stream:
        iterator = stream.__aiter__()
        async with asyncio.timeout(3):
            while True:
                item = await anext(iterator)
                if isinstance(item, HarnessEvent) and isinstance(item.event, SafeBoundary):
                    break
        try:
            # Stop at the marker, before the public consumer observes any model output.
            await asyncio.wait_for(emitted.wait(), 3)
            staged = boundaries.states[1]
            assert staged.cursors == {"memory": "cursor-1"}
            assert [block.content["text"] for block in staged.display.blocks if block.kind in {"input", "text"}] == [
                "Current input"
            ]
            assert any(block.content.get("text") == "Later answer" for block in projector.capture().blocks)
            assert staged.display.position.sequence < projector.capture().position.sequence
            assert len(staged.state.message_history) == 3
            assert not any(block.kind == "extension" for block in staged.display.blocks)
        finally:
            release.set()
        while True:
            try:
                item = await anext(iterator)
            except StopAsyncIteration:
                break
            if isinstance(item, HarnessEvent) and isinstance(item.event, SafeBoundary):
                boundaries.acknowledge(item.event.token)
    assert stream.result is not None and stream.result.state is not None
    final = capture.capture(stream.run_id, stream.result.state.message_history)
    assert [block.content["text"] for block in final.blocks if block.kind in {"input", "text"}] == [
        "Current input",
        "Later answer",
    ]
    assert final.blocks == delivered.capture().blocks
