from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_stream_protocol.display import DisplayPosition, DisplaySnapshot, DisplayState, Producer
from a13n_stream_protocol.projector import DisplayProjector
from a13n_stream_protocol.session import DisplaySession
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


def _projection(state: HarnessState):
    baseline = DisplaySnapshot(position=DisplayPosition(producer=Producer(run_id="host-run", generation="1")))
    delivered = DisplayState(baseline)
    projector = DisplayProjector(
        baseline, publish=delivered.apply, ignored_capabilities=frozenset({"a13n.harness_ui.checkpoint"})
    )
    session = DisplaySession(projector, state.message_history, thread_id=state.thread_id)
    return projector, session, delivered


async def test_capture_uses_producer_history_while_external_consumer_is_stalled() -> None:
    initial = HarnessState.new()
    projector, session, delivered = _projection(initial)
    captured = asyncio.Event()
    release_save = asyncio.Event()
    snapshots: list[DisplaySnapshot] = []

    async def save(state: HarnessState, snapshot: DisplaySnapshot | None) -> str:
        snapshots.append(session.capture(state.message_history))
        captured.set()
        await release_save.wait()
        return "checkpoint-1"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "A complete answer"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(session, RootCheckpointCapability(save)),
    )
    stream = executable.stream(
        "User input before model entry",
        previous_state=initial,
        bindings=RunBindings.embedded(),
    )
    async with stream:
        iterator = stream.__aiter__()
        first = asyncio.create_task(anext(iterator))
        try:
            # The pre-model save proceeds while the external reader is not
            # requesting subsequent events. It does not await its own marker.
            await asyncio.wait_for(captured.wait(), 3)
            assert any(block.content.get("text") == "User input before model entry" for block in snapshots[0].blocks)
            assert not any(block.kind == "text" for block in snapshots[0].blocks)
        finally:
            release_save.set()
        await first
        while True:
            try:
                await anext(iterator)
            except StopAsyncIteration:
                break
    assert stream.result is not None
    final = session.capture(stream.result.state.message_history)
    assert [block.content["text"] for block in final.blocks if block.kind == "text"] == ["A complete answer"]
    assert delivered.capture().blocks == final.blocks
    assert projector.capture().blocks == final.blocks


async def test_stream_and_checkpoint_reconciliation_share_one_response_address() -> None:
    initial = HarnessState.new()
    projector, session, delivered = _projection(initial)
    snapshots: list[DisplaySnapshot] = []

    async def save(state: HarnessState, snapshot: DisplaySnapshot | None) -> str:
        snapshots.append(session.capture(state.message_history))
        return f"checkpoint-{len(snapshots)}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "same "
        yield "text "
        yield "same text"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(session, RootCheckpointCapability(save)),
    )
    async with executable.stream(
        "input",
        previous_state=initial,
        bindings=RunBindings.embedded(),
    ) as stream:
        async for _ in stream:
            pass
    assert stream.result is not None
    final = session.capture(stream.result.state.message_history)
    assert len([block for block in final.blocks if block.kind == "text"]) == 1
    assert final.blocks[-1].content["text"] == "same text same text"
    before = projector.state.position
    assert session.capture(stream.result.state.message_history).position == before
    assert delivered.capture().blocks == final.blocks


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
async def test_first_replacement_checkpoint_preserves_old_display_and_one_summary(kind: str) -> None:
    from a13n_harness.capabilities import CompactionCapability, CompactionPolicy, HandoffCapability
    from a13n_harness.capabilities.context import _COMPACTION_PROMPT
    from a13n_harness.model_context import user_prompt_content
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.function import DeltaToolCall, DeltaToolCalls
    from pydantic_ai.usage import RequestUsage

    initial = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("Original input")]),
            ModelResponse(parts=[TextPart("Original answer")], usage=RequestUsage(input_tokens=1000)),
        ]
    )
    _projector, session, delivered = _projection(initial)
    snapshots: list[DisplaySnapshot] = []
    requests = 0

    async def save(state: HarnessState, snapshot: DisplaySnapshot | None) -> str:
        snapshots.append(session.capture(state.message_history))
        return f"checkpoint-{len(snapshots)}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        if any(
            _COMPACTION_PROMPT in str(item.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in user_prompt_content(part)
        ):
            yield "Keep the decisions"
            return
        requests += 1
        if kind == "handoff" and requests == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize", json_args='{"content":"Keep the decisions"}', tool_call_id="handoff-call"
                )
            }
        else:
            yield "Final answer"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(
            session,
            RootCheckpointCapability(save),
            HandoffCapability(),
            *((CompactionCapability(CompactionPolicy(trigger_tokens=1)),) if kind == "compaction" else ()),
        ),
    )
    async with executable.stream("New input", previous_state=initial, bindings=RunBindings.embedded()) as stream:
        async for _ in stream:
            pass
    assert stream.result is not None
    stream.result.output_or_raise()
    final = session.capture(stream.result.state.message_history)
    texts = [block.content.get("text") for block in final.blocks]
    assert texts.count("Original input") == 1
    assert texts.count("Original answer") == 1
    assert texts.count("New input") == 1, [(block.id, block.content) for block in final.blocks]
    assert texts.count("Final answer") == 1
    assert len([block for block in final.blocks if block.kind == "context_summary"]) == 1
    assert not any(_COMPACTION_PROMPT in str(text) for text in texts)
    replacement = [
        snapshot for snapshot in snapshots if any(block.kind == "context_summary" for block in snapshot.blocks)
    ]
    assert replacement
    assert "Original answer" in [block.content.get("text") for block in replacement[0].blocks]
    assert delivered.capture().blocks == final.blocks


