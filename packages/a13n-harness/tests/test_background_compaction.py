from __future__ import annotations

import asyncio
from copy import deepcopy
from io import BytesIO

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessModelCharacteristics, HarnessState
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.compaction import (
    CompactionOperation,
    CompactionOperator,
    CompactionPlan,
    CompactionResult,
    apply_compaction,
    apply_compaction_to_state,
    generate_compaction,
)
from a13n_harness.errors import DefinitionError
from a13n_harness.model_calls import ModelCallCheckError
from a13n_harness.state import encode_messages
from PIL import Image
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def _previous():
    return HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("old history")]),
            ModelResponse(parts=[TextPart("old answer")], usage=RequestUsage(input_tokens=9000)),
        )
    )


def _plan(previous=None):
    previous = previous or _previous()
    return CompactionPlan.create(
        operation_id="compaction-test",
        thread_id=previous.thread_id,
        run_id="run-test",
        model_name="test",
        provider_name="function",
        messages=previous.message_history,
        parameters=ModelRequestParameters(),
        settings={"tool_choice": "auto"},
    )


def _user_values(messages):
    return [
        part.content
        for msg in messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, UserPromptPart)
    ]


async def test_plan_roundtrip_detaches_native_values_and_result_preserves_delta():
    previous = _previous()
    messages = list(previous.message_history)
    replay = ModelRequest(parts=[UserPromptPart(["image", BinaryContent(b"image", media_type="image/png")])])
    messages += [replay, ModelResponse(parts=[TextPart("seen")])]
    plan = CompactionPlan.create(
        operation_id="compaction-test",
        thread_id=previous.thread_id,
        run_id="run-test",
        model_name="test",
        provider_name="function",
        messages=messages,
        retained_requests=[replay],
        parameters=ModelRequestParameters(),
    )
    frozen = plan.model_dump_json()
    messages[0].parts = []
    plan.messages[0].parts = []
    plan.replay_requests[0].parts = []
    plan = CompactionPlan.model_validate_json(frozen)
    assert plan.model_dump_json() == frozen
    result = CompactionResult.from_summary(plan, "summary")
    delta = [
        ModelRequest(parts=[UserPromptPart("later steering")]),
        ModelResponse(parts=[ToolCallPart("read", {}, "call-1")]),
        ModelRequest(parts=[ToolReturnPart("read", "new result", "call-1")]),
    ]
    latest = [*plan.messages, *delta]
    before = encode_messages(latest)
    merged = apply_compaction(plan, result, thread_id=plan.thread_id, messages=latest)
    assert encode_messages(merged[-3:]) == encode_messages(delta)
    assert _user_values(merged).count("later steering") == 1
    assert plan.replay_requests[0].parts[0].content in _user_values(merged)
    assert encode_messages(latest) == before


@pytest.mark.parametrize("mismatch", ["thread", "operation", "digest", "prefix", "pending"])
async def test_application_rejects_incompatible_result_without_mutation(mismatch):
    plan = _plan()
    result = CompactionResult.from_summary(plan, "summary")
    messages = plan.messages
    thread_id = plan.thread_id
    if mismatch == "thread":
        thread_id = "thread-other"
    elif mismatch == "operation":
        result = result.model_copy(update={"operation_id": "other"})
    elif mismatch == "digest":
        result = result.model_copy(update={"plan_digest": "other"})
    elif mismatch == "prefix":
        messages[0].parts = [UserPromptPart("cleared or handoff history")]
    else:
        messages.append(ModelResponse(parts=[ToolCallPart("read", {}, "pending")]))
    before = encode_messages(messages)
    with pytest.raises(DefinitionError):
        apply_compaction(plan, result, thread_id=thread_id, messages=messages)
    assert encode_messages(messages) == before


