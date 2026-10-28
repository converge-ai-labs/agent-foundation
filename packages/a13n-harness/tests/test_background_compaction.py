from __future__ import annotations

import asyncio
from copy import deepcopy
from io import BytesIO

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessModelCharacteristics, HarnessState
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.compaction import (
    BackgroundCompactionPolicy,
    CompactionInput,
    CompactionOperation,
    CompactionOperator,
    CompactionResult,
    _CompactionState,
    _prefix_digest,
    _prepare_compaction,
    apply_compaction,
    compaction_disposition,
    generate_compaction,
)
from a13n_harness.errors import DefinitionError
from a13n_harness.model_calls import ModelCallCheckError
from a13n_harness.state import encode_messages
from PIL import Image
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ModelConnectionError, ModelHTTPError
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
from pydantic_ai.usage import RequestUsage, UsageLimits

pytestmark = pytest.mark.anyio


async def _pending_checkpoint(state, *, result, state_store=None):
    """Construct the checkpoint saved after replacement but before runtime restoration."""
    from a13n_harness.state import AgentContextState
    from a13n_harness.storage import RunStorage

    namespaces = AgentContextState(await RunStorage(state_store).resolve_context(state.agent_context_state))
    previous = await namespaces.read("a13n.compaction", _CompactionState, version="1")
    messages = await RunStorage(state_store).resolve_messages(state.message_history)
    messages, marker = _prepare_compaction(result, thread_id=state.thread_id, messages=messages, previous=previous)
    if marker is None:
        return state
    await namespaces.write("a13n.compaction", marker, version="1")
    return state.model_copy(
        deep=True,
        update={"message_history_json": encode_messages(messages), "agent_context_state": await namespaces.snapshot()},
    )


def _previous():
    return HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("old history")]),
            ModelResponse(parts=[TextPart("old answer")], usage=RequestUsage(input_tokens=9000)),
        )
    )


