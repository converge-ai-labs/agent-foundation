from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

import a13n_harness.tools.invocation as tool_invocation_module
import a13n_harness.tools.surface as tool_surface_module
import a13n_harness.toolsets.delegation as delegation_toolset_module
import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AbstractModelCostCapability,
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    DelegationCapability,
    DelegationConfiguration,
    DelegationRunCapability,
    DelegationState,
    HandoffCapability,
    HandoffConfiguration,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    ModelCostInput,
    ModelCostQuote,
    PluginError,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
    RunBindings,
    SubagentDefinition,
    WorkingState,
    WorkingStateCapability,
)
from a13n_harness import (
    AgentSpec as HarnessAgentSpec,
)
from a13n_harness.capabilities.delegation import DELEGATION_CAPABILITY_ID
from a13n_harness.capabilities.working_state import WORKING_STATE_CAPABILITY_ID
from a13n_harness.environment.advanced import (
    NoopEnvironmentRunBinding,
)
from a13n_harness.tools import (
    HARNESS_TOOL_METADATA_KEY,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
)
from a13n_harness.tools.metadata import normalize_harness_tool_metadata
from pydantic import BaseModel
from pydantic_ai import Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio


async def _allow(*args: Any, **kwargs: Any) -> InvocationPolicyDecision:
    del args, kwargs
    return InvocationPolicyDecision.allow()


def _owned_context_part_indexes(message: ModelRequest) -> set[int]:
    metadata = message.metadata or {}
    ownership = metadata.get("a13n.model-context-overlay")
    if not isinstance(ownership, dict):
        return set()
    parts = ownership.get("parts")
    if not isinstance(parts, list):
        return set()
    return {index for item in parts if isinstance(item, dict) and isinstance(index := item.get("index"), int)}


def _latest_user_text(messages: list[ModelMessage]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, ModelRequest):
            continue
        owned = _owned_context_part_indexes(message)
        for index in range(len(message.parts) - 1, -1, -1):
            part = message.parts[index]
            if index not in owned and isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return None


def _returns_after_latest_user(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    latest_user_index = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, ModelRequest)
            and any(
                isinstance(part, UserPromptPart) and part_index not in _owned_context_part_indexes(message)
                for part_index, part in enumerate(message.parts)
            )
        ),
        default=-1,
    )
    return [
        part
        for message in messages[latest_user_index + 1 :]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _previous_child_id(messages: list[ModelMessage]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in reversed(message.parts):
            if not isinstance(part, ToolReturnPart) or part.tool_name != "delegate":
                continue
            content = part.content
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    continue
            elif isinstance(content, BaseModel):
                content = content.model_dump(mode="json")
            if isinstance(content, dict) and isinstance(content.get("child_instance_id"), str):
                return content["child_instance_id"]
    return None


def _child_definition(
    *,
    working_state: bool = False,
    capabilities: tuple[AbstractModelCostCapability, ...] = (),
) -> AgentDefinition[str]:
    async def child_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        turn = sum(isinstance(message, ModelResponse) for message in messages) + 1
        yield f"child-turn-{turn}"

    return AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="child-definition-v1",
        model=FunctionModel(stream_function=child_stream),
        capabilities=(
            *((WorkingStateCapability(),) if working_state else ()),
            *capabilities,
        ),
    )


def _parent_definition(
    child: AgentDefinition[str],
    model: FunctionModel,
    *,
    working_state: bool = False,
    plugins: tuple[AbstractHarnessPlugin, ...] = (),
    capabilities: tuple[AbstractModelCostCapability, ...] = (),
):
    return AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="parent-definition-v1",
        model=model,
        capabilities=(
            DelegationCapability(),
            *((WorkingStateCapability(),) if working_state else ()),
            *capabilities,
        ),
        plugins=plugins,
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Review one bounded task.",
                agent=child,
                usage_limits=UsageLimits(request_limit=5, total_tokens_limit=80_000),
            ),
        ),
    )