async def test_generator_retries_same_snapshot_with_no_mutable_aliases():
    plan = _plan()
    calls = []
    delays = []

    async def request(actual, messages, settings, parameters):
        assert actual == plan
        calls.append(encode_messages(messages))
        assert _COMPACTION_PROMPT in _user_values(messages)
        assert settings["tool_choice"] == "auto"
        assert parameters.output_mode == "text"
        messages[0].parts = []
        settings["tool_choice"] = "none"
        if len(calls) < 3:
            raise ModelHTTPError(503, "test")
        return ModelResponse(parts=[TextPart("summary")])

    async def sleep(delay):
        delays.append(delay)

    result = await generate_compaction(plan, request, sleep=sleep)
    assert result == CompactionResult.from_summary(plan, "summary")
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    assert len(delays) == 2


@pytest.mark.parametrize("error", [ModelCallCheckError(), ModelHTTPError(400, "test"), asyncio.CancelledError()])
async def test_generator_does_not_retry_refusal_permanent_failure_or_cancellation(error):
    calls = 0

    async def request(*args):
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(type(error)):
        await generate_compaction(_plan(), request)
    assert calls == 1


@pytest.mark.parametrize("parts", [[TextPart("  ")], [ToolCallPart("dangerous", {}, "call")]])
async def test_generator_rejects_invalid_summary_without_dispatching_tools(parts):
    async def request(*args):
        return ModelResponse(parts=parts)

    with pytest.raises(DefinitionError, match="Compaction returned"):
        await generate_compaction(_plan(), request)


class _Operator(CompactionOperator):
    """Test Host: work lives here, never on a parent Run or Capability."""

    def __init__(self):
        self.operation = None
        self.task = None
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.waiting = asyncio.Event()
        self.submissions = 0
        self.notifications = []

    async def submit(self, plan):
        if self.operation is not None:
            return self.operation
        self.submissions += 1
        self.operation = CompactionOperation(plan=plan, status="running")
        self.task = asyncio.create_task(self._execute(plan))
        return self.operation

    async def _execute(self, plan):
        async def request(actual, messages, settings, parameters):
            self.started.set()
            await self.release.wait()
            return ModelResponse(parts=[TextPart("background summary")])

        result = await generate_compaction(plan, request)
        self.operation = CompactionOperation(plan=plan, status="ready", result=result)
        self.notifications.append(result.operation_id)
        return self.operation

    async def inspect(self, thread_id):
        return self.operation

    async def wait(self, thread_id, operation_id):
        assert self.task is not None
        self.waiting.set()
        return await asyncio.shield(self.task)


class _CountingModel(FunctionModel):
    async def count_tokens(self, messages, model_settings, model_request_parameters):
        # Includes fresh overlays and tool outputs, not response usage.
        assert any("agent-context" in str(value) for value in _user_values(messages))
        return RequestUsage(input_tokens=100)


def _build(operator, stream, *, counting=True, extra=()):
    return HarnessBuilder().build(
        AgentSpec(
            model_settings={"max_tokens": 100},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
        ),
        output_type=str,
        model=(_CountingModel if counting else FunctionModel)(stream_function=stream),
        capabilities=(
            CompactionCapability(CompactionPolicy(trigger_tokens=8000), background=True, operator=operator),
            *extra,
        ),
    )


async def test_background_survives_parent_close_and_applies_once_on_next_run():
    operator = _Operator()
    requests = []

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        yield "foreground answer"

    executable = _build(operator, stream)
    first = await executable.run("first input", previous_state=_previous())
    assert first.output_or_raise() == "foreground answer"
    assert first.state is not None
    saved = first.state.model_dump_json()
    assert "old history" in _user_values(first.state.message_history)
    assert operator.task is not None and not operator.task.done()
    operator.release.set()
    await operator.task
    assert first.state.model_dump_json() == saved
    assert operator.notifications == [operator.operation.plan.operation_id]
    second = await executable.run("next input", previous_state=HarnessState.model_validate_json(saved))
    assert second.output_or_raise() == "foreground answer"
    assert second.state is not None
    assert "old history" not in _user_values(second.state.message_history)
    assert _user_values(second.state.message_history).count("first input") == 1
    assert _user_values(second.state.message_history).count("next input") == 1
    marker = second.state.agent_context_state.get("a13n.compaction")
    assert marker is not None and marker.data["applied_operation_id"] == operator.operation.plan.operation_id
    third = await executable.run(
        "third input", previous_state=HarnessState.model_validate_json(second.state.model_dump_json())
    )
    assert third.output_or_raise() == "foreground answer"
    assert operator.submissions == 1
    summaries = [
        part.content
        for msg in third.state.message_history
        if isinstance(msg, ModelResponse)
        for part in msg.parts
        if isinstance(part, TextPart) and part.content == "background summary"
    ]
    assert summaries == ["background summary"]