def _input(previous=None):
    previous = previous or _previous()
    return CompactionInput.create(
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


async def test_input_snapshots_provider_tool_definitions_without_host_metadata():
    from pydantic_ai.tools import ToolDefinition

    # Host metadata can own resources that cannot be copied or serialized.
    tool = ToolDefinition(
        name="read",
        parameters_json_schema={"type": "object"},
        metadata={"owner": asyncio.get_running_loop()},
    )
    previous = _previous()
    input = CompactionInput.create(
        operation_id="compaction-test",
        thread_id=previous.thread_id,
        run_id="run-test",
        model_name="test",
        provider_name="function",
        messages=previous.message_history,
        parameters=ModelRequestParameters(function_tools=[tool]),
    )
    frozen = input.model_dump_json()
    tool.parameters_json_schema["description"] = "later mutation"
    restored = CompactionInput.model_validate_json(frozen).parameters.function_tools[0]
    assert restored.name == "read"
    assert restored.parameters_json_schema == {"type": "object"}
    assert restored.metadata is None
    assert tool.metadata is not None


async def test_input_roundtrip_detaches_native_values_and_result_preserves_delta():
    previous = _previous()
    messages = list(previous.message_history)
    replay = ModelRequest(parts=[UserPromptPart(["image", BinaryContent(b"image", media_type="image/png")])])
    messages += [replay, ModelResponse(parts=[TextPart("seen")])]
    input = CompactionInput.create(
        operation_id="compaction-test",
        thread_id=previous.thread_id,
        run_id="run-test",
        model_name="test",
        provider_name="function",
        messages=messages,
        retained_requests=[replay],
        parameters=ModelRequestParameters(),
    )
    canonical = deepcopy(messages)
    frozen = input.model_dump_json()
    messages[0].parts = []
    input.request_messages[0].parts = []
    input.basis.replay_requests[0].parts = []
    input = CompactionInput.model_validate_json(frozen)
    assert input.model_dump_json() == frozen
    result = CompactionResult.from_summary(input.basis, "summary")
    delta = [
        ModelRequest(parts=[UserPromptPart("later steering")]),
        ModelResponse(parts=[ToolCallPart("read", {}, "call-1")]),
        ModelRequest(parts=[ToolReturnPart("read", "new result", "call-1")]),
    ]
    latest = [*canonical, *delta]
    before = encode_messages(latest)
    merged = apply_compaction(result, thread_id=input.basis.thread_id, messages=latest)
    assert encode_messages(merged[-3:]) == encode_messages(delta)
    assert _user_values(merged).count("later steering") == 1
    assert input.basis.replay_requests[0].parts[0].content in _user_values(merged)
    assert encode_messages(latest) == before


@pytest.mark.parametrize("mismatch", ["thread", "digest", "prefix", "pending"])
async def test_application_rejects_incompatible_result_without_mutation(mismatch):
    input = _input()
    result = CompactionResult.from_summary(input.basis, "summary")
    messages = input.request_messages[:-1]
    thread_id = input.basis.thread_id
    if mismatch == "thread":
        thread_id = "thread-other"
    elif mismatch == "digest":
        result = result.model_copy(update={"basis": result.basis.model_copy(update={"prefix_digest": "other"})})
    elif mismatch == "prefix":
        messages[0].parts = [UserPromptPart("cleared or handoff history")]
    else:
        messages.append(ModelResponse(parts=[ToolCallPart("read", {}, "pending")]))
    before = encode_messages(messages)
    with pytest.raises(DefinitionError):
        apply_compaction(result, thread_id=thread_id, messages=messages)
    assert encode_messages(messages) == before


async def test_generator_retries_same_snapshot_with_no_mutable_aliases():
    input = _input()
    calls = []
    delays = []

    async def request(actual):
        assert actual == input
        messages, settings, parameters = actual.request_messages, actual.settings, actual.parameters
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

    result = await generate_compaction(input, request, sleep=sleep)
    assert result == CompactionResult.from_summary(input.basis, "summary")
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
        await generate_compaction(_input(), request)
    assert calls == 1


@pytest.mark.parametrize("parts", [[TextPart("  ")], [ToolCallPart("dangerous", {}, "call")]])
async def test_generator_rejects_invalid_summary_without_dispatching_tools(parts):
    async def request(*args):
        return ModelResponse(parts=parts)

    with pytest.raises(DefinitionError, match="Compaction returned"):
        await generate_compaction(_input(), request)


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

    async def submit(self, input):
        if self.operation is not None:
            return self.operation
        self.submissions += 1
        self.operation = CompactionOperation(input=input, status="running")
        self.task = asyncio.create_task(self._execute(input))
        return self.operation

    async def _execute(self, input):
        async def request(actual):
            self.started.set()
            await self.release.wait()
            return ModelResponse(parts=[TextPart("background summary")])

        result = await generate_compaction(input, request)
        self.operation = CompactionOperation(input=input, status="ready", result=result)
        self.notifications.append(result.basis.operation_id)
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
    assert operator.notifications == [operator.operation.input.basis.operation_id]
    second = await executable.run("next input", previous_state=HarnessState.model_validate_json(saved))
    assert second.output_or_raise() == "foreground answer"
    assert second.state is not None
    assert "old history" not in _user_values(second.state.message_history)
    assert _user_values(second.state.message_history).count("first input") == 1
    assert _user_values(second.state.message_history).count("next input") == 1
    marker = second.state.agent_context_state.get("a13n.compaction")
    assert marker is not None and marker.data["operation_id"] == operator.operation.input.basis.operation_id
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
async def test_active_boundary_skips_stale_operation_without_resubmission(status):
    previous = _previous()
    input = _input(previous)
    operator = _Operator()
    operation = CompactionOperation(
        input=input,
        status=status,
        result=CompactionResult.from_summary(input.basis, "stale summary") if status == "ready" else None,
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
        resumed = await executable.run("continue", previous_state=changed)
        assert resumed.output_or_raise() == "done"
        assert await compaction_disposition(resumed.state, basis=input.basis) == "skipped"
    assert len(requests) == 2
    assert operator.operation is operation
    assert operator.submissions == 0
    assert changed.model_dump_json() == saved


@pytest.mark.parametrize("counting", [False, True])
async def test_failed_operation_falls_back_once_and_acknowledges_on_next_run(counting):
    previous = _previous()
    operator = _Operator()
    operation = CompactionOperation(input=_input(previous), status="failed", error_code="provider_unavailable")
    operator.operation = operation
    requests = []

    async def stream(messages, info):
        requests.append(messages)
        yield "fallback summary" if _COMPACTION_PROMPT in _user_values(messages) else "done"

    executable = _build(operator, stream, counting=counting)
    for prompt in ("continue", "next run"):
        result = await executable.run(prompt, previous_state=previous)
        assert result.output_or_raise() == "done"
        assert "old history" not in _user_values(result.state.message_history)
        assert await compaction_disposition(result.state, basis=operation.input.basis) == "skipped"
        previous = HarnessState.model_validate_json(result.state.model_dump_json())
    assert len(requests) == 3
    assert operator.operation is operation
    assert operator.submissions == 0


async def test_unknown_capacity_waits_and_waiter_cancellation_preserves_work():
    operator = _Operator()
    requests = []

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    previous = _previous()
    await operator.submit(_input(previous))
    executable = _build(operator, stream, counting=False)
    task = asyncio.create_task(executable.run("input", previous_state=previous))
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
    input = _input(previous)
    result = CompactionResult.from_summary(input.basis, "supplied summary")
    requests = []

    async def stream(messages, info):
        requests.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=stream),
        output_type=str,
    )
    first = await executable.run("continue", previous_state=previous, compaction_result=result)
    assert await compaction_disposition(first.state, basis=result.basis) == "applied"
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
    assert len(operator.operation.input.basis.replay_requests) == 1
    if media == "video":
        assert "video_input_unsupported" in str(requests[-1])
        assert "video_input_unsupported" in str(operator.operation.input.request_messages)
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
        assert len(images(operator.operation.input.request_messages)) > 1
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
    input = _input().model_copy(update={"request_limit": 1})
    calls = 0

    async def request(*args):
        nonlocal calls
        calls += 1
        raise ModelHTTPError(503, "test")

    with pytest.raises(ModelHTTPError):
        await generate_compaction(input, request)
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
    previous = _previous()
    await operator.submit(_input(previous))
    run = asyncio.create_task(executable.run("input", previous_state=previous))
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
    input = _input(previous)
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
    result = CompactionResult.from_summary(input.basis, "summary")
    compacted = await _pending_checkpoint(previous, result=result)
    assert previous.model_dump_json() == original
    assert encode_messages(compacted.message_history[-2:]) == encode_messages(delta)
    assert compacted.environment_states == previous.environment_states
    assert compacted.refs == previous.refs
    assert compacted.agent_context_state.get("unknown.owner") == previous.agent_context_state.get("unknown.owner")
    assert compacted.model_extra == previous.model_extra
    compacted.model_extra["future"]["nested"].append(3)
    assert previous.model_dump_json() == original
    compacted = HarnessState.model_validate_json(compacted.model_dump_json())
    assert await _pending_checkpoint(compacted, result=result) is compacted
    changed = input.basis.model_copy(update={"prefix_digest": "different"})
    with pytest.raises(DefinitionError, match="identity was reused"):
        await _pending_checkpoint(compacted, result=CompactionResult.from_summary(changed, "other"))


