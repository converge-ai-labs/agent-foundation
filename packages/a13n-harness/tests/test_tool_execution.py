"""Execution ownership, storage, and foreground wait boundaries."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from a13n_harness import AgentSpec, DefinitionError, HarnessBuilder, RunBindings, RunError
from a13n_harness._retained_scope import RetainedScope
from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
from a13n_harness.events import HarnessEvent
from a13n_harness.tool_execution import (
    MemoryToolExecutionStore,
    StoredToolExecution,
    ToolExecutionConfig,
    ToolExecutionRuntime,
)
from anyio import create_task_group
from pydantic_ai import Tool, ToolReturn
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


def admit(runtime, operation, **kwargs):
    return runtime.admit(
        operation, thread_id="thread-a", run_id="run-a", tool_call_id="call-a", tool_name="lookup", **kwargs
    )


async def test_admission_precedes_effect_and_fast_result_keeps_native_value():
    store = MemoryToolExecutionStore()
    completed = []
    runtime = ToolExecutionRuntime(store=store, on_completed=completed.append)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        saved = await store.get("thread-a", receipt.execution_id)
        assert saved is not None and saved.record.status == "running"
        return ToolReturn(return_value={"answer": 42}, content=["evidence"])

    receipt = admit(runtime, operation)
    assert calls == 0
    assert (await runtime.info("thread-a", receipt.execution_id)).status == "queued"
    result = await runtime.wait("thread-a", receipt.execution_id)
    assert result.record.status == "succeeded"
    assert result.result.return_value == {"answer": 42}
    assert result.result.content == ["evidence"]
    assert calls == 1
    assert completed == [result.record]
    assert not result.record.delivery_acknowledged
    await runtime.acknowledge_delivery("thread-a", receipt.execution_id)
    assert (await runtime.result("thread-a", receipt.execution_id)).record.delivery_acknowledged
    assert not result.record.delivery_acknowledged
    assert await runtime.close() == ()


async def test_receipt_during_result_publication_is_reported_as_detached(monkeypatch):
    store = MemoryToolExecutionStore()
    publishing, finish = asyncio.Event(), asyncio.Event()
    original_save = store.save
    observations = []

    async def delayed_save(execution):
        if execution.record.terminal:
            publishing.set()
            await finish.wait()
        await original_save(execution)

    monkeypatch.setattr(store, "save", delayed_save)
    runtime = ToolExecutionRuntime(store=store, on_completed=observations.append)

    async def operation():
        return 42

    record = admit(runtime, operation)
    await publishing.wait()
    assert not (await runtime.result("thread-a", record.execution_id)).record.result_available
    runtime.release_foreground("thread-a", record.execution_id)
    finish.set()
    result = await runtime.wait("thread-a", record.execution_id)
    assert result.result == 42
    assert result.record.foreground_released
    assert observations == [result.record]
    await runtime.close()


@pytest.mark.parametrize("acknowledge_fast", [False, True])
async def test_completed_retention_preserves_long_call_that_finishes_last(acknowledge_fast):
    store = MemoryToolExecutionStore(max_completed=3)
    runtime = ToolExecutionRuntime(store=store)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await finish.wait()
        return "slow result"

    async def fast():
        return "fast result"

    slow_record = admit(runtime, slow)
    await entered.wait()
    fast_records = []
    for _ in range(5):
        record = admit(runtime, fast)
        await runtime.wait("thread-a", record.execution_id)
        if acknowledge_fast:
            await runtime.acknowledge_delivery("thread-a", record.execution_id)
        fast_records.append(record)
    assert (await runtime.info("thread-a", slow_record.execution_id)).status == "running"
    finish.set()
    result = await runtime.wait("thread-a", slow_record.execution_id)
    assert result.result == "slow result"
    assert (await runtime.info("thread-a", slow_record.execution_id)).status == "succeeded"
    assert (await runtime.result("thread-a", slow_record.execution_id)).result == "slow result"
    assert await runtime.result("thread-b", slow_record.execution_id) is None
    assert {record.execution_id for record in await store.list("thread-a")} == {
        slow_record.execution_id,
        *(record.execution_id for record in fast_records[-2:]),
    }
    # Metadata updates keep the original completion age for eviction.
    oldest = fast_records[-2]
    await store.save(await store.get("thread-a", oldest.execution_id))
    await runtime.acknowledge_delivery("thread-a", oldest.execution_id)
    newest = admit(runtime, fast)
    await runtime.wait("thread-a", newest.execution_id)
    assert await runtime.result("thread-a", oldest.execution_id) is None
    assert (await runtime.result("thread-a", slow_record.execution_id)).result == "slow result"
    await runtime.close()


async def test_summary_prioritizes_queued_and_unconsumed_work():
    runtime = ToolExecutionRuntime()

    async def fast():
        return 42

    first = admit(runtime, fast)
    await runtime.wait("thread-a", first.execution_id)
    for _ in range(22):
        delivered = admit(runtime, fast)
        await runtime.wait("thread-a", delivered.execution_id)
        await runtime.acknowledge_delivery("thread-a", delivered.execution_id)
    queued = admit(runtime, fast)
    summary = await runtime.summarize("thread-a", limit=2)
    assert [record.execution_id for record in summary] == [queued.execution_id, first.execution_id]
    assert summary[0].status == "queued"
    await runtime.close()


async def test_result_publication_failure_is_terminal_and_closes_admission(monkeypatch):
    store = MemoryToolExecutionStore()
    original_save = store.save

    async def fail_terminal(execution):
        if execution.record.terminal:
            raise OSError("private provider detail")
        await original_save(execution)

    monkeypatch.setattr(store, "save", fail_terminal)
    observed = []
    runtime = ToolExecutionRuntime(store=store, on_completed=observed.append)

    async def operation():
        return 42

    record = admit(runtime, operation)
    result = await runtime.wait("thread-a", record.execution_id)
    assert result.record.status == "lost"
    assert result.result is None and not result.record.result_available
    assert "private" not in result.record.error
    assert await runtime.info("thread-a", record.execution_id) == result.record
    assert (await runtime.summarize("thread-a"))[0] == result.record
    assert observed == [result.record]
    with pytest.raises(ToolFailed, match="shutting down"):
        admit(runtime, operation)
    assert await runtime.close() == ()


async def test_completion_observer_failure_does_not_lose_result():
    def fail_observer(record):
        raise RuntimeError("observer unavailable")

    runtime = ToolExecutionRuntime(on_completed=fail_observer)

    async def operation():
        return 42

    record = admit(runtime, operation)
    result = await runtime.wait("thread-a", record.execution_id)
    assert result.record.status == "succeeded" and result.result == 42
    assert await runtime.close() == ()


async def test_expiry_and_cancelled_waiter_leave_original_operation_running():
    runtime = ToolExecutionRuntime()
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return {"value": [1]}

    receipt = admit(runtime, operation)
    await entered.wait()
    pending = await runtime.wait("thread-a", receipt.execution_id, timeout_seconds=0)
    assert pending.record.status == "running"
    waiter = asyncio.create_task(runtime.wait("thread-a", receipt.execution_id))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert (await runtime.info("thread-a", receipt.execution_id)).status == "running"
    finish.set()
    result = await runtime.wait("thread-a", receipt.execution_id)
    assert result.result == {"value": [1]}
    result.result["value"].append(2)
    assert (await runtime.result("thread-a", receipt.execution_id)).result == {"value": [1]}
    assert calls == 1
    await runtime.close()


@pytest.mark.parametrize("before_start", [True, False])
async def test_explicit_cancel_releases_scope_once_and_waits_for_cleanup(before_start):
    runtime = ToolExecutionRuntime()
    entered, cleanup = asyncio.Event(), asyncio.Event()
    releases = 0

    async def operation():
        entered.set()
        await asyncio.Event().wait()

    async def release():
        nonlocal releases
        releases += 1
        await cleanup.wait()

    receipt = admit(runtime, operation, release=release)
    if not before_start:
        await entered.wait()
    await runtime.cancel("thread-a", receipt.execution_id)
    await asyncio.sleep(0)
    await runtime.cancel("thread-a", receipt.execution_id)
    assert not (await runtime.info("thread-a", receipt.execution_id)).terminal
    cleanup.set()
    result = await runtime.wait("thread-a", receipt.execution_id)
    assert result.record.status == "cancelled"
    assert releases == 1
    assert entered.is_set() is not before_start
    await runtime.close()


async def test_cancel_during_cleanup_keeps_execution_live_until_resources_close():
    runtime = ToolExecutionRuntime()
    cleaning, finish = asyncio.Event(), asyncio.Event()

    async def operation():
        return 42

    async def release():
        cleaning.set()
        await finish.wait()

    receipt = admit(runtime, operation, release=release)
    await cleaning.wait()
    await runtime.cancel("thread-a", receipt.execution_id)
    assert await runtime.close(timeout_seconds=0) == (receipt.execution_id,)
    assert not (await runtime.info("thread-a", receipt.execution_id)).terminal
    finish.set()
    result = await runtime.wait("thread-a", receipt.execution_id)
    assert result.record.status == "succeeded"
    assert result.result == 42
    assert await runtime.close() == ()


async def test_sequential_barrier_covers_prior_and_later_calls_across_runs():
    runtime = ToolExecutionRuntime()
    first_entered, first_finish = asyncio.Event(), asyncio.Event()
    barrier_entered, barrier_finish = asyncio.Event(), asyncio.Event()
    later_entered = asyncio.Event()

    async def first():
        first_entered.set()
        await first_finish.wait()

    async def barrier():
        barrier_entered.set()
        await barrier_finish.wait()

    async def later():
        later_entered.set()

    before = admit(runtime, first)
    await first_entered.wait()
    serial = admit(runtime, barrier, sequential=True)
    after = runtime.admit(later, thread_id="thread-a", run_id="run-b", tool_call_id="later", tool_name="later")
    inline = asyncio.create_task(runtime.inline(later, thread_id="thread-a", sequential=False))
    await runtime.wait("thread-a", after.execution_id, timeout_seconds=0)
    assert not barrier_entered.is_set() and not later_entered.is_set()
    first_finish.set()
    await barrier_entered.wait()
    assert not later_entered.is_set()
    barrier_finish.set()
    await asyncio.gather(*(runtime.wait("thread-a", item.execution_id) for item in (before, serial, after)), inline)
    assert later_entered.is_set()
    assert await runtime.close() == ()


async def test_capacity_thread_scope_and_shutdown():
    runtime = ToolExecutionRuntime(max_active=1)
    released = asyncio.Event()

    async def operation():
        await asyncio.Event().wait()

    async def release():
        released.set()

    receipt = admit(runtime, operation, release=release)
    with pytest.raises(ToolFailed, match="capacity"):
        admit(runtime, operation)
    assert await runtime.info("thread-b", receipt.execution_id) is None
    assert await runtime.result("thread-b", receipt.execution_id) is None
    assert await runtime.cancel("thread-b", receipt.execution_id) is None
    assert await runtime.wait("thread-b", receipt.execution_id) is None
    assert await runtime.summarize("thread-b") == ()
    assert await runtime.close() == ()
    assert released.is_set()
    assert (await runtime.info("thread-a", receipt.execution_id)).status == "cancelled"
    with pytest.raises(ToolFailed, match="shutting down"):
        admit(runtime, operation)


async def test_failed_admission_storage_never_dispatches_effect_and_releases_scope():
    class RejectAdmission(MemoryToolExecutionStore):
        async def save(self, execution: StoredToolExecution) -> None:
            if execution.record.status == "queued":
                raise OSError("storage unavailable")
            await super().save(execution)

    runtime = ToolExecutionRuntime(store=RejectAdmission())
    dispatched, released = False, False

    async def operation():
        nonlocal dispatched
        dispatched = True

    async def release():
        nonlocal released
        released = True

    receipt = admit(runtime, operation, release=release)
    result = await runtime.wait("thread-a", receipt.execution_id)
    assert result.record.status == "failed"
    assert result.record.error == "Tool execution failed."
    assert not dispatched and released
    await runtime.close()


async def test_retained_scope_closes_in_owner_task_after_last_invocation():
    owners = []
    closed = asyncio.Event()

    @asynccontextmanager
    async def resource():
        async with create_task_group():
            owners.append(asyncio.current_task())
            yield
            owners.append(asyncio.current_task())
        closed.set()

    scope = RetainedScope(resource())
    await scope.ready()
    first, second = scope.retain(), scope.retain()
    await scope.close()
    assert not closed.is_set()
    with pytest.raises(RuntimeError, match="no longer admits"):
        scope.retain()
    await first()
    assert not closed.is_set()
    await second()
    assert closed.is_set()
    assert owners[0] is owners[1]
    assert owners[0] is not asyncio.current_task()


async def test_scope_entry_failure_propagates_without_leaking_owner_task():
    @asynccontextmanager
    async def resource():
        raise ValueError("cannot open")
        yield

    scope = RetainedScope(resource())
    with pytest.raises(ValueError, match="cannot open"):
        await scope.ready()


async def test_scope_cancelled_entry_drains_owner_without_waiting_for_readiness():
    entered, cleaned = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def resource():
        try:
            entered.set()
            await asyncio.Event().wait()
            yield
        finally:
            cleaned.set()

    scope = RetainedScope(resource())
    ready = asyncio.create_task(scope.ready())
    await entered.wait()
    ready.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ready
    assert cleaned.is_set()


def tool_agent(operation, *, config, owned=True):
    observed = []

    async def model(messages, info):
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            yield {0: DeltaToolCall(name=operation.__name__, json_args="{}", tool_call_id="call-lookup")}
        else:
            observed.extend(returns)
            yield "continuing conversation"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            ToolExecutionCapability(config),
            Capability(tools=[Tool(operation, metadata={TOOL_EXECUTION_METADATA_KEY: "owned"} if owned else {})]),
        ),
    )
    return executable, observed


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("exhaust_retries", [False, True])
async def test_owned_tool_preserves_native_retry_prompt_and_budget(bound, exhaust_retries):
    from pydantic_ai import ModelRetry
    from pydantic_ai.messages import RetryPromptPart

    calls = 0
    prompts = []

    async def lookup(location: str):
        nonlocal calls
        calls += 1
        if location != "Paris" or exhaust_retries:
            raise ModelRetry("Provide a valid location")
        return "sunny"

    async def model(messages, info):
        retries = [part for message in messages for part in message.parts if isinstance(part, RetryPromptPart)]
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returns:
            assert returns[-1].content == "sunny"
            yield "done"
        else:
            if retries:
                assert retries[-1].content == "Provide a valid location"
                assert retries[-1].tool_name == "lookup"
                prompts.append(retries[-1])
            yield {
                0: DeltaToolCall(
                    name="lookup",
                    json_args='{"location":"Paris"}' if retries else '{"location":"?"}',
                    tool_call_id=f"lookup-{len(retries)}",
                )
            }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            ToolExecutionCapability(),
            Capability(tools=[Tool(lookup, max_retries=1, metadata={TOOL_EXECUTION_METADATA_KEY: "owned"})]),
        ),
    )
    runtime = ToolExecutionRuntime()
    try:
        result = await executable.run("start", bindings=RunBindings.embedded(tool_execution=runtime if bound else None))
        assert result.status == ("failed" if exhaust_retries else "completed")
        assert calls == 2 and len(prompts) == 1
    finally:
        await runtime.close()


@pytest.mark.parametrize("retriever", ["tool_execution_result", "tool_execution_wait"])
async def test_detached_retry_returns_original_correction_without_retrying_retrieval(retriever):
    import json

    from pydantic_ai import ModelRetry
    from pydantic_ai.messages import RetryPromptPart

    finish = asyncio.Event()
    calls = 0

    async def lookup():
        nonlocal calls
        calls += 1
        await finish.wait()
        raise ModelRetry("Provide a valid location")

    runtime = ToolExecutionRuntime()
    executable, observed = tool_agent(lookup, config=ToolExecutionConfig(mode="auto", foreground_wait_seconds=0))
    try:
        first = await executable.run("start", bindings=RunBindings.embedded(tool_execution=runtime))
        execution_id = observed[0].content["execution_id"]
        finish.set()
        stored = await runtime.wait(first.thread_id, execution_id)
        assert stored.retry_prompt == "Provide a valid location"
        assert stored.record.status == "failed"

        async def model(messages, info):
            assert not any(isinstance(part, RetryPromptPart) for message in messages for part in message.parts)
            returns = [
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == retriever
            ]
            if returns:
                assert returns[-1].content["retry_prompt"] == "Provide a valid location"
                assert returns[-1].content["tool_name"] == "lookup"
                yield "correction retrieved"
            else:
                yield {0: DeltaToolCall(name=retriever, json_args=json.dumps({"execution_id": execution_id}))}

        reader = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(ToolExecutionCapability(),),
        )
        result = await reader.run(
            "retrieve", previous_state=first.state, bindings=RunBindings.embedded(tool_execution=runtime)
        )
        assert result.output_or_raise() == "correction retrieved"
        assert calls == 1
    finally:
        await runtime.close()


@pytest.mark.parametrize("foreground_mode", [None, "foreground"])
async def test_foreground_tool_obeys_cross_run_barrier_while_controls_remain_available(monkeypatch, foreground_mode):
    import json

    entered, queued = asyncio.Event(), asyncio.Event()
    ran = False

    async def slow():
        entered.set()
        await asyncio.Event().wait()

    async def foreground():
        nonlocal ran
        ran = True
        return "foreground completed"

    runtime = ToolExecutionRuntime()
    from a13n_harness import HarnessState

    previous = HarnessState.new()
    slow_record = runtime.admit(
        slow, thread_id=previous.thread_id, run_id="run-first", tool_call_id="slow", tool_name="slow", sequential=True
    )
    await entered.wait()
    original_inline = runtime.inline

    async def inline(operation, *, thread_id, sequential):
        # Signal entry into the production scheduler, then wait for its barrier.
        queued.set()
        return await original_inline(operation, thread_id=thread_id, sequential=sequential)

    monkeypatch.setattr(runtime, "inline", inline)

    async def model(messages, info):
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returns:
            yield "done"
        else:
            yield {0: DeltaToolCall(name="foreground", json_args="{}")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            ToolExecutionCapability(),
            Capability(tools=[Tool(foreground, metadata={TOOL_EXECUTION_METADATA_KEY: foreground_mode})]),
        ),
    )
    # A separate Run shares the exact Thread with the admitted sequential work.
    pending = asyncio.create_task(
        executable.run("start", previous_state=previous, bindings=RunBindings.embedded(tool_execution=runtime))
    )
    try:
        await asyncio.wait_for(queued.wait(), 2)
        assert not ran and not pending.done()

        async def control_model(messages, info):
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            if returns:
                yield "cancelled"
            else:
                yield {
                    0: DeltaToolCall(
                        name="tool_execution_cancel", json_args=json.dumps({"execution_id": slow_record.execution_id})
                    )
                }

        controller = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=control_model),
            capabilities=(ToolExecutionCapability(),),
        )
        controlled = await asyncio.wait_for(
            controller.run("cancel", previous_state=previous, bindings=RunBindings.embedded(tool_execution=runtime)), 2
        )
        assert controlled.output_or_raise() == "cancelled"
        result = await asyncio.wait_for(pending, 2)
        assert result.output_or_raise() == "done" and ran
        assert (await runtime.info(previous.thread_id, slow_record.execution_id)).status == "cancelled"
    finally:
        await runtime.close()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_auto_without_runtime_rejects_before_tool_execution():
    called = False

    async def lookup():
        nonlocal called
        called = True
        return 42

    executable, observed = tool_agent(lookup, config=ToolExecutionConfig(mode="auto"))
    with pytest.raises(DefinitionError) as caught:
        await executable.run("start")
    assert caught.value.code == "tool_execution_unbound"
    assert not called and observed == []


async def test_retained_spill_materializes_in_later_run_after_original_cleanup(tmp_path):
    import json

    from a13n_harness.context import AgentContext
    from pydantic_ai import RunContext

    from .test_codeact import _local_environment

    entered, finish = asyncio.Event(), asyncio.Event()

    async def lookup(ctx: RunContext[AgentContext]):
        entered.set()
        await finish.wait()
        path = await ctx.deps._spill_tool_result(b"retained evidence", suffix=".txt")
        assert path is not None
        return ToolReturn(return_value={"output_file_path": path}, content=[f"Evidence: {path}"])

    runtime = ToolExecutionRuntime()
    executable, observed = tool_agent(lookup, config=ToolExecutionConfig(mode="auto", foreground_wait_seconds=0))
    first = await executable.run(
        "start", bindings=RunBindings.embedded(tool_execution=runtime, environment=_local_environment(tmp_path))
    )
    execution_id = observed[0].content["execution_id"]
    finish.set()
    completed = await runtime.wait(first.thread_id, execution_id)
    assert completed.record.status == "succeeded", completed.record
    old_path = completed.result.return_value["output_file_path"]
    assert not list(tmp_path.rglob("tool-result-*.txt"))
    assert completed.artifacts[0].data == b"retained evidence"

    retrieved = False

    async def model(messages, info):
        nonlocal retrieved
        returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "tool_execution_result"
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="tool_execution_result",
                    json_args=json.dumps({"execution_id": execution_id}),
                    tool_call_id="retrieve",
                )
            }
        else:
            content = returns[-1].content
            assert isinstance(content, list)
            path = content[0]["output_file_path"]
            assert content[1] == f"Evidence: {path}"
            assert path != old_path
            files = list(tmp_path.rglob("tool-result-*.txt"))
            assert len(files) == 1
            assert path.endswith(files[0].relative_to(tmp_path).as_posix())
            assert files[0].read_bytes() == b"retained evidence"
            retrieved = True
            yield "retrieved"

    reader = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(ToolExecutionCapability(),),
    )
    second = await reader.run(
        "read",
        previous_state=first.state,
        bindings=RunBindings.embedded(tool_execution=runtime, environment=_local_environment(tmp_path)),
    )
    assert second.status == "completed" and retrieved
    assert (await runtime.info(first.thread_id, execution_id)).delivery_acknowledged
    assert (await runtime.result(first.thread_id, execution_id)).artifacts[0].data == b"retained evidence"
    await runtime.close()


def test_interruption_settlement_preserves_existing_native_retry():
    from a13n_harness._tool_execution import RunToolExecutions
    from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart

    state = RunToolExecutions()
    state.returns["call"] = {"process_id": "process-one"}
    messages = (
        ModelResponse(parts=[ToolCallPart("shell_exec", {}, "call")]),
        ModelRequest(parts=[RetryPromptPart("invalid result", tool_name="shell_exec", tool_call_id="call")]),
    )
    assert state.settle(messages) == messages


@pytest.mark.parametrize("immediate", [False, True])
async def test_tool_handoff_publishes_one_return_to_each_channel(immediate):
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def lookup():
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return 42

    runtime = ToolExecutionRuntime()
    produced = []
    executable, observed = tool_agent(
        lookup, config=ToolExecutionConfig(mode="inline" if immediate else "auto", foreground_wait_seconds=0)
    )
    async with executable.stream(
        "start", bindings=RunBindings.embedded(tool_execution=runtime, producer_observer=produced.append)
    ) as stream:

        async def consume():
            return [event async for event in stream]

        consumer = asyncio.create_task(consume())
        await entered.wait()
        if immediate:
            await stream.steer("continue without waiting", immediate=True)
        published = await consumer
        assert stream.result.status == "completed"
        thread_id = stream.thread_id
    assert calls == 1
    receipt = observed[0].content
    assert receipt["type"] == "tool_execution_receipt"
    for events in (produced, published):
        returns = [
            item.event.part
            for item in events
            if isinstance(item, HarnessEvent) and isinstance(item.event, FunctionToolResultEvent)
        ]
        assert len(returns) == 1
        assert returns[0].tool_call_id == "call-lookup"
        assert returns[0].content == receipt
    finish.set()
    outcome = await runtime.wait(thread_id, receipt["execution_id"])
    assert outcome.result == 42
    await runtime.close()


@pytest.mark.parametrize("bound", [False, True])
async def test_immediate_unsupported_tool_preserves_original_operation(bound, monkeypatch):
    monkeypatch.setattr("a13n_harness._run_stream._IMMEDIATE_STEERING_WAIT_SECONDS", 0.01)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def lookup():
        entered.set()
        await finish.wait()
        return 42

    executable, observed = tool_agent(lookup, config=ToolExecutionConfig(), owned=False)
    runtime = ToolExecutionRuntime() if bound else None
    async with executable.stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream:

        async def consume():
            return [event async for event in stream]

        consumer = asyncio.create_task(consume())
        await entered.wait()
        with pytest.raises(RunError) as caught:
            await stream.steer("interrupt", immediate=True)
        assert caught.value.code == ("steering_timeout" if bound else "tool_execution_unbound")
        await stream.steer("ordinary steer")
        finish.set()
        await consumer
        assert stream.result.status == "completed"
    assert observed[0].content == 42
    if runtime is not None:
        await runtime.close()


async def test_immediate_settlement_publishes_completed_native_result_once(monkeypatch):
    ready = asyncio.Event()
    runtime = ToolExecutionRuntime()
    wait = runtime.wait

    async def pause_after_completion(*args, **kwargs):
        result = await wait(*args, **kwargs)
        ready.set()
        await asyncio.Event().wait()
        return result

    monkeypatch.setattr(runtime, "wait", pause_after_completion)

    async def lookup():
        return ToolReturn(return_value={"answer": 42}, metadata={"origin": "retained"})

    produced = []
    executable, observed = tool_agent(lookup, config=ToolExecutionConfig(mode="inline"))
    async with executable.stream(
        "start", bindings=RunBindings.embedded(tool_execution=runtime, producer_observer=produced.append)
    ) as stream:

        async def consume():
            return [event async for event in stream]

        consumer = asyncio.create_task(consume())
        await ready.wait()
        await stream.steer("continue", immediate=True)
        published = await consumer
        assert stream.result.status == "completed"
        assert (await runtime.summarize(stream.thread_id))[0].delivery_acknowledged
    assert len(observed) == 1 and observed[0].content == {"answer": 42}
    for events in (produced, published):
        returns = [
            item.event.part
            for item in events
            if isinstance(item, HarnessEvent) and isinstance(item.event, FunctionToolResultEvent)
        ]
        assert len(returns) == 1
        assert returns[0].content == {"answer": 42}
        assert returns[0].metadata["origin"] == "retained"
    await runtime.close()


async def test_early_close_observes_all_settled_returns_without_filling_public_queue():
    from pydantic_ai.messages import FunctionToolCallEvent

    count = 65  # Exceeds the public event queue capacity.
    entered = asyncio.Event()
    calls = 0

    async def lookup():
        nonlocal calls
        calls += 1
        if calls == count:
            entered.set()
        await asyncio.Event().wait()

    async def model(messages, info):
        yield {
            index: DeltaToolCall(name="lookup", json_args="{}", tool_call_id=f"call-{index}") for index in range(count)
        }

    runtime = ToolExecutionRuntime(max_active=128)
    produced = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            ToolExecutionCapability(),
            Capability(tools=[Tool(lookup, metadata={TOOL_EXECUTION_METADATA_KEY: "owned"})]),
        ),
    )
    try:
        async with asyncio.timeout(10):
            async with executable.stream(
                "start", bindings=RunBindings.embedded(tool_execution=runtime, producer_observer=produced.append)
            ) as stream:
                started = 0
                async for event in stream:
                    if isinstance(event, HarnessEvent) and isinstance(event.event, FunctionToolCallEvent):
                        started += 1
                        if started == count:
                            break
                await entered.wait()
        returns = [
            item.event.part
            for item in produced
            if isinstance(item, HarnessEvent) and isinstance(item.event, FunctionToolResultEvent)
        ]
        assert len(returns) == count
        assert len({part.tool_call_id for part in returns}) == count
        assert all(part.content["type"] == "tool_execution_receipt" for part in returns)
    finally:
        await runtime.close()