async def test_ready_result_applies_between_tool_rounds_and_keeps_delta():
    operator = _Operator()
    requests = []
    tool_calls = 0

    async def finish_summary():
        nonlocal tool_calls
        tool_calls += 1
        operator.release.set()
        await operator.task
        return "fresh tool result"

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        if len(requests) == 1:
            assert "old history" in _user_values(messages)
            yield {0: DeltaToolCall(name="finish_summary", json_args="{}", tool_call_id="finish")}
        else:
            assert "old history" not in _user_values(messages)
            assert any(
                isinstance(part, ToolReturnPart) and part.content == "fresh tool result"
                for msg in messages
                for part in msg.parts
            )
            yield "done"

    result = await _build(operator, stream, extra=(Capability(id="finish", tools=[finish_summary]),)).run(
        "current input",
        previous_state=_previous(),
    )
    assert result.output_or_raise() == "done"
    assert tool_calls == 1 and len(requests) == 2
    assert _user_values(requests[-1]).count("current input") == 1


@pytest.mark.parametrize("status", ["running", "ready"])
async def test_active_boundary_rejects_stale_operation_without_resubmission(status):
    previous = _previous()
    plan = _plan(previous)
    operator = _Operator()
    operation = CompactionOperation(
        plan=plan,
        status=status,
        result=CompactionResult.from_summary(plan, "stale summary") if status == "ready" else None,
    )
    operator.operation = operation
    changed = HarnessState.new(
        thread_id=previous.thread_id,
        message_history=(
            ModelRequest(parts=[UserPromptPart("replacement history")]),
            ModelResponse(parts=[TextPart("replacement answer")]),
        ),
    )
    saved = changed.model_dump_json()
    requests = []

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    executable = _build(operator, stream)
    for _ in range(2):
        with pytest.raises(DefinitionError) as error:
            await executable.run("continue", previous_state=changed)
        assert error.value.code == "compaction_prefix_mismatch"
    assert not requests
    assert operator.operation is operation
    assert operator.submissions == 0
    assert changed.model_dump_json() == saved


@pytest.mark.parametrize("counting", [False, True])
async def test_failed_operation_requires_verified_capacity_and_suppresses_resubmission(counting):
    previous = _previous()
    operator = _Operator()
    operation = CompactionOperation(plan=_plan(previous), status="failed", error_code="provider_unavailable")
    operator.operation = operation
    requests = []

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    executable = _build(operator, stream, counting=counting)
    for _ in range(2):
        if counting:
            result = await executable.run("continue", previous_state=previous)
            assert result.output_or_raise() == "done"
            assert "old history" in _user_values(result.state.message_history)
        else:
            with pytest.raises(DefinitionError) as error:
                await executable.run("continue", previous_state=previous)
            assert error.value.code == "compaction_capacity_unavailable"
    assert len(requests) == (2 if counting else 0)
    assert operator.operation is operation
    assert operator.submissions == 0


async def test_unknown_capacity_waits_and_waiter_cancellation_preserves_work():
    operator = _Operator()
    requests = []

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    executable = _build(operator, stream, counting=False)
    task = asyncio.create_task(executable.run("input", previous_state=_previous()))
    await asyncio.wait_for(operator.waiting.wait(), 3)
    assert not requests
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert operator.task is not None and not operator.task.done()
    operator.release.set()
    await operator.task
    assert operator.operation.status == "ready"