def _bindings_factory(
    *,
    parent_instance_id: str = "parent-1",
    host_limits: UsageLimits | None = None,
    observed_limits: list[UsageLimits | None] | None = None,
    extra_capabilities: Sequence[Any] = (),
):
    async def binder(child, input, child_instance_id, continuation, usage_limits):
        del child, input, continuation
        if observed_limits is not None:
            observed_limits.append(usage_limits)
        internal_id = f"internal-{child_instance_id}"
        return RunBindings(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="child"),
                agent_instance_id=internal_id,
                parent_agent_instance_id=parent_instance_id,
                delegation_id=child_instance_id,
            ),
            environment=NoopEnvironmentRunBinding(),
        )

    return RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="parent"),
            agent_instance_id=parent_instance_id,
        ),
        environment=NoopEnvironmentRunBinding(),
        capabilities=(
            InvocationPolicyCapability(evaluator=_allow),
            DelegationRunCapability(binder=binder, usage_limits=host_limits),
            *extra_capabilities,
        ),
    )


@pytest.mark.parametrize(
    ("parent_default", "child_default", "run_override", "expected_child_toolset_guidance"),
    [
        (False, True, None, True),
        (True, True, False, False),
        (False, False, True, True),
    ],
)
async def test_inline_child_inherits_only_explicit_toolset_instruction_override(
    parent_default: bool,
    child_default: bool,
    run_override: bool | None,
    expected_child_toolset_guidance: bool,
) -> None:
    child_instructions: list[str] = []

    async def child_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        child_instructions.append(info.instructions or "")
        yield "child-done"

    child = AgentDefinition(
        agent=HarnessAgentSpec(
            instructions="Child authored instruction.",
            toolset_instructions=child_default,
        ),
        output_type=str,
        definition_id="instruction-child-v1",
        model=FunctionModel(stream_function=child_stream),
        capabilities=(HandoffCapability(HandoffConfiguration(include_summary_reminder=False)),),
    )

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "inspect"}),
                    tool_call_id="delegate-1",
                )
            }
            return
        yield "parent-done"

    parent = AgentDefinition(
        agent=HarnessAgentSpec(toolset_instructions=parent_default),
        output_type=str,
        definition_id="instruction-parent-v1",
        model=FunctionModel(stream_function=parent_stream),
        capabilities=(DelegationCapability(),),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Inspect instruction inheritance.",
                agent=child,
            ),
        ),
    )
    executable = HarnessBuilder().build(parent)
    bindings = replace(_bindings_factory(), toolset_instructions=run_override)

    result = await executable.run("delegate", bindings=bindings)

    assert result.output_or_raise() == "parent-done"
    assert len(child_instructions) == 1
    assert "Child authored instruction." in child_instructions[0]
    assert ('<tool-instruction name="summarize">' in child_instructions[0]) is expected_child_toolset_guidance


async def test_inline_child_deferred_fallback_is_a_tool_failure_not_parent_suspension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_resolver = tool_surface_module.resolve_tool_surface

    def bypass_child_filter(candidates, *, allow_deferred=True):
        del allow_deferred
        return original_resolver(candidates, allow_deferred=True)

    monkeypatch.setattr(tool_surface_module, "resolve_tool_surface", bypass_child_filter)

    async def bypass_child_runtime_denial(self, ctx, *, requests):
        del self, ctx, requests
        return None

    monkeypatch.setattr(
        tool_invocation_module.ToolExecutionBoundaryCapability,
        "handle_deferred_tool_calls",
        bypass_child_runtime_denial,
    )

    def approved_action(value: int) -> int:
        return value

    async def child_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del messages, info
        yield {
            0: DeltaToolCall(
                name="approved_action",
                json_args=json.dumps({"value": 1}),
                tool_call_id="approval-1",
            )
        }

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="deferred-child-v1",
        model=FunctionModel(stream_function=child_stream),
        capabilities=(
            Capability(
                tools=[Tool(approved_action, requires_approval=True)],
                id="approval-tools",
            ),
        ),
    )
    child_failures: list[str] = []

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if any(isinstance(message, ModelResponse) for message in messages):
            failure_parts = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, RetryPromptPart | ToolReturnPart)
            ]
            child_failures.extend(str(part.content) for part in failure_parts)
            yield "parent-recovered"
            return
        yield {
            0: DeltaToolCall(
                name="delegate",
                json_args=json.dumps({"subagent": "reviewer", "task": "inspect"}),
                tool_call_id="delegate-1",
            )
        }

    executable = HarnessBuilder().build(_parent_definition(child, FunctionModel(stream_function=parent_stream)))

    result = await executable.run("delegate", bindings=_bindings_factory())

    assert result.output_or_raise() == "parent-recovered"
    assert result.status == "completed"
    assert child_failures
    assert "subagent_deferred_unsupported" in child_failures[0]