@pytest.mark.parametrize("mismatch", ["thread", "digest", "prefix", "pending"])
async def test_preparation_distinguishes_stale_incomplete_and_wrong_thread(mismatch):
    previous = _previous()
    input = _input(previous)
    result = CompactionResult.from_summary(input.basis, "summary")
    if mismatch == "thread":
        previous = previous.fork()
    elif mismatch == "digest":
        result = result.model_copy(update={"basis": result.basis.model_copy(update={"prefix_digest": "other"})})
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
    if mismatch == "thread":
        with pytest.raises(DefinitionError):
            await _pending_checkpoint(previous, result=result)
    else:
        prepared = await _pending_checkpoint(previous, result=result)
        assert await compaction_disposition(prepared, basis=result.basis) == (
            None if mismatch == "pending" else "skipped"
        )
    assert previous.model_dump_json() == original


async def test_joint_resume_hydrates_stored_history_and_preserves_unrelated_namespaces():
    from a13n_harness.errors import StateError
    from a13n_harness.storage import RunStorage

    from .test_state_store import MemoryStore

    store = MemoryStore()
    image = BinaryContent(b"image" * 200, media_type="image/png")
    prefix = [ModelRequest(parts=[UserPromptPart([image])]), ModelResponse(parts=[TextPart("seen")])]
    previous = HarnessState.new(message_history=prefix)
    input = _input(previous)
    result = CompactionResult.from_summary(input.basis, "summary")
    delta = [ModelRequest(parts=[UserPromptPart([image])]), ModelResponse(parts=[TextPart("again")])]
    previous = previous.model_copy(update={"message_history_json": encode_messages([*prefix, *delta])})
    import json

    from a13n_harness import RunBindings
    from a13n_harness.state import AgentContextStateSnapshot, CapabilityState

    opaque = CapabilityState(version="future", data={"retained": json.loads(encode_messages(prefix))})
    previous = previous.model_copy(
        update={
            "agent_context_state": AgentContextStateSnapshot(entries={"unknown.owner": opaque}),
        }
    )
    previous = await RunStorage(store).export(previous)
    saved = previous.model_dump_json()
    requests = []

    async def stream(messages, _info):
        requests.append(deepcopy(messages))
        yield "done"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
    with pytest.raises(StateError, match="no state store"):
        await executable.run("next", previous_state=previous, compaction_result=result)
    assert not requests
    with pytest.raises(StateError, match="no state store"):
        await compaction_disposition(previous, basis=result.basis)

    completed = await executable.run(
        "next",
        previous_state=previous,
        compaction_result=result,
        bindings=RunBindings.embedded(state_store=store),
    )
    assert completed.output_or_raise() == "done"
    assert completed.state is not None
    assert previous.model_dump_json() == saved
    restored = await RunStorage(store).resolve_messages(completed.state.message_history)
    retained_media = [
        item
        for content in _user_values(restored)
        if isinstance(content, list)
        for item in content
        if isinstance(item, BinaryContent)
    ]
    assert len(retained_media) == 1
    assert retained_media[0].data == image.data
    assert any(encode_messages([message]) == encode_messages([delta[1]]) for message in restored)
    namespaces = await RunStorage(store).resolve_context(completed.state.agent_context_state)
    assert namespaces.get("unknown.owner") == opaque
    assert await compaction_disposition(completed.state, basis=result.basis, state_store=store) == "applied"


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_restoration_keeps_pending_provenance_for_retry(cancel):
    from a13n_harness.capabilities.context import ContextRestoredEvent
    from pydantic_ai.capabilities import AbstractCapability

    previous = _previous()
    input = _input(previous)
    result = CompactionResult.from_summary(input.basis, "summary")
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
            await executable.run("continue", previous_state=previous, compaction_result=result)
    else:
        failed = await executable.run("continue", previous_state=previous, compaction_result=result)
        assert failed.state.agent_context_state.get("a13n.compaction").data["restoration_pending"] is True
    assert not requests
    from a13n_harness.compaction import _CompactionState

    marker = await contexts[0].deps.state.read("a13n.compaction", _CompactionState, version="1")
    assert marker.restoration_pending
    compacted = await contexts[0].deps.export_state(contexts[0].messages)
    assert await compaction_disposition(compacted, basis=result.basis) == "applied"
    second = await executable.run("continue", previous_state=compacted, compaction_result=result)
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
    input = _input(first.state)
    second = await executable.run(
        "continue", previous_state=first.state, compaction_result=CompactionResult.from_summary(input.basis, "summary")
    )
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
    input = _input(first.state)
    compacted = await _pending_checkpoint(first.state, result=CompactionResult.from_summary(input.basis, "summary"))
    second, snapshots = await _run_calls(
        capabilities, [("run_code", {"code": "await double(value=21)"})], previous_state=compacted
    )
    assert second.output_or_raise()
    assert effects == [20, 21]
    assert "double" in snapshots[0][1].model_request_parameters.revealed_tool_names
    assert snapshots[0][1].instructions.count("async def double") == 1
    assert "async def triple" not in snapshots[0][1].instructions

    _, snapshots = await _run_calls((CodeActCapability(),), [], previous_state=compacted)
    assert not snapshots[0][1].model_request_parameters.revealed_tool_names
    assert "async def double" not in snapshots[0][1].instructions


@pytest.mark.parametrize("too_large", [False, True])
async def test_state_restoration_checks_capacity_after_current_projection_and_media(too_large):
    from a13n_harness.capabilities import FileMemoryCapability, FileMount
    from pydantic_ai.messages import VideoUrl

    from .memory_helpers import FakeFileStore

    previous = _previous()
    input = _input(previous)
    delta = [ModelRequest(parts=[UserPromptPart([VideoUrl("https://example.com/video.mp4")])])]
    previous = previous.model_copy(
        update={"message_history_json": encode_messages([*previous.message_history, *delta])}
    )
    compacted = await _pending_checkpoint(previous, result=CompactionResult.from_summary(input.basis, "summary"))
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


