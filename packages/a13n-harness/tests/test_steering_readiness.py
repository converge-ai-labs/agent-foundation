"""Bounded immediate admission waits without queued fallback or tool replay."""

import asyncio

import pytest
from a13n_harness import RunBindings, RunError
from a13n_harness._tool_execution import RunToolExecutions
from a13n_harness.capabilities.steering import steering_input_ids
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.tool_execution import ToolExecutionRuntime
from pydantic_ai import Tool
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import DeltaToolCall

from .test_steering_handoff import build, consume

pytestmark = pytest.mark.anyio


def observe_waits(stream, monkeypatch):
    waits = asyncio.Queue()
    original = stream._wait_for_steering

    async def wait(ready):
        waits.put_nowait(ready)
        await original(ready)

    monkeypatch.setattr(stream, "_wait_for_steering", wait)
    return waits


async def test_wait_crosses_validation_and_execution_then_preempts_once(monkeypatch):
    validating, execute, executing, finish = (asyncio.Event() for _ in range(4))
    calls, requests = [], []

    async def validate(ctx):
        validating.set()
        await execute.wait()

    async def lookup():
        calls.append("lookup")
        executing.set()
        await finish.wait()
        return 42

    async def model(messages, info):
        requests.append(messages)
        if len(requests) == 1:
            yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call")}
        elif [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart) and part.content in ("first", "second")
        ] == ["first", "second"]:
            yield "done"
        else:
            yield "still generating"
            await asyncio.Event().wait()

    executable = build(model, [Tool(lookup, args_validator=validate)])
    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            executable.stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            waits = observe_waits(stream, monkeypatch)
            consumer = asyncio.create_task(consume(stream))
            await validating.wait()
            first = asyncio.create_task(stream.steer("first", input_id="first", immediate=True))
            await waits.get()
            second = asyncio.create_task(stream.steer("second", input_id="second", immediate=True))
            await waits.get()
            assert not stream.context._steering._pending_requests
            execute.set()
            await executing.wait()
            assert not first.done() and not second.done()
            finish.set()
            ids = await asyncio.gather(first, second)
            assert len(set(ids)) == 2
            await consumer
            assert calls == ["lookup"]
            assert stream.result.output == "done"
            assert steering_input_ids(stream.result.state.message_history) == ("first", "second")
    finally:
        execute.set()
        finish.set()
        await runtime.close()