@pytest.mark.parametrize("adjacent_requests", [False, True])
async def test_suspended_response_keeps_its_slot_across_checkpoint_and_reload(adjacent_requests: bool) -> None:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    initial = HarnessState.new(
        message_history=[
            *([ModelRequest(parts=[UserPromptPart("First")], run_id="old")] if adjacent_requests else []),
            ModelRequest(parts=[UserPromptPart("Continue")], run_id="old"),
            ModelResponse(parts=[TextPart("partial ")], state="suspended", run_id="old"),
        ]
    )
    _projector, session, delivered = _projection(initial)
    snapshots: list[tuple[HarnessState, DisplaySnapshot]] = []

    async def save(state: HarnessState, snapshot: DisplaySnapshot | None) -> str:
        snapshots.append((state, session.capture(state.message_history)))
        return "checkpoint"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "finished"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(session, RootCheckpointCapability(save)),
    )
    result = await executable.run(None, previous_state=initial, bindings=RunBindings.embedded())
    result.output_or_raise()
    final = session.capture(result.state.message_history)
    texts = [block.content.get("text") for block in final.blocks if block.kind in {"input", "text"}]
    inputs = ["First", "Continue"] if adjacent_requests else ["Continue"]
    assert texts == [*inputs, "partial ", "finished"]
    assert len({block.message_index for block in final.blocks if block.kind == "text"}) == 1
    assert delivered.capture().blocks == final.blocks
    state, saved = snapshots[0]
    restored = DisplayProjector(DisplaySnapshot.model_validate_json(saved.model_dump_json()))
    reopened = DisplaySession(restored, state.message_history, thread_id=state.thread_id)
    retry = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(reopened,)
    )
    retried = await retry.run(None, previous_state=state, bindings=RunBindings.embedded())
    retried.output_or_raise()
    assert [
        block.content.get("text")
        for block in reopened.capture(retried.state.message_history).blocks
        if block.kind in {"input", "text"}
    ] == [*inputs, "finished"]