@pytest.mark.parametrize(
    "field,value",
    [("operation_id", "other"), ("thread_id", "other"), ("prefix_digest", "other"), ("replay_json", "[]")],
)
async def test_operation_rejects_a_result_with_a_different_basis(field, value):
    input = _input()
    if field == "replay_json":
        value = encode_messages([ModelRequest(parts=[UserPromptPart("different replay")])]).decode()
    basis = input.basis.model_copy(update={field: value})
    with pytest.raises(ValueError, match="submitted basis"):
        CompactionOperation(input=input, status="ready", result=CompactionResult.from_summary(basis, "summary"))


async def test_serialized_result_applies_without_generation_input_and_uses_canonical_prefix():
    from pydantic_ai.messages import SystemPromptPart

    original = BinaryContent(b"original-media", media_type="image/png")
    replay = ModelRequest(parts=[UserPromptPart(["retained input", original])])
    prefix = [
        ModelRequest(parts=[SystemPromptPart("canonical system"), UserPromptPart("prefix-only history")]),
        ModelResponse(parts=[TextPart("earlier reference")]),
        replay,
        ModelResponse(parts=[TextPart("latest reference")]),
    ]
    state = HarnessState.new(message_history=prefix)
    input = CompactionInput.create(
        operation_id="compaction-detached",
        thread_id=state.thread_id,
        run_id="run-source",
        model_id="selected-model",
        model_name="test",
        provider_name="function",
        messages=prefix,
        summary_messages=[ModelRequest(parts=[UserPromptPart("projected request")])],
        retained_requests=[replay],
        parameters=ModelRequestParameters(),
    )

    async def request(actual):
        assert actual.model_id == "selected-model"
        assert actual.run_id == "run-source"
        assert "projected request" in _user_values(actual.request_messages)
        assert "prefix-only history" not in actual.model_dump_json()
        return ModelResponse(parts=[TextPart("summary")])

    result = await generate_compaction(input, request)
    serialized = result.model_dump_json()
    assert "projected request" not in serialized
    assert "selected-model" not in serialized and "run-source" not in serialized
    del input
    result = CompactionResult.model_validate_json(serialized)
    result.basis.replay_requests[0].parts = []
    assert result.model_dump_json() == serialized
    delta = [ModelRequest(parts=[UserPromptPart("use the last answer")])]
    latest = state.model_copy(update={"message_history_json": encode_messages([*prefix, *delta])})
    compacted = await _pending_checkpoint(latest, result=result)
    active = apply_compaction(result, thread_id=state.thread_id, messages=latest.message_history)
    for messages in (active, compacted.message_history):
        assert messages[0].parts[0] == prefix[0].parts[0]
        assert "latest reference" in str(messages)
        assert "earlier reference" not in str(messages)
        retained = next(
            value for value in _user_values(messages) if isinstance(value, list) and value[0] == "retained input"
        )
        assert len(retained) == 2
        assert isinstance(retained[1], BinaryContent)
        assert retained[1].data == original.data and retained[1].media_type == original.media_type
        assert encode_messages(messages[-1:]) == encode_messages(delta)
    assert await _pending_checkpoint(compacted, result=result) is compacted


async def test_result_application_rejects_a_cutoff_inside_a_complete_tool_pair():
    state = _previous()
    prefix = [*state.message_history, ModelResponse(parts=[ToolCallPart("read", {}, "call")])]
    messages = [*prefix, ModelRequest(parts=[ToolReturnPart("read", "value", "call")])]
    basis = _input(state).basis.model_copy(
        update={
            "prefix_length": len(prefix),
            "prefix_digest": _prefix_digest(prefix),
        }
    )
    result = CompactionResult.from_summary(basis, "summary")
    before = encode_messages(messages)
    with pytest.raises(DefinitionError) as error:
        apply_compaction(result, thread_id=state.thread_id, messages=messages)
    assert error.value.code == "compaction_boundary_pending"
    assert encode_messages(messages) == before


@pytest.mark.parametrize("middle_prompt", [None, ("second", "first", "added")])
@pytest.mark.parametrize("current_prompt", [None, ("current", "authority")])
async def test_joint_resume_applies_across_intervening_system_prompt_changes(middle_prompt, current_prompt):
    from a13n_harness.content import ContentItem, ContentMetadata, request_input_content
    from a13n_harness.events import HarnessRunResultEvent
    from pydantic_ai.messages import SystemPromptPart

    requests = []

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        yield "1. First point\n2. Second point"

    def build(prompt):
        return HarnessBuilder().build(
            AgentSpec(system_prompt=prompt), model=FunctionModel(stream_function=stream), output_type=str
        )

    image = BinaryContent(b"original", media_type="image/png")
    first = await build(("first", "second")).run(
        [
            ContentItem(image, ContentMetadata(source_id="attachment", display=False, attachment_id="asset")),
            "user text mentioning system prompts is real history",
        ]
    )
    generation = _input(first.state)
    frozen_request = generation.request_json
    result = CompactionResult.from_summary(generation.basis, "prior history summary")
    middle = await build(middle_prompt).run("intervening input", previous_state=first.state)
    assert (
        _prefix_digest(middle.state.message_history[: generation.basis.prefix_length]) == generation.basis.prefix_digest
    )
    original = middle.state.model_dump_json()
    async with build(current_prompt).stream(
        "explain the second point", previous_state=middle.state, compaction_result=result
    ) as run:
        events = [event async for event in run]
    final = next(event.result for event in events if isinstance(event, HarnessRunResultEvent))
    assert final.output_or_raise()
    assert await compaction_disposition(final.state, basis=result.basis) == "applied"
    assert middle.state.model_dump_json() == original
    assert generation.request_json == frozen_request
    assert _user_values(requests[-1]).count("intervening input") == 1
    assert _user_values(requests[-1]).count("explain the second point") == 1
    assert "prior history summary" in str(requests[-1])
    systems = [
        part.content
        for message in final.state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    ]
    assert systems == list(current_prompt or ())
    retained = [
        item
        for message in middle.state.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if isinstance(item.value, BinaryContent)
    ]
    assert retained[0].metadata.source_id == "attachment"
    assert retained[0].metadata.display is False
    assert retained[0].metadata.model_extra == {"attachment_id": "asset"}