async def test_inline_delegation_persists_child_thread_and_forwards_events() -> None:
    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            child_id = _previous_child_id(messages)
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps(
                        {
                            "subagent": "reviewer",
                            "task": {"request": _latest_user_text(messages)},
                            "child_instance_id": child_id,
                        }
                    ),
                    tool_call_id=f"delegate-{len(messages)}",
                )
            }
            return
        yield "parent-done"

    observed_limits: list[UsageLimits | None] = []
    executable = HarnessBuilder().build(
        _parent_definition(
            _child_definition(),
            FunctionModel(stream_function=parent_stream),
        )
    )
    bindings = _bindings_factory(
        host_limits=UsageLimits(request_limit=7, total_tokens_limit=60_000),
        observed_limits=observed_limits,
    )

    events = []
    async with executable.stream(
        "start",
        bindings=bindings,
        usage_limits=UsageLimits(request_limit=9, total_tokens_limit=100_000),
    ) as stream:
        parent_run_id = stream.run_id
        async for item in stream:
            if isinstance(item, HarnessEvent):
                events.append(item)
            elif isinstance(item, HarnessRunResultEvent):
                first = item.result

    assert first.output_or_raise() == "parent-done"
    assert first.state is not None
    restored = DelegationState.model_validate(first.state.agent_context_state.entries[DELEGATION_CAPABILITY_ID].data)
    child_id, child_record = next(iter(restored.children.items()))
    assert child_id.startswith("reviewer-")
    assert child_record.state.thread_id != first.state.thread_id
    child_events = [event for event in events if event.run_id != parent_run_id]
    assert child_events
    started_events = [
        event
        for event in child_events
        if isinstance(event.event, HarnessExtensionEvent)
        and event.event.kind == "delegation"
        and event.event.payload["action"] == "started"
    ]
    completed_events = [
        event
        for event in events
        if event.run_id == parent_run_id
        and isinstance(event.event, HarnessExtensionEvent)
        and event.event.kind == "delegation"
        and event.event.payload["action"] == "completed"
    ]
    assert len(started_events) == len(completed_events) == 1
    started_payload = started_events[0].event.payload
    completed_payload = completed_events[0].event.payload
    assert started_payload["type"] == completed_payload["type"] == "inline_delegation"
    assert started_payload["invocation_id"] == completed_payload["invocation_id"]
    assert started_payload["parent_run_id"] == completed_payload["parent_run_id"] == parent_run_id
    assert started_payload["parent_agent_instance_id"] == completed_payload["parent_agent_instance_id"] == "parent-1"
    assert started_payload["parent_tool_call_id"] == completed_payload["parent_tool_call_id"]
    assert started_payload["child_run_id"] == completed_payload["child_run_id"] == started_events[0].run_id
    child_usage_events = [
        event
        for event in child_events
        if isinstance(event.event, HarnessExtensionEvent) and event.event.kind == "usage"
    ]
    assert len(child_usage_events) == 1
    usage_records = child_usage_events[0].event.payload["records"]
    assert isinstance(usage_records, list) and len(usage_records) == 1
    assert usage_records[0]["agent_instance_id"] == f"internal-{child_id}"
    assert usage_records[0]["parent_agent_instance_id"] == "parent-1"
    assert usage_records[0]["delegation_id"] == child_id
    assert observed_limits[0] is not None
    assert observed_limits[0].request_limit == 5
    assert observed_limits[0].total_tokens_limit == 60_000
    assert observed_limits[0].count_tokens_before_request is False
    assert first.usage.requests == 3

    second = await executable.run(
        "continue",
        bindings=_bindings_factory(
            host_limits=UsageLimits(request_limit=7, total_tokens_limit=60_000),
            observed_limits=observed_limits,
        ),
        previous_state=first.state,
        usage_limits=UsageLimits(request_limit=9, total_tokens_limit=100_000),
    )
    assert second.output_or_raise() == "parent-done"
    assert second.state is not None
    assert second.state.thread_id == first.state.thread_id
    continued = DelegationState.model_validate(second.state.agent_context_state.entries[DELEGATION_CAPABILITY_ID].data)
    assert set(continued.children) == {child_id}
    assert continued.children[child_id].state.thread_id == child_record.state.thread_id
    assert continued.children[child_id].state.thread_id != second.state.thread_id
    assert "child-turn-2" in json.dumps(second.all_messages(), default=str)