@pytest.mark.parametrize("ending", ["timeout", "caller_cancel", "stop", "close"])
async def test_wait_abandonment_never_accepts_input(ending, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def lookup():
        calls.append("lookup")
        started.set()
        await finish.wait()
        return 42

    async def model(messages, info):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call")}
        else:
            yield "done"

    executable = build(model, [Tool(lookup)])
    runtime = ToolExecutionRuntime()
    if ending == "timeout":
        monkeypatch.setattr("a13n_harness._run_stream._IMMEDIATE_STEERING_WAIT_SECONDS", 0.02)
    try:
        async with asyncio.timeout(5):
            async with executable.stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream:
                waits = observe_waits(stream, monkeypatch)
                consumer = asyncio.create_task(consume(stream))
                await started.wait()
                steering = asyncio.create_task(stream.steer("not accepted", input_id="absent", immediate=True))
                await waits.get()
                if ending == "caller_cancel":
                    steering.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await steering
                elif ending == "close":
                    consumer.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await consumer
                    with pytest.raises(RunError, match="not active"):
                        await steering
                else:
                    if ending == "stop":
                        stream.cancel()
                    with pytest.raises(RunError) as caught:
                        await steering
                    assert caught.value.code == ("steering_timeout" if ending == "timeout" else "run_not_active")
                assert not stream.context._steering._pending_requests
                finish.set()
                if ending != "close":
                    await consumer
                assert calls == ["lookup"]
                state = await stream.export_state()
                assert steering_input_ids(state.message_history) == ()
    finally:
        finish.set()
        await runtime.close()


@pytest.mark.parametrize("best_effort", [False, True])
async def test_wait_for_initial_native_binding(monkeypatch, best_effort):
    binding, release = asyncio.Event(), asyncio.Event()

    async def model(messages, info):
        if any(
            isinstance(part, UserPromptPart) and part.content == "early"
            for message in messages
            for part in message.parts
        ):
            yield "done"
        else:
            yield "waiting"
            await asyncio.Event().wait()

    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            build(model, []).stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            original = stream.context._steering.bind

            async def bind(ctx):
                binding.set()
                await release.wait()
                return await original(ctx)

            monkeypatch.setattr(stream.context._steering, "bind", bind)
            waits = observe_waits(stream, monkeypatch)
            consumer = asyncio.create_task(consume(stream))
            await binding.wait()
            steering = asyncio.create_task(
                stream.steer("early", input_id="early", immediate=True, best_effort=best_effort)
            )
            await waits.get()
            release.set()
            await steering
            await consumer
            assert steering_input_ids(stream.result.state.message_history) == ("early",)
    finally:
        release.set()
        await runtime.close()


async def test_native_completion_wakes_readiness_waiter(monkeypatch):
    generating, finish = asyncio.Event(), asyncio.Event()

    async def model(messages, info):
        yield "done"
        generating.set()
        await finish.wait()

    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            build(model, []).stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            waits = observe_waits(stream, monkeypatch)
            consumer = asyncio.create_task(consume(stream))
            await generating.wait()
            # Hold a barrier beyond native completion to exercise the terminal wake independently.
            stream.context._tool_executions.block_handoff("held")
            steering = asyncio.create_task(stream.steer("too late", immediate=True))
            await waits.get()
            finish.set()
            with pytest.raises(RunError) as caught:
                await steering
            assert caught.value.code == "run_not_active"
            await consumer
            assert not stream.context._steering._pending_requests
    finally:
        finish.set()
        await runtime.close()


async def test_acceptance_disarms_readiness_deadline(monkeypatch):
    generating, emitting, release = (asyncio.Event() for _ in range(3))
    monkeypatch.setattr("a13n_harness._run_stream._IMMEDIATE_STEERING_WAIT_SECONDS", 0.01)

    async def model(messages, info):
        if steering_input_ids(messages):
            yield "done"
        else:
            yield "working"
            generating.set()
            await asyncio.Event().wait()

    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            build(model, []).stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            original = stream.context._steering._emit_enqueued

            async def emit(*args, **kwargs):
                emitting.set()
                await release.wait()
                await original(*args, **kwargs)

            monkeypatch.setattr(stream.context._steering, "_emit_enqueued", emit)
            consumer = asyncio.create_task(consume(stream))
            await generating.wait()
            steering = asyncio.create_task(stream.steer("accepted", input_id="once", immediate=True))
            await emitting.wait()
            await asyncio.sleep(0.03)  # Deliberately exceed the admission budget after acceptance.
            assert not steering.done()
            release.set()
            assert await steering
            await consumer
            assert steering_input_ids(stream.result.state.message_history) == ("once",)
    finally:
        release.set()
        await runtime.close()


async def test_handoff_notification_requires_all_calls_to_release():
    state = RunToolExecutions()
    assert state.handoff_ready.is_set()
    state.block_handoff("first")
    state.block_handoff("second")
    state.unblock_handoff("first")
    assert not state.handoff_ready.is_set()
    state.unblock_handoff("second")
    assert state.handoff_ready.is_set()
    state.block_handoff("third")
    assert not state.handoff_ready.is_set()


async def test_wait_during_model_retry_backoff(monkeypatch):
    from a13n_harness import AgentSpec, HarnessBuilder, HarnessEvent, ModelRecoveryPolicy
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.models.function import FunctionModel

    retrying = asyncio.Event()
    requests = []

    async def model(messages, info):
        requests.append(messages)
        if len(requests) == 1:
            raise ModelHTTPError(503, "test")
        if any(
            isinstance(part, UserPromptPart) and part.content == "during retry"
            for message in messages
            for part in message.parts
        ):
            yield "done"
        else:
            yield "working"
            await asyncio.Event().wait()

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        model_recovery=ModelRecoveryPolicy(
            enabled=True, max_attempts=2, backoff_initial_seconds=0.1, backoff_max_seconds=0.1
        ),
    )
    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            executable.stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            waits = observe_waits(stream, monkeypatch)

            async def consume_retry():
                async for item in stream:
                    if (
                        isinstance(item, HarnessEvent)
                        and isinstance(item.event, HarnessExtensionEvent)
                        and item.event.kind == "recovery"
                    ):
                        retrying.set()

            consumer = asyncio.create_task(consume_retry())
            await retrying.wait()
            steering = asyncio.create_task(stream.steer("during retry", input_id="retry", immediate=True))
            await waits.get()
            assert not stream.context._steering._pending_requests
            await steering
            await consumer
            assert stream.result.output == "done"
            assert steering_input_ids(stream.result.state.message_history) == ("retry",)
    finally:
        await runtime.close()


