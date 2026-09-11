from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    DeferredToolResume,
    HarnessBuilder,
    HarnessRunResultEvent,
    HarnessState,
    RunBindings,
)
from a13n_harness.recovery import INTERRUPTED_TOOL_RESULT, normalize_interrupted_history
from a13n_harness.tools import (
    RECOVERY_RETRY_SAFE_METADATA_KEY,
    CanonicalResource,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
    recovery_retryable,
)
from pydantic_ai import Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability, Toolset
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("state", ["complete", "interrupted"])
@pytest.mark.parametrize("mode", [None, "declared", "never", "always"])
async def test_partial_recovery_preserves_results_and_retries_only_selected_calls(state, mode) -> None:
    executed = []
    histories = []

    @recovery_retryable
    def read(value: str) -> str:
        executed.append(value)
        return value

    def write() -> str:
        executed.append("write")
        return "written"

    async def model(messages, info) -> AsyncIterator[str]:
        histories.append(deepcopy(messages))
        yield "done"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[Capability(tools=[read, write])],
    )
    calls = [
        ToolCallPart("read", {"value": name}, tool_call_id=name) for name in ("done", "closed", "retry", "pending")
    ]
    calls.extend([ToolCallPart("write", {}, tool_call_id="write"), ToolCallPart("removed", {}, tool_call_id="removed")])
    response = ModelResponse(parts=calls, state=state)
    saved = HarnessState.new(
        message_history=(
            response,
            ModelRequest(parts=[ToolReturnPart("read", "saved", tool_call_id="done")]),
            ModelRequest(
                parts=[
                    ToolReturnPart("read", INTERRUPTED_TOOL_RESULT, tool_call_id="closed", outcome="failed"),
                    RetryPromptPart("invalid", tool_name="read", tool_call_id="retry"),
                ]
            ),
        )
    )
    payload = saved.model_dump_json()
    options = {} if mode is None else {"tool_recovery": mode}
    result = await executable.run("Continue", previous_state=saved, **options)
    assert result.output_or_raise() == "done"
    assert sorted(executed) == ([] if mode == "never" else ["pending", "write"] if mode == "always" else ["pending"])
    returns = [p for m in histories[0] for p in m.parts if isinstance(p, ToolReturnPart)]
    assert len(returns) == 5
    by_id = {p.tool_call_id: p.content for p in returns}
    assert by_id == {
        "done": "saved",
        "closed": INTERRUPTED_TOOL_RESULT,
        "pending": INTERRUPTED_TOOL_RESULT if mode == "never" else "pending",
        "write": "written" if mode == "always" else INTERRUPTED_TOOL_RESULT,
        "removed": INTERRUPTED_TOOL_RESULT,
    }
    assert [p.tool_call_id for m in histories[0] for p in m.parts if isinstance(p, RetryPromptPart)] == ["retry"]
    assert next(m for m in histories[0] if isinstance(m, ModelResponse)).parts == response.parts
    assert saved.model_dump_json() == payload
    before = list(executed)
    repeated = await executable.run("Continue again", previous_state=result.state, tool_recovery="always")
    assert repeated.output_or_raise() == "done"
    assert executed == before


def test_recovery_marker_preserves_native_tool_settings_without_mutating_source() -> None:
    def read(value: int) -> int:
        return value

    async def prepare(ctx, tool):
        return tool

    original = Tool(
        read,
        name="custom_read",
        description="Custom",
        prepare=prepare,
        requires_approval=True,
        sequential=True,
        timeout=30,
        metadata={"owner": "plugin"},
    )
    marked = recovery_retryable(original)
    assert marked is not original
    assert marked.function is original.function
    assert marked.function_schema is original.function_schema
    assert marked.prepare is prepare
    assert (marked.name, marked.description, marked.requires_approval, marked.sequential, marked.timeout) == (
        "custom_read",
        "Custom",
        True,
        True,
        30,
    )
    assert original.metadata == {"owner": "plugin"}
    assert marked.metadata == {"owner": "plugin", RECOVERY_RETRY_SAFE_METADATA_KEY: True}


async def test_plugin_dynamic_tool_declaration_is_resolved_from_current_definition() -> None:
    executed = []
    enabled = True

    def lookup() -> str:
        executed.append("lookup")
        return "found"

    async def tool_factory(ctx):
        tool = Tool(lookup, name="plugin_lookup")
        return FunctionToolset(tools=[recovery_retryable(tool) if enabled else tool])

    class Plugin(AbstractHarnessPlugin):
        @property
        def plugin_id(self):
            return "test.recovery"

        def get_capabilities(self):
            return (Toolset(tool_factory),)

    async def model(messages, info) -> AsyncIterator[str]:
        yield "done"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        plugins=[Plugin()],
    )
    saved = HarnessState.new(
        message_history=(ModelResponse(parts=[ToolCallPart("plugin_lookup", {}, tool_call_id="old")]),)
    )
    first = await executable.run(previous_state=saved)
    assert first.output_or_raise() == "done"
    assert executed == ["lookup"]
    enabled = False
    second = await executable.run(previous_state=saved)
    assert second.output_or_raise() == "done"
    assert executed == ["lookup"]
    assert any(
        isinstance(p, ToolReturnPart) and p.content == INTERRUPTED_TOOL_RESULT
        for m in second.all_messages()
        for p in m.parts
    )