async def test_inline_delegation_intersects_child_agent_spec_usage_limits() -> None:
    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps(
                        {
                            "subagent": "reviewer",
                            "task": {"request": "inspect"},
                        }
                    ),
                    tool_call_id="delegate-1",
                )
            }
            return
        yield "parent-done"

    child = _child_definition().with_updates(
        agent=HarnessAgentSpec(
            usage_limits=UsageLimits(
                request_limit=4,
                total_tokens_limit=90_000,
            )
        )
    )
    observed_limits: list[UsageLimits | None] = []
    executable = HarnessBuilder().build(
        _parent_definition(
            child,
            FunctionModel(stream_function=parent_stream),
        )
    )

    result = await executable.run(
        "delegate",
        bindings=_bindings_factory(
            host_limits=UsageLimits(request_limit=7, total_tokens_limit=60_000),
            observed_limits=observed_limits,
        ),
        usage_limits=UsageLimits(request_limit=9, total_tokens_limit=100_000),
    )

    assert result.output_or_raise() == "parent-done"
    assert len(observed_limits) == 1
    assert observed_limits[0] is not None
    assert observed_limits[0].request_limit == 4
    assert observed_limits[0].total_tokens_limit == 60_000


class ChildEventMutationPlugin(AbstractHarnessPlugin):
    def __init__(self, mutation: str) -> None:
        self.mutation = mutation

    @property
    def plugin_id(self) -> str:
        return f"child-event-{self.mutation}"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessEvent) and item.run_id != exchange.context.run_id:
                    if self.mutation == "thread":
                        item = replace(item, thread_id=exchange.context.thread_id)
                    else:
                        item = replace(item, sequence=item.sequence + 1)
                yield item

        return PluginRunResponse(iterate())


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("thread", "plugin_event_run_mismatch"),
        ("sequence", "plugin_event_sequence_invalid"),
    ],
)
async def test_plugin_cannot_change_forwarded_child_event_provenance(
    mutation: str,
    error_code: str,
) -> None:
    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps(
                        {
                            "subagent": "reviewer",
                            "task": {"request": "inspect"},
                        }
                    ),
                    tool_call_id="delegate-1",
                )
            }
            return
        yield "parent-done"

    executable = HarnessBuilder().build(
        _parent_definition(
            _child_definition(),
            FunctionModel(stream_function=parent_stream),
            plugins=(ChildEventMutationPlugin(mutation),),
        )
    )

    with pytest.raises(PluginError) as exc_info:
        await executable.run("start", bindings=_bindings_factory())

    assert exc_info.value.code == error_code