async def test_wait_snapshots_mutable_multimodal_input(monkeypatch):
    from pydantic_ai.messages import BinaryContent

    started, finish = asyncio.Event(), asyncio.Event()
    received = []
    image = BinaryContent(data=b"original", media_type="image/png")

    async def lookup():
        started.set()
        await finish.wait()
        return 42

    async def model(messages, info):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call")}
        else:
            yield "done"

    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            build(model, [Tool(lookup)]).stream(
                "start", bindings=RunBindings.embedded(tool_execution=runtime)
            ) as stream,
        ):
            waits = observe_waits(stream, monkeypatch)
            consumer = asyncio.create_task(consume(stream))
            await started.wait()
            steering = asyncio.create_task(stream.steer(["image", image], input_id="image", immediate=True))
            await waits.get()
            image.data = b"changed"
            finish.set()
            await steering
            await consumer
            for message in stream.result.state.message_history:
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                        received.extend(item.data for item in part.content if isinstance(item, BinaryContent))
            assert received == [b"original"]
            assert steering_input_ids(stream.result.state.message_history) == ("image",)
    finally:
        finish.set()
        await runtime.close()


@pytest.mark.parametrize("phase", ["validation", "execution"])
async def test_best_effort_accepts_ordered_input_before_barrier_release(phase, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls, observed = [], []

    async def validate(ctx):
        if phase == "validation":
            entered.set()
            await release.wait()

    async def lookup():
        calls.append("lookup")
        if phase == "execution":
            entered.set()
            await release.wait()
        return 42

    async def model(messages, info):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call")}
        else:
            yield "done"

    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            build(model, [Tool(lookup, args_validator=validate)]).stream(
                "start", bindings=RunBindings.embedded(tool_execution=runtime, producer_observer=observed.append)
            ) as stream,
        ):
            waits = observe_waits(stream, monkeypatch)
            consumer = asyncio.create_task(consume(stream))
            await entered.wait()
            ids = await asyncio.gather(
                *(stream.steer(text, input_id=text, immediate=True, best_effort=True) for text in ("first", "second"))
            )
            assert len(set(ids)) == 2
            assert waits.empty()
            assert len(stream.context._steering._pending_requests) == 2
            assert not stream.context._tool_executions.handoff_requested.is_set()
            events = [
                event.event.payload
                for event in observed
                if isinstance(event.event, HarnessExtensionEvent)
                and event.event.payload.get("type") == "steering_input_enqueued"
            ]
            assert [event["enqueue_id"] for event in events] == ids
            assert all(event["handoff_requested"] is False for event in events)
            release.set()
            await consumer
            assert calls == ["lookup"]
            assert steering_input_ids(stream.result.state.message_history) == ("first", "second")
    finally:
        release.set()
        await runtime.close()


@pytest.mark.parametrize("bound,immediate", [(True, True), (False, True), (True, False)])
async def test_best_effort_model_handoff_or_ordinary_queue(bound, immediate):
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def model(messages, info):
        if any(isinstance(part, UserPromptPart) and part.content == "change" for msg in messages for part in msg.parts):
            yield "changed"
        else:
            entered.set()
            yield "working"
            await release.wait()

    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            build(model, []).stream(
                "start",
                bindings=RunBindings.embedded(
                    tool_execution=runtime if bound else None,
                    producer_observer=observed.append,
                ),
            ) as stream,
        ):
            consumer = asyncio.create_task(consume(stream))
            await entered.wait()
            await stream.steer("change", input_id="change", immediate=immediate, best_effort=True)
            events = [
                event.event.payload
                for event in observed
                if isinstance(event.event, HarnessExtensionEvent)
                and event.event.payload.get("type") == "steering_input_enqueued"
            ]
            assert len(events) == 1
            assert events[0]["handoff_requested"] is (bound and immediate)
            if not (bound and immediate):
                assert not consumer.done()
                release.set()
            await consumer
            assert stream.result.output == "changed"
            assert steering_input_ids(stream.result.state.message_history) == ("change",)
    finally:
        release.set()
        await runtime.close()