@pytest.mark.parametrize("nested", [False, True])
async def test_inline_child_capture_uses_own_scope_and_root_sequence(nested: bool) -> None:
    from a13n_harness import AgentDefinition, SubagentDefinition
    from a13n_harness.capabilities import SubagentCapability
    from a13n_harness.environment.advanced import EmptyEnvironmentRuntime
    from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
    from a13n_stream_protocol.session import DisplayCapture
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, DeltaToolCalls

    baseline = DisplaySnapshot(position=DisplayPosition(producer=Producer(run_id="root", generation="1")))
    receiver = DisplayState(baseline)
    projector = DisplayProjector(
        baseline, publish=receiver.apply, ignored_capabilities=frozenset({"a13n.harness_ui.checkpoint"})
    )
    capture = DisplayCapture(projector)
    snapshots: list[DisplaySnapshot] = []

    async def save(state: HarnessState, snapshot: DisplaySnapshot | None) -> str:
        snapshots.append(capture.capture(stream.run_id, state.message_history))
        return f"checkpoint-{len(snapshots)}"

    async def grandchild_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "Grandchild answer"

    async def child_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if nested and not any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args='{"subagent":"grandchild","prompt":"Nested question"}',
                    tool_call_id="nested-call",
                )
            }
        else:
            yield "Child answer"

    async def parent_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if not any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield {
                0: DeltaToolCall(
                    name="delegate", json_args='{"subagent":"child","prompt":"Question"}', tool_call_id="delegate-call"
                )
            }
        else:
            yield "Root answer"

    async def allow(*args, **kwargs):
        return InvocationPolicyDecision.allow()

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="child",
        model=FunctionModel(stream_function=child_model),
        capabilities=(capture, SubagentCapability()),
        subagents=(
            SubagentDefinition(
                name="grandchild",
                description="Nested task",
                agent=AgentDefinition(
                    agent=AgentSpec(),
                    output_type=str,
                    definition_id="grandchild",
                    model=FunctionModel(stream_function=grandchild_model),
                    capabilities=(capture,),
                ),
            ),
        )
        if nested
        else (),
    )
    parent = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="parent",
        model=FunctionModel(stream_function=parent_model),
        capabilities=(capture, SubagentCapability(), RootCheckpointCapability(save)),
        subagents=(SubagentDefinition(name="child", description="Child task", agent=child),),
    )
    executable = HarnessBuilder().build(parent)
    stream = executable.stream(
        "Root input",
        bindings=RunBindings.embedded(
            environment=EmptyEnvironmentRuntime(),
            capabilities=(InvocationPolicyCapability(evaluator=allow),),
            extension_observer=projector.observe_extension,
        ),
    )
    async with stream:
        async for _ in stream:
            pass
    assert stream.result is not None
    stream.result.output_or_raise()
    final = capture.capture(stream.run_id, stream.result.state.message_history)
    assert len(final.scopes) == (3 if nested else 2)
    root, child_scope = final.scopes[:2]
    if nested:
        assert final.scopes[2].parent_scope_id == child_scope.id
        assert final.scopes[2].parent_tool_call_id == "nested-call"
        assert final.scopes[2].status == "completed"
    assert child_scope.parent_scope_id == root.id
    assert child_scope.parent_tool_call_id == "delegate-call"
    assert child_scope.status == "completed"
    assert [block.content.get("text") for block in final.blocks if block.kind == "text"] == [
        *(["Grandchild answer"] if nested else []),
        "Child answer",
        "Root answer",
    ]
    assert any(any(block.content.get("text") == "Child answer" for block in snapshot.blocks) for snapshot in snapshots)
    assert receiver.capture().blocks == final.blocks
    assert len(capture.sessions) == (3 if nested else 2)


@pytest.mark.parametrize("cancel", [False, True])
async def test_parallel_tool_boundaries_capture_both_calls_and_independent_status(cancel: bool) -> None:
    from a13n_harness import AgentDefinition
    from pydantic_ai.capabilities import Toolset
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, DeltaToolCalls
    from pydantic_ai.toolsets import FunctionToolset

    initial = HarnessState.new()
    projector, session, receiver = _projection(initial)
    started: set[str] = set()
    both_started = asyncio.Event()
    release = asyncio.Event()
    saved: list[DisplaySnapshot] = []

    async def work(name: str) -> str:
        started.add(name)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return f"result-{name}"

    async def save(state: HarnessState, snapshot: DisplaySnapshot | None) -> str:
        saved.append(session.capture(state.message_history))
        return f"checkpoint-{len(saved)}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "done"
        else:
            yield {
                i: DeltaToolCall(name="work", json_args=f'{{"name":"{name}"}}', tool_call_id=name)
                for i, name in enumerate(("first", "second"))
            }

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(session, RootCheckpointCapability(save), Toolset(FunctionToolset([work]))),
        )
    )
    async with executable.stream("parallel", previous_state=initial, bindings=RunBindings.embedded()) as stream:

        async def consume() -> None:
            async for _ in stream:
                pass

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(both_started.wait(), 3)
            active = [block for block in projector.capture().blocks if block.kind == "tool_chunk"]
            assert len(active) == 2
            assert {block.status for block in active} == {"running"}
            assert all(block.content["arguments_complete"] is True for block in active)
            if cancel:
                stream.cancel()
            else:
                release.set()
            await asyncio.wait_for(consumer, 3)
        finally:
            release.set()
            if not consumer.done():
                stream.cancel()
                await consumer
    assert stream.result is not None
    final = session.capture(stream.result.state.message_history)
    tools = [block for block in final.blocks if block.kind == "tool_chunk"]
    assert len(tools) == 2
    assert {block.status for block in tools} == ({"cancelled"} if cancel else {"succeeded"})
    if not cancel:
        assert len(saved) == 2
        assert {block.status for block in saved[-1].blocks if block.kind == "tool_chunk"} == {"succeeded"}
    assert receiver.capture().blocks == final.blocks