async def test_nested_inline_delegation_forwards_descendant_events() -> None:
    async def grandchild_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages, info
        yield "grandchild-done"

    grandchild = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="grandchild-definition-v1",
        model=FunctionModel(stream_function=grandchild_stream),
    )

    async def child_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "worker", "task": "nested work"}),
                    tool_call_id="delegate-grandchild",
                )
            }
            return
        yield "child-done"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="nested-child-definition-v1",
        model=FunctionModel(stream_function=child_stream),
        capabilities=(DelegationCapability(),),
        subagents=(
            SubagentDefinition(
                name="worker",
                description="Perform nested work.",
                agent=grandchild,
            ),
        ),
    )

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "review deeply"}),
                    tool_call_id="delegate-child",
                )
            }
            return
        yield "parent-done"

    def nested_binder(parent_instance_id: str):
        async def binder(child, input, child_instance_id, continuation, usage_limits):
            del input, continuation, usage_limits
            internal_id = f"internal-{child_instance_id}"
            capabilities: list[Any] = [InvocationPolicyCapability(evaluator=_allow)]
            if DELEGATION_CAPABILITY_ID in child.executable._definition_reserved_capability_ids:
                capabilities.append(DelegationRunCapability(binder=nested_binder(internal_id)))
            return RunBindings(
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="test", subject="nested-child"),
                    agent_instance_id=internal_id,
                    parent_agent_instance_id=parent_instance_id,
                    delegation_id=child_instance_id,
                ),
                environment=NoopEnvironmentRunBinding(),
                capabilities=tuple(capabilities),
            )

        return binder

    executable = HarnessBuilder().build(_parent_definition(child, FunctionModel(stream_function=parent_stream)))
    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="parent"),
            agent_instance_id="parent-1",
        ),
        environment=NoopEnvironmentRunBinding(),
        capabilities=(
            InvocationPolicyCapability(evaluator=_allow),
            DelegationRunCapability(binder=nested_binder("parent-1")),
        ),
    )
    events: list[HarnessEvent] = []
    async with executable.stream("start", bindings=bindings) as stream:
        parent_run_id = stream.run_id
        async for item in stream:
            if isinstance(item, HarnessEvent):
                events.append(item)
            else:
                result = item.result

    assert result.output_or_raise() == "parent-done"
    assert result.usage.requests == 5
    child_events = [event for event in events if event.run_id != parent_run_id]
    child_run_ids = {
        event.run_id
        for event in child_events
        if isinstance(event.event, HarnessExtensionEvent)
        and event.event.kind == "delegation"
        and event.event.payload["action"] == "started"
    }
    assert len(child_run_ids) == 2
    records = [
        record
        for event in child_events
        if isinstance(event.event, HarnessExtensionEvent) and event.event.kind == "usage"
        for record in event.event.payload["records"]
    ]
    assert len(records) == 3
    assert any(record["parent_agent_instance_id"] == "parent-1" for record in records)
    nested_records = [
        record
        for record in records
        if isinstance(record["parent_agent_instance_id"], str)
        and record["parent_agent_instance_id"].startswith("internal-reviewer-")
    ]
    assert len(nested_records) == 1
    assert nested_records[0]["agent_instance_id"].startswith("internal-worker-")