async def test_joint_resume_empty_suffix_uses_latest_assistant_for_new_input_and_does_not_replay_it():
    requests = []

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        yield "done"

    previous = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("old question")]),
            ModelResponse(parts=[TextPart("wrong earlier answer")]),
            ModelRequest(parts=[UserPromptPart("give numbered points")]),
            ModelResponse(parts=[TextPart("1. latest first\n2. latest second")]),
        ]
    )
    result = CompactionResult.from_summary(_input(previous).basis, "summary")
    executable = HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=stream), output_type=str)
    for _ in range(2):  # Lost checkpoint commit: retry against the original checkpoint.
        resumed = await executable.run("explain the second point", previous_state=previous, compaction_result=result)
        assert resumed.output_or_raise() == "done"
        assert "latest second" in str(requests[-1])
        assert "wrong earlier answer" not in str(requests[-1])
        assert _user_values(requests[-1]).count("explain the second point") == 1
        assert await compaction_disposition(resumed.state, basis=result.basis) == "applied"
    # Commit succeeded but acknowledgement was lost: duplicate before prefix validation.
    saved = HarnessState.model_validate_json(resumed.state.model_dump_json())
    repeated = await executable.run("continue", previous_state=saved, compaction_result=result)
    assert repeated.output_or_raise() == "done"
    assert (
        sum(
            isinstance(part, TextPart) and part.content == "summary"
            for msg in repeated.state.message_history
            for part in msg.parts
        )
        == 1
    )
    conflict = result.model_copy(update={"basis": result.basis.model_copy(update={"prefix_digest": "conflict"})})
    with pytest.raises(DefinitionError, match="identity was reused"):
        await executable.run("continue", previous_state=saved, compaction_result=conflict)


@pytest.mark.parametrize("mutation", ["user", "assistant", "short"])
async def test_joint_resume_skips_changed_history_and_persists_duplicate_disposition(mutation):
    previous = _previous()
    result = CompactionResult.from_summary(_input(previous).basis, "stale summary")
    messages = list(previous.message_history)
    if mutation == "user":
        messages[0].parts = [UserPromptPart("changed system prompt mentioned by user")]
    elif mutation == "assistant":
        messages[1].parts = [TextPart("edited assistant response")]
    else:
        messages = []
    previous = previous.model_copy(update={"message_history_json": encode_messages(messages)})

    async def stream(messages, info):
        assert "stale summary" not in str(messages)
        yield "done"

    executable = HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=stream), output_type=str)
    for _ in range(2):
        resumed = await executable.run("continue", previous_state=previous, compaction_result=result)
        assert resumed.output_or_raise() == "done"
        assert await compaction_disposition(resumed.state, basis=result.basis) == "skipped"
        previous = HarnessState.model_validate_json(resumed.state.model_dump_json())


async def test_prefix_normalization_preserves_empty_slots_annotations_and_non_system_fields():
    from a13n_harness.content import ContentItem, ContentMetadata, annotate_prompt, replace_request_parts, request_parts
    from pydantic_ai.messages import SystemPromptPart

    request = annotate_prompt(
        ModelRequest(parts=[SystemPromptPart("old"), UserPromptPart("input")]),
        1,
        [ContentItem("input", ContentMetadata(source_id="user", attachment_id="asset"))],
    )
    messages = [
        ModelRequest(parts=[SystemPromptPart("system only")]),
        request,
        ModelResponse(parts=[TextPart("answer")]),
    ]
    digest = _prefix_digest(messages)
    changed = [
        replace_request_parts(
            msg, [(part, items) for part, items in request_parts(msg) if not isinstance(part, SystemPromptPart)]
        )
        if isinstance(msg, ModelRequest)
        else msg
        for msg in messages
    ]
    assert _prefix_digest(changed) == digest
    assert len(changed) == 3 and len(changed[0].parts) == 0
    assert _prefix_digest(changed[1:]) != digest
    annotated = annotate_prompt(changed[1], 0, [ContentItem("input", ContentMetadata(source_id="different"))])
    assert _prefix_digest([changed[0], annotated, changed[2]]) != digest
    changed[2] = ModelResponse(parts=[TextPart("answer")], model_name="different")
    assert _prefix_digest(changed) != digest


async def test_joint_resume_defers_unclosed_tools_then_applies_after_normal_recovery():
    previous = _previous()
    result = CompactionResult.from_summary(_input(previous).basis, "summary")
    pending = previous.model_copy(
        update={
            "message_history_json": encode_messages(
                [
                    *previous.message_history,
                    ModelRequest(parts=[UserPromptPart("recover the work")]),
                    ModelResponse(parts=[ToolCallPart("recover", {}, "pending")]),
                ]
            )
        }
    )
    calls = []

    def recover():
        calls.append("executed")
        return "recovered result"

    async def stream(messages, info):
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=stream),
        output_type=str,
        capabilities=(Capability(tools=[recover]),),
    )
    recovered = await executable.run(previous_state=pending, compaction_result=result, tool_recovery="always")
    assert recovered.output_or_raise() == "done"
    assert calls == ["executed"]
    assert await compaction_disposition(recovered.state, basis=result.basis) is None
    resumed = await executable.run("continue", previous_state=recovered.state, compaction_result=result)
    assert resumed.output_or_raise() == "done"
    assert await compaction_disposition(resumed.state, basis=result.basis) == "applied"
    assert calls == ["executed"]
    assert "recovered result" in str(resumed.state.message_history)


