from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from a13n_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    CodeActCapability,
    CodeActPolicyToolset,
    CodeActToolPolicy,
    DelegationCapability,
    DelegationRunCapability,
    EnvironmentAction,
    EnvironmentPermissionSet,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.environment.advanced import (
    EnvironmentBindingRequest,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    NoopEnvironmentRunBinding,
    create_environment_run_binding,
)
from a13n_harness.environment.local.binding import DirectLocalEnvironmentProviderBinding
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


def _tool_returns(messages: list[ModelMessage], name: str) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == name
    ]


def _codeact_tools(*tools: Any, allowed: tuple[str, ...]) -> Capability[Any]:
    return Capability(
        toolsets=[
            CodeActPolicyToolset(
                wrapped=FunctionToolset(list(tools), id="test-tools"),
                policy=CodeActToolPolicy(tools={name: True for name in allowed}),
                reject_unknown_tools=True,
            )
        ],
        id="codeact-test-tools",
    )


def _local_environment(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="codeact-test",
            root=DirectLocalRootConfiguration(path=root),
        )
    )
    return create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                EnvironmentBindingRequest(
                    binding_id="binding-1",
                    binding_revision=1,
                    alias="local",
                    permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                    default_working_directory="/",
                    provider_binding=provider,
                ),
            ),
            default_binding_id="binding-1",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


async def test_run_code_dispatches_eligible_tools_and_owns_inline_state() -> None:
    calls: list[int] = []
    descriptions: list[str] = []
    observed_returns: list[Any] = []

    def double(value: int) -> int:
        """Double one integer."""
        calls.append(value)
        return value * 2

    def hidden() -> str:
        raise AssertionError("denied tool must not be callable from CodeAct")

    programs = (
        {"code": "saved = await double(value=4)\nsaved"},
        {"code": "saved + 1"},
        {"code": "saved = 3\nsaved", "restart": True},
        {"code": "saved + 1"},
    )

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        runner = next(tool for tool in info.function_tools if tool.name == "run_code")
        descriptions.append(runner.description or "")
        returns = _tool_returns(messages, "run_code")
        if returns:
            observed_returns[:] = [part.content for part in returns]
        if len(returns) < len(programs):
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps(programs[len(returns)]),
                    tool_call_id=f"code-{len(returns) + 1}",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            _codeact_tools(double, hidden, allowed=("double",)),
            CodeActCapability(),
        ),
    )

    events: list[HarnessEvent] = []
    async with executable.stream("run", bindings=RunBindings.embedded()) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent):
                events.append(item)

    assert stream.result is not None
    assert stream.result.output_or_raise() == "done"
    assert calls == [4]
    assert observed_returns == [8, 9, 3, 4]
    assert all("double" in description and "hidden" not in description for description in descriptions)
    payload_types = [
        item.event.payload["type"]
        for item in events
        if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "diagnostic"
    ]
    assert payload_types.count("codeact_execution_started") == 4
    assert payload_types.count("codeact_execution_completed") == 4
    assert payload_types.count("codeact_tool_call_started") == 1
    assert payload_types.count("codeact_tool_call_completed") == 1


async def test_run_program_reads_direct_local_source_and_dispatches_current_tools(tmp_path: Path) -> None:
    (tmp_path / "job.codeact.py").write_text(
        "async def main(inputs):\n    return await double(value=inputs['value'])\n",
        encoding="utf-8",
    )
    calls: list[int] = []

    def double(value: int) -> int:
        calls.append(value)
        return value * 2

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _tool_returns(messages, "run_program"):
            yield {
                0: DeltaToolCall(
                    name="run_program",
                    json_args=json.dumps({"path": "/workspace/job.codeact.py", "inputs": {"value": 5}}),
                    tool_call_id="program-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(_codeact_tools(double, allowed=("double",)), CodeActCapability()),
    )
    result = await executable.run(
        "run program",
        bindings=RunBindings.embedded(environment=_local_environment(tmp_path)),
    )

    assert result.output_or_raise() == "done"
    assert calls == [5]
    returns = [part for part in _tool_returns(result.all_messages(), "run_program")]
    assert [part.content for part in returns] == [10]


async def test_inline_delegation_gives_root_and_child_independent_codeact_runtimes() -> None:
    calls: list[tuple[str, int]] = []

    def parent_double(value: int) -> int:
        calls.append(("parent", value))
        return value * 2

    def child_double(value: int) -> int:
        calls.append(("child", value))
        return value * 2

    async def child_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _tool_returns(messages, "run_code"):
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args='{"code":"child_value = await child_double(value=3)\\nchild_value"}',
                    tool_call_id="child-code-1",
                )
            }
        else:
            yield "child-done"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="codeact-child-v1",
        model=FunctionModel(stream_function=child_model),
        capabilities=(_codeact_tools(child_double, allowed=("child_double",)), CodeActCapability()),
    )

    async def parent_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        code_returns = _tool_returns(messages, "run_code")
        delegate_returns = _tool_returns(messages, "delegate")
        if not code_returns:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args='{"code":"root_value = await parent_double(value=2)\\nroot_value"}',
                    tool_call_id="parent-code-1",
                )
            }
        elif not delegate_returns:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args='{"subagent":"reviewer","task":{"request":"run child code"}}',
                    tool_call_id="delegate-1",
                )
            }
        else:
            yield "parent-done"

    parent = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="codeact-parent-v1",
        model=FunctionModel(stream_function=parent_model),
        capabilities=(
            _codeact_tools(parent_double, allowed=("parent_double",)),
            CodeActCapability(),
            DelegationCapability(),
        ),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Run one bounded child task.",
                agent=child,
            ),
        ),
    )

    async def allow(*args: Any, **kwargs: Any) -> InvocationPolicyDecision:
        del args, kwargs
        return InvocationPolicyDecision.allow()

    async def bind_child(child, input, child_instance_id, continuation, usage_limits):
        del child, input, continuation, usage_limits
        return RunBindings(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="child"),
                agent_instance_id=f"internal-{child_instance_id}",
                parent_agent_instance_id="parent-1",
                delegation_id=child_instance_id,
            ),
            environment=NoopEnvironmentRunBinding(),
        )

    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="parent"),
            agent_instance_id="parent-1",
        ),
        environment=NoopEnvironmentRunBinding(),
        capabilities=(
            InvocationPolicyCapability(evaluator=allow),
            DelegationRunCapability(binder=bind_child),
        ),
    )
    result = await HarnessBuilder().build(parent).run("start", bindings=bindings)

    assert result.output_or_raise() == "parent-done"
    assert calls == [("parent", 2), ("child", 3)]