async def test_inline_delegation_forwards_child_lifecycle_before_pre_request_failure() -> None:
    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "work"}),
                    tool_call_id="delegate-pre-request-failure",
                )
            }
            return
        yield "handled"

    executable = HarnessBuilder().build(
        _parent_definition(
            _child_definition(),
            FunctionModel(stream_function=parent_stream),
        )
    )
    events: list[HarnessEvent] = []
    async with executable.stream(
        "start",
        bindings=_bindings_factory(
            host_limits=UsageLimits(count_tokens_before_request=True),
        ),
    ) as stream:
        parent_run_id = stream.run_id
        async for item in stream:
            if isinstance(item, HarnessEvent):
                events.append(item)
            else:
                result = item.result

    assert result.output_or_raise() == "handled"
    assert result.usage.requests == 2
    child_events = [event for event in events if event.run_id != parent_run_id]
    assert len(child_events) == 3
    assert all(isinstance(event.event, HarnessExtensionEvent) for event in child_events)
    child_payloads = [event.event.payload for event in child_events if isinstance(event.event, HarnessExtensionEvent)]
    assert child_events[0].event.kind == "delegation"
    assert child_payloads[0]["action"] == "started"
    assert child_events[1].event.kind == "lifecycle"
    assert child_payloads[1]["type"] == "model_request_started"
    assert child_events[2].event.kind == "lifecycle"
    assert child_payloads[2]["type"] == "model_request_failed"
    assert not any(
        event.run_id != parent_run_id and isinstance(event.event, HarnessExtensionEvent) and event.event.kind == "usage"
        for event in events
    )
    failed = [
        event.event
        for event in events
        if event.run_id == parent_run_id
        and isinstance(event.event, HarnessExtensionEvent)
        and event.event.kind == "delegation"
        and event.event.payload["action"] == "failed"
    ]
    assert len(failed) == 1
    assert failed[0].payload["child_run_id"] == child_events[0].run_id
    assert failed[0].payload["invocation_id"] == child_payloads[0]["invocation_id"]
    assert failed[0].payload["parent_run_id"] == child_payloads[0]["parent_run_id"] == parent_run_id
    assert failed[0].payload["parent_tool_call_id"] == child_payloads[0]["parent_tool_call_id"]


async def test_inline_delegation_inherits_parent_pricing_without_double_counting() -> None:
    class FixedCostCapability(AbstractModelCostCapability):
        def __init__(self) -> None:
            self.inputs: list[ModelCostInput] = []

        @property
        def revision(self) -> str:
            return "pricing-v1"

        def quote(self, value: ModelCostInput) -> ModelCostQuote:
            self.inputs.append(value)
            return ModelCostQuote(
                cost_usd=Decimal("0.25"),
                source="custom",
                pricing_revision=self.revision,
                rule_id="fixed",
            )

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "price this"}),
                    tool_call_id="delegate-priced",
                )
            }
            return
        yield "done"

    cost_capability = FixedCostCapability()
    executable = HarnessBuilder().build(
        _parent_definition(
            _child_definition(),
            FunctionModel(stream_function=parent_stream),
            capabilities=(cost_capability,),
        )
    )
    events: list[HarnessEvent] = []
    async with executable.stream(
        "start",
        bindings=_bindings_factory(),
    ) as stream:
        parent_run_id = stream.run_id
        async for item in stream:
            if isinstance(item, HarnessEvent):
                events.append(item)
            else:
                result = item.result

    assert result.output_or_raise() == "done"
    assert result.usage.requests == 3
    assert result.usage.cost == Decimal("0.75")
    assert len(cost_capability.inputs) == 3
    child_usage = [
        event.event.payload["records"]
        for event in events
        if event.run_id != parent_run_id
        and isinstance(event.event, HarnessExtensionEvent)
        and event.event.kind == "usage"
    ]
    assert len(child_usage) == 1
    assert child_usage[0][0]["pricing_revision"] == "pricing-v1"
    assert child_usage[0][0]["pricing_status"] == "applied"
    assert len(result.usage_records) == 2
    assert all(record.run_id == parent_run_id for record in result.usage_records)