async def test_compacted_state_next_run_needs_no_capability_operator_or_summary_request():
    previous = _previous()
    plan = _plan(previous)
    result = CompactionResult.from_summary(plan, "supplied summary")
    requests = []

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=stream),
        output_type=str,
    )
    compacted = await apply_compaction_to_state(previous, plan=plan, result=result)
    compacted = HarnessState.model_validate_json(compacted.model_dump_json())
    assert compacted.agent_context_state.get("a13n.compaction").data["restoration_pending"] is True
    first = await executable.run("continue", previous_state=compacted)
    assert first.output_or_raise() == "done"
    assert first.state is not None
    assert first.state.agent_context_state.get("a13n.compaction").data["restoration_pending"] is False
    second = await executable.run("again", previous_state=first.state)
    assert second.output_or_raise() == "done"
    assert len(requests) == 2
    assert "old history" not in _user_values(requests[0])
    assert "continue" in _user_values(requests[1])


@pytest.mark.parametrize("media", ["text", "image", "video"])
async def test_same_run_cutoff_keeps_tool_pairs_and_replays_initial_input_once(media):
    image = BytesIO()
    Image.new("RGB", (10, 5000), "white").save(image, format="PNG")
    initial = (
        ["initial semantic input", BinaryContent(image.getvalue(), media_type="image/png")]
        if media == "image"
        else "initial semantic input"
    )
    if media == "video":
        initial = ["initial semantic input", BinaryContent(b"video", media_type="video/mp4")]
    operator = _Operator()
    requests = []

    async def step():
        if operator.task is not None:
            operator.release.set()
            await operator.task
        return "step result"

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        if len(requests) <= 3:
            yield {0: DeltaToolCall(name="step", json_args="{}", tool_call_id=f"step-{len(requests)}")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(
            model_settings={"max_tokens": 100},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
        ),
        model=_CountingModel(stream_function=stream),
        output_type=str,
        capabilities=(
            CompactionCapability(CompactionPolicy(trigger_tokens=1), background=True, operator=operator),
            Capability(id="steps", tools=[step]),
        ),
    )
    result = await executable.run(initial)
    assert result.output_or_raise() == "done"
    assert operator.submissions == 1
    assert sum("initial semantic input" in value for value in _user_values(requests[-1])) == 1
    assert len(operator.operation.plan.replay_requests) == 1
    if media == "video":
        assert "video_input_unsupported" in str(requests[-1])
        assert "video_input_unsupported" in str(operator.operation.plan.request_messages)
        assert "video_input_unsupported" not in str(result.state.message_history)
    if media == "image":

        def images(messages):
            return [
                item
                for value in _user_values(messages)
                if isinstance(value, list)
                for item in value
                if isinstance(item, BinaryContent)
            ]

        assert [item.data for item in images(result.state.message_history)] == [image.getvalue()]
        assert len(images(requests[-1])) > 1
        assert len(images(operator.operation.plan.request_messages)) > 1
    # The fourth request re-observes the applied result: the Host has not saved
    # a checkpoint and retired it, so no second operation may be submitted.
    assert len(requests) == 4
    for call_id in ("step-1", "step-2", "step-3"):
        parts = [part for message in requests[-1] for part in message.parts]
        assert sum(isinstance(part, ToolCallPart) and part.tool_call_id == call_id for part in parts) == 1
        assert sum(isinstance(part, ToolReturnPart) and part.tool_call_id == call_id for part in parts) == 1


async def test_steering_delivered_after_cutoff_is_not_replayed_twice():
    operator = _Operator()
    started = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        if len(requests) == 1:
            started.set()
            await release.wait()
        yield "done"

    executable = _build(operator, stream)
    async with executable.stream("initial", previous_state=_previous()) as run:

        async def consume():
            async for _ in run:
                pass
            return run.result

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 3)
        await run.steer("later steering")
        operator.release.set()
        await operator.task
        release.set()
        result = await consumer
    assert result.output_or_raise() == "done"
    assert len(requests) == 2
    assert _user_values(requests[-1]).count("later steering") == 1
    assert _user_values(requests[-1]).count("initial") == 1