@pytest.mark.parametrize("decision", ["allow", "deny", "approval_required"])
async def test_recovery_approval_still_evaluates_current_managed_policy(decision) -> None:
    executed = []
    checked = []

    def change() -> str:
        executed.append("changed")
        return "changed"

    async def resolve(arguments, *, context):
        return (CanonicalResource(namespace="test", kind="item", identifier="1", approval_revision="v1"),)

    async def policy(invocation, metadata, *, context):
        checked.append(invocation)
        if decision == "deny":
            return InvocationPolicyDecision.deny("denied")
        if decision == "approval_required":
            return InvocationPolicyDecision.require_approval("confirm")
        return InvocationPolicyDecision.allow()

    async def model(messages, info) -> AsyncIterator[str]:
        yield "done"

    tool = recovery_retryable(
        HarnessTool(
            change,
            harness_metadata=HarnessToolMetadata(
                tool_id="test.change",
                effects=frozenset({"write"}),
                credential_audiences=(),
                idempotency="none",
                output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
                resource_resolver=resolve,
            ),
        )
    )
    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[Capability(tools=[tool])],
    )
    bindings = RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),))
    saved = HarnessState.new(message_history=(ModelResponse(parts=[ToolCallPart("change", {}, tool_call_id="old")]),))
    first = await executable.run(previous_state=saved, bindings=bindings)
    assert len(checked) == 1, [
        p.content for m in first.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)
    ]
    assert executed == (["changed"] if decision == "allow" else [])
    if decision == "approval_required":
        assert first.status == "suspended"
        assert first.deferred is not None
        decision = "allow"
        second = await executable.run(
            previous_state=first.state,
            bindings=bindings,
            deferred_resume=DeferredToolResume(first.deferred, first.deferred.build_results(approve_all=True)),
        )
        assert second.output_or_raise() == "done"
        assert executed == ["changed"]
    else:
        assert first.output_or_raise() == "done"


def test_interrupted_recovery_preserves_results_across_multiple_requests() -> None:
    history = (
        ModelResponse(parts=[ToolCallPart("read", {}, tool_call_id=name) for name in ("old", "new", "pending")]),
        ModelRequest(parts=[ToolReturnPart("read", "saved", tool_call_id="old")]),
        ModelRequest(parts=[ToolReturnPart("read", "recovered", tool_call_id="new")], state="interrupted"),
    )
    normalized, closed = normalize_interrupted_history(history)
    assert closed == 1
    returns = [p for m in normalized for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [(p.tool_call_id, p.content) for p in returns] == [
        ("old", "saved"),
        ("new", "recovered"),
        ("pending", INTERRUPTED_TOOL_RESULT),
    ]


async def test_cancellation_during_partial_recovery_keeps_old_and_new_results() -> None:
    executed = []
    started = asyncio.Event()

    async def read(value: str) -> str:
        executed.append(value)
        if value == "pending":
            started.set()
            await asyncio.Event().wait()
        return value

    async def model(messages, info) -> AsyncIterator[str]:
        yield "done"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[Capability(tools=[recovery_retryable(Tool(read, sequential=True))])],
    )
    saved = HarnessState.new(
        message_history=(
            ModelResponse(
                parts=[ToolCallPart("read", {"value": name}, tool_call_id=name) for name in ("old", "new", "pending")]
            ),
            ModelRequest(parts=[ToolReturnPart("read", "saved", tool_call_id="old")]),
        )
    )
    async with executable.stream(previous_state=saved) as stream:

        async def consume():
            async for event in stream:
                if isinstance(event, HarnessRunResultEvent):
                    return event.result
            pytest.fail("Missing terminal result")

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 5)
        stream.cancel()
        result = await asyncio.wait_for(task, 5)
    assert result.status == "cancelled"
    returns = [p for m in result.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [(p.tool_call_id, p.content) for p in returns] == [
        ("old", "saved"),
        ("new", "new"),
        ("pending", INTERRUPTED_TOOL_RESULT),
    ]
    resumed = await executable.run(previous_state=result.state, tool_recovery="always")
    assert resumed.output_or_raise() == "done"
    assert executed == ["new", "pending"]


async def test_retryable_call_still_runs_native_argument_validation() -> None:
    @recovery_retryable
    def read(value: int) -> int:
        pytest.fail("Invalid arguments must not execute")

    async def model(messages, info) -> AsyncIterator[str]:
        assert any(isinstance(p, RetryPromptPart) and p.tool_call_id == "old" for m in messages for p in m.parts)
        yield "done"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[Capability(tools=[read])],
    )
    saved = HarnessState.new(
        message_history=(ModelResponse(parts=[ToolCallPart("read", {"value": "invalid"}, tool_call_id="old")]),)
    )
    result = await executable.run(previous_state=saved)
    assert result.output_or_raise() == "done"


async def test_recovery_keeps_external_deferral_and_explicit_result_continuation() -> None:
    from pydantic_ai.tools import DeferredToolResults, ToolDefinition
    from pydantic_ai.toolsets import ExternalToolset

    async def model(messages, info) -> AsyncIterator[str]:
        assert any(isinstance(p, ToolReturnPart) and p.content == "external result" for m in messages for p in m.parts)
        yield "done"

    external = ExternalToolset(
        [
            ToolDefinition(
                name="external",
                parameters_json_schema={"type": "object", "properties": {}},
                metadata={RECOVERY_RETRY_SAFE_METADATA_KEY: True},
            )
        ]
    )
    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[Toolset(external)],
    )
    saved = HarnessState.new(message_history=(ModelResponse(parts=[ToolCallPart("external", {}, tool_call_id="old")]),))
    first = await executable.run(previous_state=saved)
    assert first.status == "suspended"
    assert first.deferred is not None
    assert [call.tool_call_id for call in first.deferred.calls] == ["old"]
    second = await executable.run(
        previous_state=first.state,
        tool_recovery="never",
        deferred_resume=DeferredToolResume(first.deferred, DeferredToolResults(calls={"old": "external result"})),
    )
    assert second.output_or_raise() == "done"