async def test_joint_resume_defers_exact_approval_then_applies_on_later_run():
    from a13n_harness import DeferredToolResume

    from .test_deferred_resume import _build

    previous = _previous()
    result = CompactionResult.from_summary(_input(previous).basis, "old summary")
    executed = []
    executable = _build(executed)
    suspended = await executable.run("go", previous_state=previous)
    assert suspended.status == "suspended"
    assert suspended.state is not None and suspended.deferred is not None
    resumed = await executable.run(
        previous_state=suspended.state,
        compaction_result=result,
        deferred_resume=DeferredToolResume(
            suspended.deferred,
            suspended.deferred.build_results(approve_all=True),
        ),
    )
    assert resumed.status == "completed"
    assert resumed.state is not None
    assert executed == [1]
    assert await compaction_disposition(resumed.state, basis=result.basis) is None
    assert "old history" in _user_values(resumed.state.message_history)

    later = await executable.run("continue", previous_state=resumed.state, compaction_result=result)
    assert later.status == "completed"
    assert later.state is not None
    assert await compaction_disposition(later.state, basis=result.basis) == "applied"
    assert executed == [1]


@pytest.mark.parametrize("capacity", ["room", "tight", "unknown"])
@pytest.mark.parametrize("triggered", [False, True])
async def test_no_operation_chooses_background_only_with_verified_headroom(capacity, triggered):
    operator = _Operator()
    previous = _previous()
    if not triggered:
        previous = HarnessState.new(message_history=previous.message_history[:1])
    requests = []

    class Model(FunctionModel):
        async def count_tokens(self, messages, model_settings, model_request_parameters):
            if capacity == "unknown":
                raise NotImplementedError
            compacted = any(isinstance(m, ModelResponse) and m.metadata == {"keep": "compact"} for m in messages)
            return RequestUsage(input_tokens=9999 if capacity == "tight" and not compacted else 100)

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        yield "foreground summary" if _COMPACTION_PROMPT in _user_values(messages) else "done"

    executable = HarnessBuilder().build(
        AgentSpec(
            model_settings={"max_tokens": 100},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
        ),
        model=Model(stream_function=stream),
        output_type=str,
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=8000), background=True, operator=operator),),
    )
    result = await executable.run("current input", previous_state=previous)
    assert result.output_or_raise() == "done"
    synchronous = triggered and capacity != "room"
    assert len(requests) == (2 if synchronous else 1)
    assert operator.submissions == int(triggered and capacity == "room")
    assert ("old history" not in _user_values(result.state.message_history)) is synchronous
    assert _user_values(result.state.message_history).count("current input") == 1
    if synchronous:
        assert any("agent-context" in str(v) for v in _user_values(requests[-1]))
        assert result.usage.requests == 2
    if operator.task is not None:
        operator.release.set()
        await operator.task


@pytest.mark.parametrize("counting", [False, True])
async def test_wait_failure_uses_current_history_for_synchronous_fallback(counting):
    previous = _previous()

    class FailingOperator(_Operator):
        async def wait(self, thread_id, operation_id):
            self.waiting.set()
            self.operation = CompactionOperation(input=self.operation.input, status="failed", error_code="interrupted")
            return self.operation

    operator = FailingOperator()
    operator.operation = CompactionOperation(input=_input(previous), status="running")
    requests = []

    class Model(FunctionModel):
        async def count_tokens(self, messages, model_settings, model_request_parameters):
            if not counting:
                raise NotImplementedError
            compacted = any(isinstance(m, ModelResponse) and m.metadata == {"keep": "compact"} for m in messages)
            return RequestUsage(input_tokens=100 if compacted else 9999)

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        yield "fallback summary" if _COMPACTION_PROMPT in _user_values(messages) else "done"

    executable = HarnessBuilder().build(
        AgentSpec(
            model_settings={"max_tokens": 100},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
        ),
        model=Model(stream_function=stream),
        output_type=str,
        capabilities=(CompactionCapability(background=True, operator=operator),),
    )
    result = await executable.run("new input after background capture", previous_state=previous)
    assert result.output_or_raise() == "done"
    assert operator.waiting.is_set() and operator.submissions == 0
    assert len(requests) == 2
    assert "new input after background capture" in _user_values(requests[0])
    assert "old history" not in _user_values(requests[1])
    assert await compaction_disposition(result.state, basis=operator.operation.input.basis) == "skipped"


@pytest.mark.parametrize("counting", [False, True])
async def test_failed_foreground_fallback_is_bounded_and_preserves_history(counting):
    previous = _previous()
    operator = _Operator()
    operator.operation = CompactionOperation(input=_input(previous), status="failed", error_code="interrupted")
    summaries = 0
    ordinary = 0

    async def step():
        return "tool result"

    async def stream(messages, info):
        nonlocal summaries, ordinary
        if _COMPACTION_PROMPT in _user_values(messages):
            summaries += 1
            raise RuntimeError("summary unavailable")
        ordinary += 1
        if ordinary == 1:
            yield {0: DeltaToolCall(name="step", json_args="{}", tool_call_id="step")}
        else:
            yield "done"

    executable = _build(operator, stream, counting=counting, extra=(Capability(id="step", tools=[step]),))
    if counting:
        result = await executable.run("continue", previous_state=previous)
        assert result.output_or_raise() == "done"
        assert "old history" in _user_values(result.state.message_history)
        assert await compaction_disposition(result.state, basis=operator.operation.input.basis) is None
        assert ordinary == 2
    else:
        with pytest.raises(DefinitionError, match="could not free"):
            await executable.run("continue", previous_state=previous)
        assert ordinary == 0
    assert summaries == 1 and operator.submissions == 0