async def test_inline_delegation_cancellation_before_state_commit_leaves_no_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "work"}),
                    tool_call_id="delegate-cancel-before-commit",
                )
            }
            return
        yield "unexpected"

    state_commit_started = asyncio.Event()
    never_release = asyncio.Event()
    original_store = delegation_toolset_module.DelegationToolset._store_child

    async def paused_store(self, child_instance_id, child, state):
        state_commit_started.set()
        await never_release.wait()
        await original_store(self, child_instance_id, child, state)

    monkeypatch.setattr(delegation_toolset_module.DelegationToolset, "_store_child", paused_store)
    executable = HarnessBuilder().build(
        _parent_definition(
            _child_definition(),
            FunctionModel(stream_function=parent_stream),
        )
    )
    stream = executable.stream("start", bindings=_bindings_factory())

    async with stream:

        async def consume() -> None:
            async for _ in stream:
                pass

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(state_commit_started.wait(), timeout=2)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

    state = await stream.context.state.read(
        DELEGATION_CAPABILITY_ID,
        DelegationState,
        version="1",
    )
    assert state is None


async def test_inline_delegation_retains_new_id_after_handled_dispatch_rejection() -> None:
    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "work"}),
                    tool_call_id="delegate-rejected",
                )
            }
            return
        yield "handled"

    async def rejecting_binder(child, input, child_instance_id, continuation, usage_limits):
        del child, input, child_instance_id, continuation, usage_limits
        raise ToolFailed("Denied by current Host policy.")

    executable = HarnessBuilder().build(
        _parent_definition(
            _child_definition(),
            FunctionModel(stream_function=parent_stream),
        )
    )
    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="parent"),
            agent_instance_id="parent-1",
        ),
        environment=NoopEnvironmentRunBinding(),
        capabilities=(
            InvocationPolicyCapability(evaluator=_allow),
            DelegationRunCapability(binder=rejecting_binder),
        ),
    )
    result = await executable.run("start", bindings=bindings)

    assert result.output_or_raise() == "handled"
    assert result.state is not None
    restored = DelegationState.model_validate(result.state.agent_context_state.entries[DELEGATION_CAPABILITY_ID].data)
    child_id = next(iter(restored.children))
    returns = _returns_after_latest_user(result.all_messages())
    assert any(child_id in str(part.content) for part in returns if part.tool_name == "delegate")
    assert restored.children[child_id].state.message_history == ()


async def test_inline_delegation_borrows_exact_parent_embedded_task_state() -> None:
    async def child_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="task_update",
                    json_args=json.dumps({"task_id": "task-1", "status": "completed"}),
                    tool_call_id="child-complete-task",
                )
            }
            return
        yield "task-completed"

    child = _child_definition(working_state=True)
    child = AgentDefinition(
        agent=child.agent,
        output_type=str,
        definition_id=child.definition_id,
        model=FunctionModel(stream_function=child_stream),
        capabilities=child.capabilities,
    )

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="task_create",
                    json_args=json.dumps({"subject": "Shared", "description": "Complete in child"}),
                    tool_call_id="create-shared-task",
                )
            }
        elif not any(part.tool_name == "delegate" for part in returns):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "complete task-1"}),
                    tool_call_id="delegate-shared-task",
                )
            }
        else:
            yield "parent-done"

    executable = HarnessBuilder().build(
        _parent_definition(child, FunctionModel(stream_function=parent_stream), working_state=True)
    )
    result = await executable.run("coordinate", bindings=_bindings_factory())

    assert result.output_or_raise() == "parent-done"
    assert result.state is not None
    working = WorkingState.model_validate(result.state.agent_context_state.entries[WORKING_STATE_CAPABILITY_ID].data)
    assert working.tasks is not None
    task = working.tasks.tasks["task-1"]
    assert task.status == "completed"
    assert task.owner is not None and task.owner.startswith("internal-reviewer-")


async def test_inline_delegation_rejects_invalid_child_lineage_before_model_work() -> None:
    child_calls = 0

    async def child_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal child_calls
        del messages, info
        child_calls += 1
        yield "unexpected"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=child_stream),
    )

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        failures = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not failures:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "work"}),
                    tool_call_id="bad-lineage",
                )
            }
        else:
            yield "handled"

    async def bad_binder(child, input, child_instance_id, continuation, usage_limits):
        del child, input, child_instance_id, continuation, usage_limits
        return RunBindings.embedded()

    executable = HarnessBuilder().build(_parent_definition(child, FunctionModel(stream_function=parent_stream)))
    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="parent"),
            agent_instance_id="parent-1",
        ),
        environment=NoopEnvironmentRunBinding(),
        capabilities=(
            InvocationPolicyCapability(evaluator=_allow),
            DelegationRunCapability(binder=bad_binder),
        ),
    )
    result = await executable.run("start", bindings=bindings)

    assert result.output_or_raise() == "handled"
    assert child_calls == 0