@pytest.mark.parametrize("fail_save", [False, True])
async def test_capture_checkpoint_save_failure_and_cancellation_do_not_advance_provider(fail_save: bool) -> None:
    initial = HarnessState.new()
    projector, session, _ = _projection(initial)
    entered = asyncio.Event()
    release = asyncio.Event()
    saved: list[DisplaySnapshot] = []
    provider_calls = 0

    async def save(state: HarnessState, snapshot: DisplaySnapshot | None) -> str:
        snapshot = session.capture(state.message_history)
        entered.set()
        await release.wait()
        if fail_save:
            raise OSError("storage unavailable")
        saved.append(snapshot)
        return "checkpoint"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal provider_calls
        provider_calls += 1
        yield "not reached"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(session, RootCheckpointCapability(save)),
    )
    async with executable.stream("saved input", previous_state=initial, bindings=RunBindings.embedded()) as stream:

        async def consume() -> None:
            async for _ in stream:
                pass

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            if not fail_save:
                stream.cancel()
            release.set()
            await asyncio.wait_for(consumer, 3)
        finally:
            release.set()
            if not consumer.done():
                stream.cancel()
                await consumer
    assert provider_calls == 0
    assert len(saved) == (0 if fail_save else 1)
    assert projector.capture().blocks[0].content["text"] == "saved input"
    if saved:
        assert saved[0].blocks == projector.capture().blocks


async def test_deferred_tool_resume_updates_original_scope_after_reload() -> None:
    from a13n_harness import DeferredToolResume
    from pydantic_ai import Tool
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, DeltaToolCalls
    from pydantic_ai.tools import DeferredToolResults

    initial = HarnessState.new()
    _projector, session, _ = _projection(initial)

    async def change() -> str:
        return "changed"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="change", json_args="{}", tool_call_id="call")}

    tools = Capability(tools=[Tool(change, requires_approval=True)], id="change-tools")
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(session, tools)
    )
    first = await executable.run("go", previous_state=initial, bindings=RunBindings.embedded())
    assert first.status == "suspended" and first.state is not None and first.deferred is not None
    saved = session.capture(first.state.message_history)
    old = next(block for block in saved.blocks if block.kind == "tool_chunk")
    assert old.status == "deferred"
    restored = DisplayProjector(DisplaySnapshot.model_validate_json(saved.model_dump_json()))
    continuation = DisplaySession(restored, first.state.message_history, thread_id=initial.thread_id)
    resumed = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(continuation, tools)
    )
    second = await resumed.run(
        None,
        previous_state=first.state,
        bindings=RunBindings.embedded(),
        deferred_resume=DeferredToolResume(
            requests=first.deferred, results=DeferredToolResults(approvals={"call": True})
        ),
    )
    second.output_or_raise()
    final = continuation.capture(second.state.message_history)
    calls = [block for block in final.blocks if block.kind == "tool_chunk"]
    assert len(calls) == 1
    assert calls[0].id == old.id
    assert calls[0].status == "succeeded"
    assert calls[0].content["arguments"] == "{}"
    assert calls[0].content["result"] == "changed"


async def test_native_input_event_does_not_duplicate_or_expose_hidden_payloads() -> None:
    from pydantic_ai.messages import BinaryContent, TextContent

    initial = HarnessState.new()
    _projector, session, _ = _projection(initial)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(session,)
    )
    result = await executable.run(
        [
            TextContent("visible input"),
            TextContent("private overlay", metadata={"display": False}),
            BinaryContent(data=b"private image bytes", media_type="image/png"),
        ],
        previous_state=initial,
        bindings=RunBindings.embedded(),
    )
    result.output_or_raise()
    snapshot = session.capture(result.state.message_history)
    encoded = snapshot.model_dump_json()
    assert encoded.count("visible input") == 1
    assert "private overlay" not in encoded
    assert "private image bytes" not in encoded
    assert len([block for block in snapshot.blocks if block.kind == "media"]) == 1
    assert not any(block.content.get("name") == "a13n.context.model_input" for block in snapshot.blocks)