@pytest.mark.parametrize("counting", [False, True])
async def test_foreground_fallback_respects_remaining_request_budget(counting):
    from pydantic_ai.usage import UsageLimits

    previous = _previous()
    operator = _Operator()
    operator.operation = CompactionOperation(input=_input(previous), status="failed", error_code="interrupted")
    requests = []

    async def stream(messages, info):
        requests.append(messages)
        assert _COMPACTION_PROMPT not in _user_values(messages)
        yield "done"

    executable = _build(operator, stream, counting=counting)
    if counting:
        result = await executable.run("continue", previous_state=previous, usage_limits=UsageLimits(request_limit=1))
        assert result.output_or_raise() == "done"
    else:
        with pytest.raises(DefinitionError, match="could not free"):
            await executable.run("continue", previous_state=previous, usage_limits=UsageLimits(request_limit=1))
    assert len(requests) == int(counting)
    assert operator.submissions == 0


@pytest.mark.parametrize("background", [False, True])
async def test_synchronous_compaction_retries_detached_history_with_backoff(monkeypatch, background):
    previous = _previous()
    before = encode_messages(previous.message_history)
    requests = []
    delays = []
    summaries = []
    operator = _Operator()
    if background:
        operator.operation = CompactionOperation(input=_input(previous), status="failed", error_code="interrupted")

    def delay(attempt):
        delays.append(attempt)
        return 0

    monkeypatch.setattr("a13n_harness.capabilities.context._compaction_retry_delay", delay)

    async def stream(messages, info):
        if _COMPACTION_PROMPT in _user_values(messages):
            summaries.append(deepcopy(messages))
            if len(summaries) < 3:
                # A partial response must never enter the next retry's input.
                yield "discard this partial summary"
                raise ModelConnectionError("test", "private provider detail", in_stream=True)
            yield "replacement summary"
        else:
            requests.append(deepcopy(messages))
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=stream),
        output_type=str,
        capabilities=(
            CompactionCapability(
                CompactionPolicy(trigger_tokens=8000),
                background=background,
                operator=operator if background else None,
            ),
        ),
    )
    result = await executable.run("current input", previous_state=previous)
    assert result.output_or_raise() == "done"
    assert len(summaries) == 3 and len(requests) == 1
    assert delays == [0, 1]
    for history in summaries:
        assert "old history" in _user_values(history)
        assert _user_values(history).count("current input") == 1
        assert "discard this partial summary" not in str(history)
    assert "old history" not in _user_values(requests[0])
    assert "replacement summary" in str(result.state.message_history)
    assert encode_messages(previous.message_history) == before
    assert result.usage.requests == 4
    if background:
        assert await compaction_disposition(result.state, basis=operator.operation.input.basis) == "skipped"
        assert operator.submissions == 0


@pytest.mark.parametrize("request_limit, attempts", [(1, 0), (2, 1), (3, 2), (10, 3)])
@pytest.mark.parametrize("counting", [False, True])
async def test_compaction_retry_exhaustion_preserves_budget_and_reports_cause(
    monkeypatch, request_limit, attempts, counting
):
    previous = _previous()
    operator = _Operator()
    operator.operation = CompactionOperation(input=_input(previous), status="failed", error_code="interrupted")
    calls = 0
    ordinary = 0
    delays = []
    failure = ModelConnectionError("test", "private provider detail")

    def delay(attempt):
        delays.append(attempt)
        return 0

    monkeypatch.setattr("a13n_harness.capabilities.context._compaction_retry_delay", delay)

    async def stream(messages, info):
        nonlocal calls, ordinary
        if _COMPACTION_PROMPT in _user_values(messages):
            calls += 1
            raise failure
        ordinary += 1
        yield "done"

    executable = _build(operator, stream, counting=counting)
    if counting:
        result = await executable.run(
            "continue", previous_state=previous, usage_limits=UsageLimits(request_limit=request_limit)
        )
        assert result.output_or_raise() == "done"
        assert result.usage.requests == attempts + 1
        assert "old history" in _user_values(result.state.message_history)
    else:
        with pytest.raises(DefinitionError) as caught:
            await executable.run(
                "continue", previous_state=previous, usage_limits=UsageLimits(request_limit=request_limit)
            )
        error = caught.value
        assert error.code == "compaction_capacity_unavailable"
        assert error.details == {"summary_attempts": attempts}
        assert "unverified capacity" in str(error)
        assert "private provider detail" not in str(error)
        if attempts:
            assert f"after {attempts} attempt(s) (ModelConnectionError)" in str(error)
            assert error.__cause__ is failure
            assert error.retry_hint == "new_run"
        else:
            assert "remaining request budget" in str(error)
            assert error.__cause__ is None
    assert calls == attempts and ordinary == int(counting)
    assert delays == list(range(max(0, attempts - 1)))
    assert operator.submissions == 0
    assert await compaction_disposition(previous, basis=operator.operation.input.basis) is None


@pytest.mark.parametrize("failure", [ModelHTTPError(401, "test", body="private detail"), ModelCallCheckError()])
async def test_synchronous_compaction_does_not_retry_permanent_failure_or_refusal(monkeypatch, failure):
    calls = 0

    def unexpected_delay(attempt):
        pytest.fail("terminal failure must not back off")

    monkeypatch.setattr("a13n_harness.capabilities.context._compaction_retry_delay", unexpected_delay)

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        raise failure
        yield "unreachable"

    executable = _build(_Operator(), stream, counting=False)
    expected = ModelCallCheckError if isinstance(failure, ModelCallCheckError) else DefinitionError
    with pytest.raises(expected) as caught:
        await executable.run("continue", previous_state=_previous())
    assert calls == 1
    if isinstance(failure, ModelHTTPError):
        assert "HTTP 401" in str(caught.value)
        assert "private detail" not in str(caught.value)
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure


async def test_cancel_during_compaction_backoff_preserves_history(monkeypatch):
    calls = 0
    backing_off = asyncio.Event()
    previous = _previous()
    before = encode_messages(previous.message_history)

    def delay(attempt):
        backing_off.set()
        return 60

    monkeypatch.setattr("a13n_harness.capabilities.context._compaction_retry_delay", delay)

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        raise ModelConnectionError("test", "interrupted")
        yield "unreachable"

    executable = _build(_Operator(), stream, counting=False)
    task = asyncio.create_task(executable.run("continue", previous_state=previous))
    await asyncio.wait_for(backing_off.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1
    assert encode_messages(previous.message_history) == before


async def test_cancelled_foreground_fallback_preserves_failed_operation():
    from pydantic_ai.capabilities import AbstractCapability

    previous = _previous()
    operator = _Operator()
    operator.operation = CompactionOperation(input=_input(previous), status="failed", error_code="interrupted")
    contexts = []
    started = asyncio.Event()

    class Capture(AbstractCapability):
        async def before_model_request(self, ctx, request_context):
            contexts.append(ctx)
            return request_context

    async def stream(messages, info):
        assert _COMPACTION_PROMPT in _user_values(messages)
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    executable = _build(operator, stream, extra=(Capture(),))
    task = asyncio.create_task(executable.run("continue", previous_state=previous))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = await contexts[0].deps.export_state(contexts[0].messages)
    assert "old history" in _user_values(state.message_history)
    assert await compaction_disposition(state, basis=operator.operation.input.basis) is None
    assert operator.operation.status == "failed" and operator.submissions == 0


async def test_failed_operation_below_trigger_continues_without_fallback():
    previous = _previous()
    messages = previous.message_history
    messages[-1].usage = RequestUsage(input_tokens=10)
    previous = HarnessState.new(thread_id=previous.thread_id, message_history=messages)
    operator = _Operator()
    operator.operation = CompactionOperation(input=_input(previous), status="failed", error_code="interrupted")
    calls = []

    async def stream(messages, info):
        calls.append(messages)
        assert _COMPACTION_PROMPT not in _user_values(messages)
        yield "done"

    result = await _build(operator, stream).run("continue", previous_state=previous)
    assert result.output_or_raise() == "done"
    assert len(calls) == 1 and operator.submissions == 0
    assert await compaction_disposition(result.state, basis=operator.operation.input.basis) is None


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize(
    "tokens,reserve,threshold,blocked",
    [
        (9499, 100, 0.95, False),
        (9500, 100, 0.95, True),
        (9501, 100, 0.95, True),
        (9200, 100, 0.92, True),
        (9500, 100, 0.99, False),
        (9400, 1000, 0.95, True),
    ],
)
async def test_background_blocking_threshold_is_distinct_from_physical_capacity(
    running, tokens, reserve, threshold, blocked
):
    previous = _previous()

    class Operator(_Operator):
        async def wait(self, thread_id, operation_id):
            self.waiting.set()
            return CompactionOperation(
                input=self.operation.input,
                status="ready",
                result=CompactionResult.from_summary(self.operation.input.basis, "background summary"),
            )

    operator = Operator()
    if running:
        operator.operation = CompactionOperation(input=_input(previous), status="running")
    requests = []

    class Model(FunctionModel):
        async def count_tokens(self, messages, model_settings, model_request_parameters):
            compacted = any(isinstance(m, ModelResponse) and m.metadata == {"keep": "compact"} for m in messages)
            # Applied summaries need physical fit, not headroom below the policy line.
            return RequestUsage(input_tokens=min(tokens, 10000 - reserve) if compacted else tokens)

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        yield "foreground summary" if _COMPACTION_PROMPT in _user_values(messages) else "done"

    executable = HarnessBuilder().build(
        AgentSpec(
            model_settings={"max_tokens": reserve},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
        ),
        model=Model(stream_function=stream),
        output_type=str,
        capabilities=(
            CompactionCapability(
                CompactionPolicy(trigger_tokens=8000),
                background=BackgroundCompactionPolicy(enabled=True, blocking_threshold=threshold),
                operator=operator,
            ),
        ),
    )
    result = await executable.run("current input", previous_state=previous)
    assert result.output_or_raise() == "done"
    assert operator.waiting.is_set() is (running and blocked)
    assert operator.submissions == int(not running and not blocked)
    assert len(requests) == (2 if blocked and not running else 1)
    if operator.task is not None:
        operator.release.set()
        await operator.task


async def test_disabled_background_policy_uses_common_trigger():
    requests = []

    async def stream(messages, info):
        requests.append(deepcopy(messages))
        yield "foreground summary" if _COMPACTION_PROMPT in _user_values(messages) else "done"

    executable = HarnessBuilder().build(
        AgentSpec(
            model_settings={"max_tokens": 100},
            model_characteristics=HarnessModelCharacteristics(context_window_tokens=10000),
        ),
        model=_CountingModel(stream_function=stream),
        output_type=str,
        capabilities=(
            CompactionCapability(
                CompactionPolicy(trigger_tokens=8000),
                background=BackgroundCompactionPolicy(enabled=False, blocking_threshold=1),
            ),
        ),
    )
    result = await executable.run("current input", previous_state=_previous())
    assert result.output_or_raise() == "done"
    assert len(requests) == 2


@pytest.mark.parametrize("threshold", [0, -0.1, 1.01, float("nan"), float("inf")])
async def test_background_policy_rejects_invalid_threshold(threshold):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BackgroundCompactionPolicy(blocking_threshold=threshold)