async def test_inline_delegation_reserves_new_child_capacity_before_dispatch() -> None:
    child_calls = 0

    async def child_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal child_calls
        del messages, info
        child_calls += 1
        await asyncio.sleep(0)
        yield "child-done"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="capacity-child-v1",
        model=FunctionModel(stream_function=child_stream),
    )

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "first"}),
                    tool_call_id="delegate-capacity-1",
                ),
                1: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "second"}),
                    tool_call_id="delegate-capacity-2",
                ),
            }
            return
        yield "handled"

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id="capacity-parent-v1",
            model=FunctionModel(stream_function=parent_stream),
            capabilities=(DelegationCapability(DelegationConfiguration(max_children=1)),),
            subagents=(SubagentDefinition(name="reviewer", description="Review.", agent=child),),
        )
    )
    result = await executable.run("start", bindings=_bindings_factory())

    assert result.output_or_raise() == "handled"
    assert child_calls == 1
    assert result.state is not None
    restored = DelegationState.model_validate(result.state.agent_context_state.entries[DELEGATION_CAPABILITY_ID].data)
    assert len(restored.children) == 1


async def test_inline_delegation_projects_state_budget_failure_before_completed_event() -> None:
    async def child_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "x" * (70 * 1024)

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="large-state-child-v1",
        model=FunctionModel(stream_function=child_stream),
    )

    async def parent_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _returns_after_latest_user(messages):
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "reviewer", "task": "large"}),
                    tool_call_id="delegate-large-state",
                )
            }
            return
        yield "handled"

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id="large-state-parent-v1",
            model=FunctionModel(stream_function=parent_stream),
            capabilities=(
                DelegationCapability(DelegationConfiguration(max_state_bytes=64 * 1024, max_output_bytes=1024 * 1024)),
            ),
            subagents=(SubagentDefinition(name="reviewer", description="Review.", agent=child),),
        )
    )
    events: list[HarnessEvent] = []
    async with executable.stream("start", bindings=_bindings_factory()) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent):
                events.append(item)
            else:
                result = item.result

    assert result.output_or_raise() == "handled"
    delegation_events = [
        event.event.payload
        for event in events
        if isinstance(event.event, HarnessExtensionEvent) and event.event.kind == "delegation"
    ]
    assert any(payload["action"] == "failed" and payload["status"] == "state_rejected" for payload in delegation_events)
    assert not any(payload["action"] == "completed" for payload in delegation_events)


async def test_inline_delegation_output_policy_uses_configured_total_limit() -> None:
    async def child_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "unused"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="output-policy-child-v1",
        model=FunctionModel(stream_function=child_stream),
    )

    async def parent_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        delegate = next(tool for tool in info.function_tools if tool.name == "delegate")
        assert delegate.metadata is not None
        metadata = normalize_harness_tool_metadata(delegate.metadata[HARNESS_TOOL_METADATA_KEY])
        assert metadata.output_policy.max_inline_bytes == 256 * 1024
        assert metadata.output_policy.max_output_bytes == 1024 * 1024
        yield "done"

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id="output-policy-parent-v1",
            model=FunctionModel(stream_function=parent_stream),
            capabilities=(DelegationCapability(DelegationConfiguration(max_output_bytes=1024 * 1024)),),
            subagents=(SubagentDefinition(name="reviewer", description="Review.", agent=child),),
        )
    )

    result = await executable.run("start", bindings=_bindings_factory())
    assert result.output_or_raise() == "done"