async def test_retry_budget_is_bounded_by_captured_request_allowance():
    plan = _plan().model_copy(update={"request_limit": 1})
    calls = 0

    async def request(*args):
        nonlocal calls
        calls += 1
        raise ModelHTTPError(503, "test")

    with pytest.raises(ModelHTTPError):
        await generate_compaction(plan, request)
    assert calls == 1


@pytest.mark.parametrize("too_large_after", [False, True])
async def test_capacity_guard_waits_for_same_operation_and_rejects_oversized_merge(too_large_after):
    operator = _Operator()
    requests = []

    class LargeModel(FunctionModel):
        async def count_tokens(self, messages, model_settings, model_request_parameters):
            compacted = any(isinstance(msg, ModelResponse) and msg.metadata == {"keep": "compact"} for msg in messages)
            return RequestUsage(input_tokens=100 if compacted and not too_large_after else 9999)

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(
            model_settings={"max_tokens": 100},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
        ),
        model=LargeModel(stream_function=stream),
        output_type=str,
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=8000), background=True, operator=operator),),
    )
    run = asyncio.create_task(executable.run("input", previous_state=_previous()))
    await asyncio.wait_for(operator.waiting.wait(), 3)
    assert not requests
    operator.release.set()
    if too_large_after:
        with pytest.raises(DefinitionError) as error:
            await run
        assert error.value.code == "compaction_capacity_exceeded"
        assert not requests
    else:
        result = await run
        assert result.output_or_raise() == "done"
    assert operator.submissions == 1


async def test_state_transform_preserves_envelope_namespaces_refs_and_complete_delta():
    from a13n_harness.environment.models import EnvironmentState
    from a13n_harness.state import AgentContextStateSnapshot, CapabilityState, StoredRef

    previous = _previous()
    plan = _plan(previous)
    delta = [ModelRequest(parts=[UserPromptPart("later")]), ModelResponse(parts=[TextPart("later answer")])]
    previous = HarnessState.model_validate(
        previous.model_dump()
        | {
            "message_history": [*previous.message_history, *delta],
            "agent_context_state": AgentContextStateSnapshot(
                entries={"unknown.owner": CapabilityState(version="future", data={"opaque": [1, 2]})}
            ),
            "environment_states": {
                "workspace": EnvironmentState(provider_key="test", state_version="1", state={"id": "kept"})
            },
            "refs": [StoredRef(key="nested-child", digest="0" * 64, size=20)],
            "future": {"nested": [1, 2]},
        }
    )
    original = previous.model_dump_json()
    result = CompactionResult.from_summary(plan, "summary")
    compacted = await apply_compaction_to_state(previous, plan=plan, result=result)
    assert previous.model_dump_json() == original
    assert encode_messages(compacted.message_history[-2:]) == encode_messages(delta)
    assert compacted.environment_states == previous.environment_states
    assert compacted.refs == previous.refs
    assert compacted.agent_context_state.get("unknown.owner") == previous.agent_context_state.get("unknown.owner")
    assert compacted.model_extra == previous.model_extra
    compacted.model_extra["future"]["nested"].append(3)
    assert previous.model_dump_json() == original
    compacted = HarnessState.model_validate_json(compacted.model_dump_json())
    assert await apply_compaction_to_state(compacted, plan=plan, result=result) is compacted
    changed = plan.model_copy(update={"run_id": "different"})
    with pytest.raises(DefinitionError, match="identity was reused"):
        await apply_compaction_to_state(compacted, plan=changed, result=CompactionResult.from_summary(changed, "other"))


