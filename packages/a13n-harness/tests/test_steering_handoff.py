"""Immediate steering at native model, validation and tool-batch boundaries."""

import asyncio
from types import SimpleNamespace

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings, RunError
from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
from a13n_harness.capabilities import ToolReviewAssessment, ToolReviewResult
from a13n_harness.capabilities.steering import SteeringCapability, steering_input_ids
from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
from a13n_harness.tool_execution import ToolExecutionRuntime
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from pydantic_ai import Agent, Tool
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.exceptions import ApprovalRequired, ToolFailed
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


async def consume(stream):
    return [item async for item in stream]


def build(model, tools, *capabilities):
    return HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(ToolExecutionCapability(), Capability(tools=tools), *capabilities),
    )


async def test_review_handoff_covers_whole_batch_and_does_not_leak_to_next_batch():
    reviewing, approve, finish, next_started = (asyncio.Event() for _ in range(4))
    effects, reviews, requests = [], [], []

    async def review_model(messages, info):
        yield "reviewing"
        reviewing.set()
        await approve.wait()
        yield " allowed"

    async def review(request, *, context):
        reviews.append(request.tool_call_id)
        # A helper Agent sharing the context/capability is still tool-owned.
        helper = Agent(FunctionModel(stream_function=review_model), capabilities=[SteeringCapability()])
        async with helper.run_stream("review", deps=context) as result:
            await result.get_output()
        return ToolReviewResult(assessment=ToolReviewAssessment(risk="low", reason="allowed"))

    async def lookup(value: int):
        effects.append(value)
        if value == 3:
            next_started.set()
        await finish.wait()
        return value

    async def model(messages, info):
        requests.append(tuple(messages))
        if len(requests) == 1:
            yield {
                index: DeltaToolCall(name="lookup", json_args=f'{{"value":{index + 1}}}', tool_call_id=f"call-{index}")
                for index in range(2)
            }
        elif len(requests) == 2:
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            assert len(returns) == 2
            assert all(part.content["type"] == "tool_execution_receipt" for part in returns)
            prompts = [
                part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart)
            ]
            assert prompts.index("continue") < prompts.index("preserve order")
            yield {0: DeltaToolCall(name="lookup", json_args='{"value":3}', tool_call_id="next")}
        else:
            returned = next(
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id == "next"
            )
            assert returned.content == 3
            yield "done"

    runtime = ToolExecutionRuntime()
    executable = build(
        model,
        [Tool(lookup, metadata={TOOL_EXECUTION_METADATA_KEY: "owned"})],
        ToolPermissionsCapability(ToolPermissions(default="review"), reviewer=SimpleNamespace(review=review)),
    )
    try:
        async with (
            asyncio.timeout(5),
            executable.stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            consumer = asyncio.create_task(consume(stream))
            await reviewing.wait()
            await stream.steer("continue", input_id="first", immediate=True)
            await stream.steer("preserve order", input_id="second", immediate=True)
            assert not consumer.done() and effects == []
            assert await runtime.summarize(stream.thread_id) == ()
            approve.set()
            await next_started.wait()
            assert len(requests) == 2 and not consumer.done()
            finish.set()
            await consumer
            assert stream.result.status == "completed"
            assert steering_input_ids(stream.result.state.message_history) == ("first", "second")
            assert sorted(effects) == [1, 2, 3]
            assert sorted(reviews) == ["call-0", "call-1", "next"]
            records = await runtime.summarize(stream.thread_id)
            assert len(records) == 3
            for record in records:
                assert (await runtime.wait(stream.thread_id, record.execution_id)).record.status == "succeeded"
    finally:
        finish.set()
        approve.set()
        await runtime.close()


async def test_model_response_preemption_never_starts_the_following_tool_validation():
    ready = asyncio.Event()
    validated = []
    requests = 0

    async def validate(ctx):
        validated.append(True)

    async def lookup():
        return 42

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call")}
        else:
            assert any(
                isinstance(part, UserPromptPart) and part.content == "change"
                for message in messages
                for part in message.parts
            )
            yield "changed"

    class ModelBoundary(AbstractCapability):
        async def after_model_request(self, ctx, *, request_context, response):
            if requests == 1:
                ready.set()
                await asyncio.Event().wait()
            return response

    executable = build(
        model, [Tool(lookup, args_validator=validate, metadata={TOOL_EXECUTION_METADATA_KEY: "owned"})], ModelBoundary()
    )
    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            executable.stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            consumer = asyncio.create_task(consume(stream))
            await ready.wait()
            assert validated == []
            assert stream.context._steering.model_active
            await stream.steer("change", input_id="change", immediate=True)
            await consumer
            assert stream.result.status == "completed"
            assert requests == 2 and validated == []
            assert await runtime.summarize(stream.thread_id) == ()
    finally:
        await runtime.close()


async def test_handoff_during_validation_preserves_denial_and_native_approval():
    checking, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def validate(ctx, decision: str):
        checking.set()
        await release.wait()
        if decision == "deny":
            raise ToolFailed("policy denied")
        raise ApprovalRequired()

    async def lookup(decision: str):
        effects.append(decision)

    async def model(messages, info):
        yield {
            0: DeltaToolCall(name="lookup", json_args='{"decision":"deny"}', tool_call_id="denied"),
            1: DeltaToolCall(name="lookup", json_args='{"decision":"ask"}', tool_call_id="approval"),
        }

    executable = build(model, [Tool(lookup, args_validator=validate, metadata={TOOL_EXECUTION_METADATA_KEY: "owned"})])
    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            executable.stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            consumer = asyncio.create_task(consume(stream))
            await checking.wait()
            await stream.steer("continue", input_id="pending", immediate=True)
            release.set()
            await consumer
            result = stream.result
            assert result.status == "suspended"
            assert [call.tool_call_id for call in result.deferred.approvals] == ["approval"]
            denied = next(
                part
                for message in result.state.message_history
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id == "denied"
            )
            assert denied.content == "policy denied"
            assert effects == [] and await runtime.summarize(stream.thread_id) == ()
            assert steering_input_ids(result.state.message_history) == ("pending",)
    finally:
        release.set()
        await runtime.close()


async def test_unsupported_validator_times_out_without_accepting_input(monkeypatch):
    monkeypatch.setattr("a13n_harness._run_stream._IMMEDIATE_STEERING_WAIT_SECONDS", 0.01)
    checking, release = asyncio.Event(), asyncio.Event()

    async def validate(ctx):
        checking.set()
        await release.wait()

    async def lookup():
        return 42

    async def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call")}

    executable = build(model, [Tool(lookup, args_validator=validate)])
    runtime = ToolExecutionRuntime()
    try:
        async with (
            asyncio.timeout(5),
            executable.stream("start", bindings=RunBindings.embedded(tool_execution=runtime)) as stream,
        ):
            consumer = asyncio.create_task(consume(stream))
            await checking.wait()
            with pytest.raises(RunError) as caught:
                await stream.steer("rejected", input_id="rejected", immediate=True)
            assert caught.value.code == "steering_timeout"
            release.set()
            await consumer
            assert stream.result.status == "completed"
            assert steering_input_ids(stream.result.state.message_history) == ()
    finally:
        release.set()
        await runtime.close()


async def test_completion_during_waiter_cleanup_returns_final_result(monkeypatch):
    from a13n_harness._tool_execution import RunToolExecutions

    state = RunToolExecutions()
    waiting, finish = asyncio.Event(), asyncio.Event()
    completions = []
    runtime = ToolExecutionRuntime(on_completed=completions.append)
    original_wait = runtime.wait

    async def operation():
        await finish.wait()
        return 42

    async def wait(*args, **kwargs):
        waiting.set()
        try:
            return await original_wait(*args, **kwargs)
        finally:
            # Make completion win while the handoff drains its cancelled waiter.
            finish.set()
            await runtime.join()

    monkeypatch.setattr(runtime, "wait", wait)
    record = runtime.admit(operation, thread_id="thread", run_id="run", tool_call_id="call", tool_name="lookup")
    try:
        async with asyncio.timeout(5):
            foreground = asyncio.create_task(state.wait_foreground(runtime, "thread", record.execution_id))
            await waiting.wait()
            state.handoff_requested.set()
            result = await foreground
        assert len(completions) == 1 and not completions[0].foreground_released
        assert result.record.status == "succeeded" and result.result == 42
    finally:
        finish.set()
        await runtime.close()