@pytest.mark.parametrize("ending", ["timeout", "caller_cancel", "stop", "invalid"])
async def test_best_effort_does_not_hide_readiness_or_input_failures(ending, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def model(messages, info):
        yield "done"

    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            build(model, []).stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            original = stream.context._steering.bind

            async def bind(ctx):
                entered.set()
                await release.wait()
                return await original(ctx)

            monkeypatch.setattr(stream.context._steering, "bind", bind)
            monkeypatch.setattr("a13n_harness._run_stream._IMMEDIATE_STEERING_WAIT_SECONDS", 0.05)
            waits = observe_waits(stream, monkeypatch)
            consumer = asyncio.create_task(consume(stream))
            await entered.wait()
            steering = asyncio.create_task(
                stream.steer(
                    "change",
                    input_id="" if ending == "invalid" else "change",
                    immediate=True,
                    best_effort=True,
                )
            )
            if ending != "invalid":
                await waits.get()
            if ending == "caller_cancel":
                steering.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await steering
            else:
                if ending == "stop":
                    stream.cancel()
                with pytest.raises(RunError) as caught:
                    await steering
                assert (
                    caught.value.code
                    == {
                        "invalid": "input_invalid",
                        "stop": "run_not_active",
                        "timeout": "steering_timeout",
                    }[ending]
                )
            assert not stream.context._steering._pending_requests
            release.set()
            await consumer
    finally:
        release.set()
        await runtime.close()


@pytest.mark.parametrize("immediate", [False, True])
async def test_host_acceptance_guard_rechecks_after_native_lock_wait(immediate):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def model(messages, info):
        entered.set()
        await finish.wait()
        yield "done"

    allowed = True

    def before_accept():
        if not allowed:
            raise ValueError("Host notice is stale")

    executable = build(model, [])
    async with asyncio.timeout(5), executable.stream("start") as stream:
        consumer = asyncio.create_task(consume(stream))
        await entered.wait()
        bridge = stream.context._steering
        async with bridge._lock:
            pending = asyncio.create_task(
                stream.steer("stale", immediate=immediate, best_effort=True, before_accept=before_accept)
            )
            # Let the caller reach the held queue lock before revoking authority.
            await asyncio.sleep(0)
            allowed = False
        with pytest.raises(ValueError, match="Host notice is stale"):
            await pending
        assert not bridge._pending_requests
        finish.set()
        await consumer


@pytest.mark.parametrize("pending", [False, True])
def test_rejected_input_is_removed_from_merged_history_and_replay_only(pending):
    from a13n_harness import HarnessState
    from a13n_harness.capabilities.steering import STEERING_CAPABILITY_ID, without_input
    from a13n_harness.content import (
        ContentItem,
        ContentMetadata,
        input_request,
        merge_request_history,
        request_input_content,
    )
    from a13n_harness.state import AgentContextStateSnapshot, CapabilityState, StoredRef
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    notice = input_request(
        [ContentItem("expired hint", ContentMetadata(source_id="notice", display=False))],
        metadata={"a13n.steering-input": "notice"},
    )
    user = input_request(
        [ContentItem("real work", ContentMetadata(source_id="user"))], metadata={"a13n.steering-input": "user"}
    )
    returned = ModelRequest(parts=[ToolReturnPart("delegate", "completed child", "child")])
    ref = StoredRef(key="child-state", digest="0" * 64, size=10)
    ledger = {"retained_requests": [user], "pending_requests": []}
    ledger["pending_requests" if pending else "retained_requests"].append(notice)
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    # Use the native message codec for the owning namespace's wire shape.
    ledger = {key: ModelMessagesTypeAdapter.dump_python(value, mode="json") for key, value in ledger.items()}
    state = HarnessState.new(
        message_history=merge_request_history((returned, notice, user)),
        agent_context_state=AgentContextStateSnapshot(
            entries={
                STEERING_CAPABILITY_ID: CapabilityState(version="1", data=ledger),
                "child": CapabilityState(version="1", data={"continuation": "keep"}),
            }
        ),
    ).model_copy(update={"refs": (ref,), "future_field": {"keep": True}})
    corrected = without_input(state, input_id="notice", source_id="notice")
    assert "expired hint" not in corrected.model_dump_json()
    assert "expired hint" in state.model_dump_json()
    assert steering_input_ids(corrected.message_history) == ("user",)
    (request,) = corrected.message_history
    assert request.parts[0] == returned.parts[0]
    assert [item.metadata.source_id for item in request_input_content(request)] == ["user"]
    assert corrected.agent_context_state.get("child") == state.agent_context_state.get("child")
    assert corrected.refs == (ref,) and corrected.model_extra == state.model_extra
    assert without_input(corrected, input_id="notice", source_id="notice") == corrected