@pytest.mark.parametrize("mismatch", ["thread", "operation", "digest", "prefix", "pending"])
async def test_state_transform_rejects_stale_or_incomplete_work(mismatch):
    previous = _previous()
    plan = _plan(previous)
    result = CompactionResult.from_summary(plan, "summary")
    if mismatch == "thread":
        previous = previous.fork()
    elif mismatch == "operation":
        result = result.model_copy(update={"operation_id": "other"})
    elif mismatch == "digest":
        result = result.model_copy(update={"plan_digest": "other"})
    elif mismatch == "prefix":
        previous = HarnessState.new(thread_id=previous.thread_id)
    else:
        previous = previous.model_copy(
            update={
                "message_history_json": encode_messages(
                    [*previous.message_history, ModelResponse(parts=[ToolCallPart("read", {}, "pending")])]
                )
            }
        )
    original = previous.model_dump_json()
    with pytest.raises(DefinitionError):
        await apply_compaction_to_state(previous, plan=plan, result=result)
    assert previous.model_dump_json() == original


async def test_state_transform_hydrates_prefix_but_preserves_stored_suffix():
    from a13n_harness.errors import StateError
    from a13n_harness.storage import RunStorage

    from .test_state_store import MemoryStore

    store = MemoryStore()
    image = BinaryContent(b"image" * 200, media_type="image/png")
    prefix = [ModelRequest(parts=[UserPromptPart([image])]), ModelResponse(parts=[TextPart("seen")])]
    previous = HarnessState.new(message_history=prefix)
    plan = _plan(previous)
    result = CompactionResult.from_summary(plan, "summary")
    delta = [ModelRequest(parts=[UserPromptPart([image])]), ModelResponse(parts=[TextPart("again")])]
    previous = previous.model_copy(update={"message_history_json": encode_messages([*prefix, *delta])})
    previous = await RunStorage(store).export(previous)
    saved = previous.model_dump_json()
    with pytest.raises(StateError, match="no state store"):
        await apply_compaction_to_state(previous, plan=plan, result=result)
    compacted = await apply_compaction_to_state(previous, plan=plan, result=result, state_store=store)
    assert previous.model_dump_json() == saved
    assert compacted.refs == previous.refs
    assert encode_messages(compacted.message_history[-2:]) == encode_messages(previous.message_history[-2:])
    restored = await RunStorage(store).resolve_messages(compacted.message_history)
    assert encode_messages(restored[-2:]) == encode_messages(delta)


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_restoration_keeps_pending_provenance_for_retry(cancel):
    from a13n_harness.capabilities.context import ContextRestoredEvent
    from pydantic_ai.capabilities import AbstractCapability

    previous = _previous()
    plan = _plan(previous)
    compacted = await apply_compaction_to_state(
        previous, plan=plan, result=CompactionResult.from_summary(plan, "summary")
    )
    contexts = []
    requests = []

    class Listener(AbstractCapability):
        async def on_event(self, ctx, *, event):
            if isinstance(event, ContextRestoredEvent):
                contexts.append(ctx)
                if len(contexts) == 1:
                    raise asyncio.CancelledError() if cancel else RuntimeError("listener failed")

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=stream), output_type=str, capabilities=(Listener(),)
    )
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await executable.run("continue", previous_state=compacted)
    else:
        failed = await executable.run("continue", previous_state=compacted)
        assert failed.state.agent_context_state.get("a13n.compaction").data["restoration_pending"] is True
    assert not requests
    from a13n_harness.compaction import _CompactionState

    marker = await contexts[0].deps.state.read("a13n.compaction", _CompactionState, version="1")
    assert marker.restoration_pending
    second = await executable.run("continue", previous_state=compacted)
    assert second.output_or_raise() == "done"
    assert len(contexts) == 2
    assert second.state.agent_context_state.get("a13n.compaction").data["restoration_pending"] is False


async def test_state_restoration_resets_memory_before_first_projection():
    from a13n_harness.capabilities import FileMemoryCapability, FileMount, MemoryCursors

    from .memory_helpers import FakeFileStore
    from .test_memory_context import _blocks, _parse

    store = FakeFileStore({"README.md": "remember me", "other.md": "index entry"})
    cursors = MemoryCursors()
    requests = []

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=stream),
        output_type=str,
        capabilities=(
            FileMemoryCapability([FileMount("user", store, "write", always_load=("README.md",))], cursors=cursors),
        ),
    )
    first = await executable.run("hello")
    assert cursors.snapshot()
    plan = _plan(first.state)
    compacted = await apply_compaction_to_state(
        first.state, plan=plan, result=CompactionResult.from_summary(plan, "summary")
    )
    second = await executable.run("continue", previous_state=compacted)
    assert second.output_or_raise() == "done"
    blocks = _blocks(requests[-1])
    assert len(blocks) == 1
    assert "remember me" in blocks[0] and "other.md" in blocks[0]
    assert _parse(blocks[0])[1] == _parse(_blocks(requests[0])[0])[1]


async def test_state_restoration_uses_current_discovery_and_codeact_tools():
    from a13n_harness.capabilities import CodeActCapability
    from pydantic_ai.capabilities import ToolSearch

    from .test_tool_discovery_retention import _run_calls, _source

    effects = []
    capabilities = (_source(effects), ToolSearch(strategy="keywords"), CodeActCapability())
    first, _ = await _run_calls(capabilities, [("search_tools", {"queries": ["double"]}), ("double", {"value": 20})])
    plan = _plan(first.state)
    compacted = await apply_compaction_to_state(
        first.state, plan=plan, result=CompactionResult.from_summary(plan, "summary")
    )
    second, snapshots = await _run_calls(
        capabilities, [("run_code", {"code": "await double(value=21)"})], previous_state=compacted
    )
    assert second.output_or_raise()
    assert effects == [20, 21]
    assert "double" in snapshots[0][1].model_request_parameters.revealed_tool_names
    for tool in snapshots[0][1].function_tools:
        if tool.name in {"run_code", "run_program"}:
            assert "async def double" in tool.description
            assert "async def triple" not in tool.description

    _, snapshots = await _run_calls((CodeActCapability(),), [], previous_state=compacted)
    assert not snapshots[0][1].model_request_parameters.revealed_tool_names
    for tool in snapshots[0][1].function_tools:
        if tool.name in {"run_code", "run_program"}:
            assert "async def double" not in tool.description


@pytest.mark.parametrize("too_large", [False, True])
async def test_state_restoration_checks_capacity_after_current_projection_and_media(too_large):
    from a13n_harness.capabilities import FileMemoryCapability, FileMount
    from pydantic_ai.messages import VideoUrl

    from .memory_helpers import FakeFileStore

    previous = _previous()
    plan = _plan(previous)
    delta = [ModelRequest(parts=[UserPromptPart([VideoUrl("https://example.com/video.mp4")])])]
    previous = previous.model_copy(
        update={"message_history_json": encode_messages([*previous.message_history, *delta])}
    )
    compacted = await apply_compaction_to_state(
        previous, plan=plan, result=CompactionResult.from_summary(plan, "summary")
    )
    counted = []
    requests = []

    class CountingModel(FunctionModel):
        async def count_tokens(self, messages, model_settings, model_request_parameters):
            counted.append(deepcopy(messages))
            assert "fresh memory" in str(messages)
            assert "video_url_requires_materialization" in str(messages)
            assert not any(
                isinstance(value, VideoUrl)
                for values in _user_values(messages)
                if isinstance(values, list)
                for value in values
            )
            return RequestUsage(input_tokens=10000 if too_large else 100)

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(
            model_settings={"max_tokens": 100},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
        ),
        model=CountingModel(stream_function=stream),
        output_type=str,
        capabilities=(
            FileMemoryCapability(
                [FileMount("user", FakeFileStore({"README.md": "fresh memory"}), "write", always_load=("README.md",))]
            ),
        ),
    )
    if too_large:
        with pytest.raises(DefinitionError) as error:
            await executable.run("continue", previous_state=compacted)
        assert error.value.code == "compaction_capacity_exceeded"
        assert not requests
    else:
        result = await executable.run("continue", previous_state=compacted)
        assert result.output_or_raise() == "done"
        assert "video_url_requires_materialization" in str(requests[0])
        assert "video_url_requires_materialization" not in str(result.state.message_history)
        assert "https://example.com/video.mp4" in str(result.state.message_history)
    assert len(counted) == 1
