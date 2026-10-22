from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from a13n_harness import AbstractHarnessPlugin, AgentContext, AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.tools import ToolExposure, ToolsetExposure
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai import RunContext, Tool, ToolDefinition, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, Capability, ToolSearch
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio


def _source(*tools, group="crm", eligible=True, instructions="Use exact customer IDs."):
    return Capability(
        toolsets=[
            CodeActPolicyToolset(
                FunctionToolset(list(tools), id=group, instructions=instructions), CodeActToolPolicy(default=eligible)
            )
        ]
    )


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


async def _run(
    capabilities: tuple,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    inspect: Callable[[int, AgentInfo], None] | None = None,
    bindings: RunBindings | None = None,
    usage_limits: UsageLimits | None = None,
    toolset_instructions: bool = True,
    tool_exposure: ToolExposure | None = None,
    plugins: tuple[AbstractHarnessPlugin, ...] = (),
    profile: ModelProfile | None = None,
):
    requests = 0
    returns: list[ToolReturnPart] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests, returns
        if inspect:
            inspect(requests, info)
        returns = _returns(messages)
        index = requests
        requests += 1
        if index >= len(calls):
            yield "done"
            return
        name, args = calls[index]
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{index}")}

    executable = HarnessBuilder().build(
        AgentSpec(usage_limits=usage_limits or UsageLimits(), toolset_instructions=toolset_instructions),
        output_type=str,
        model=FunctionModel(stream_function=model, profile=profile),
        capabilities=capabilities,
        tool_exposure=tool_exposure
        or ToolExposure(toolsets={"crm": ToolsetExposure(exposure="progressive", description="Customer operations")}),
        plugins=plugins,
    )
    result = await executable.run("test", bindings=bindings or RunBindings.embedded())
    return result, returns


@pytest.mark.parametrize("codeact", [False, True])
async def test_progressive_preserves_managed_authorization_and_successful_call_accounting(codeact: bool) -> None:
    from a13n_harness.tools import HarnessTool, InvocationPolicyCapability, InvocationPolicyDecision

    from .test_managed_invocation import _metadata

    effects: list[int] = []
    policy_calls: list[tuple[str, dict]] = []
    usage: list[int] = []
    manager_ids: list[int] = []

    def write(value: int) -> int:
        effects.append(value)
        return value

    async def policy(invocation, metadata, *, context):
        policy_calls.append((metadata.tool_id, dict(invocation.normalized_arguments)))
        return InvocationPolicyDecision.deny("not allowed")

    @dataclass
    class InspectUsage(AbstractCapability):
        async def before_tool_execute(self, ctx, *, call, tool_def, args):
            manager_ids.append(id(ctx.tool_manager))
            return args

        async def before_model_request(self, ctx, request_context):
            usage.append(ctx.usage.tool_calls)
            return request_context

    capabilities = (_source(HarnessTool(write, harness_metadata=_metadata())), InspectUsage())
    call = ("execute_tool", {"name": "write", "arguments": {"value": "7"}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await execute_tool(name='write', arguments={'value': '7'})"})
    result, _ = await _run(
        capabilities,
        [call],
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )
    assert result.output_or_raise() == "done"
    assert effects == []
    assert policy_calls == [("math.add", {"value": 7})]
    assert usage == [0, 0]
    assert len(manager_ids) == 2 and len(set(manager_ids)) == 1


@pytest.mark.parametrize("codeact", [False, True])
async def test_progressive_usage_limit_admits_one_actual_target_not_a_second_router_call(codeact: bool) -> None:
    effects = []
    usage = []

    def work() -> str:
        effects.append(True)
        return "worked"

    @dataclass
    class Usage(AbstractCapability):
        async def before_model_request(self, ctx, request_context):
            usage.append(ctx.usage.tool_calls)
            return request_context

    capabilities = (_source(work), Usage())
    call = ("execute_tool", {"name": "work", "arguments": {}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await execute_tool(name='work', arguments={})"})
    result, _ = await _run(capabilities, [call], usage_limits=UsageLimits(tool_calls_limit=2))
    assert result.output_or_raise() == "done"
    assert effects == [True]
    assert usage == [0, 2]
    effects.clear()
    # The parent + target cannot fit; reject before the target has any effect.
    result, _ = await _run(capabilities, [call], usage_limits=UsageLimits(tool_calls_limit=1))
    assert effects == []


@pytest.mark.parametrize("resolution", ["approve", "deny", "unresolved"])
async def test_progressive_preserves_inline_approval_without_leaking_unresolved_nested_calls(resolution: str) -> None:
    from pydantic_ai.capabilities import HandleDeferredToolCalls
    from pydantic_ai.tools import DeferredToolResults, ToolDenied

    effects = []

    def change(value: int) -> int:
        effects.append(value)
        return value

    async def handler(ctx, requests):
        assert len(requests.approvals) == 1
        request = requests.approvals[0]
        assert request.tool_name == "change"
        return DeferredToolResults(
            approvals={request.tool_call_id: True if resolution == "approve" else ToolDenied("No")}
        )

    caps = (_source(Tool(change, requires_approval=True)),)
    if resolution != "unresolved":
        caps += (HandleDeferredToolCalls(handler=handler),)
    result, returns = await _run(caps, [("execute_tool", {"name": "change", "arguments": {"value": 3}})])
    if resolution == "unresolved":
        assert result.status == "suspended"
        assert not effects
        return
    assert result.output_or_raise() == "done"
    assert effects == ([3] if resolution == "approve" else [])
    assert returns[-1].outcome == ("success" if resolution == "approve" else "failed")


async def test_source_preparation_updates_schema_and_disappearing_groups_remove_controls() -> None:
    def lookup(region: str) -> str:
        return region

    async def prepare(ctx: RunContext, definition: ToolDefinition):
        if ctx.run_step > 2:
            return None
        return replace(
            definition,
            parameters_json_schema={
                **definition.parameters_json_schema,
                "properties": {"region": {"type": "string", "enum": [f"region-{ctx.run_step}"]}},
            },
        )

    def inspect(step, info):
        names = {tool.name for tool in info.function_tools}
        assert ("describe_tools" in names) == (step < 2)

    result, returns = await _run(
        (_source(Tool(lookup, prepare=prepare)),),
        [("describe_tools", {"names": ["lookup"]}), ("describe_tools", {"names": ["lookup"]})],
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    schemas = [item.content["tools"][0]["parameters_json_schema"] for item in returns]
    assert schemas[0]["properties"]["region"]["enum"] == ["region-1"]
    assert schemas[1]["properties"]["region"]["enum"] == ["region-2"]


async def test_progressive_supersession_removes_target_from_search_and_codeact() -> None:
    from a13n_harness.tools import HarnessTool

    from .test_managed_invocation import _metadata

    def old() -> str:
        raise AssertionError("superseded")

    def new() -> str:
        return "new"

    result, returns = await _run(
        (
            CodeActCapability(),
            _source(
                HarnessTool(old, harness_metadata=replace(_metadata("old"), superseded_by_tool_ids=frozenset({"new"}))),
                HarnessTool(new, harness_metadata=_metadata("new")),
            ),
        ),
        [("list_tools", {"toolset": "crm"}), ("execute_tool", {"name": "old", "arguments": {}})],
    )
    assert result.output_or_raise() == "done"
    assert [tool["name"] for tool in returns[0].content["tools"]] == ["new"]


@pytest.mark.parametrize("codeact", [False, True])
async def test_progressive_retains_multimodal_tool_return_content(codeact: bool) -> None:
    def rich() -> ToolReturn:
        return ToolReturn(return_value={"value": 1}, content="supplemental evidence", metadata={"source": "test"})

    caps = (_source(rich),)
    call = ("execute_tool", {"name": "rich", "arguments": {}})
    if codeact:
        caps += (CodeActCapability(),)
        call = ("run_code", {"code": "await execute_tool(name='rich', arguments={})"})
    result, returns = await _run(caps, [call])
    assert result.output_or_raise() == "done"
    from a13n_harness.tools._output import tool_execution_value

    assert tool_execution_value(returns[-1].content, returns[-1].metadata) == {"value": 1}
    assert returns[-1].content == [{"value": 1}, "supplemental evidence"]
    assert not any(
        "supplemental evidence" in str(part.content)
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


async def test_progressive_sequential_tool_keeps_awaitable_barrier_in_codeact() -> None:
    import asyncio

    events = []

    async def work(value: int) -> int:
        events.append(f"start-{value}")
        await asyncio.sleep(0.001)
        events.append(f"end-{value}")
        return value

    result, returns = await _run(
        (CodeActCapability(), _source(Tool(work, sequential=True))),
        [
            (
                "run_code",
                {
                    "code": "import asyncio\nawait asyncio.gather(execute_tool(name='work', arguments={'value': 1}), execute_tool(name='work', arguments={'value': 2}))"
                },
            )
        ],
    )
    assert result.output_or_raise() == "done"
    assert returns[-1].content == [1, 2]
    assert events == ["start-1", "end-1", "start-2", "end-2"]


async def test_progressive_keeps_target_retry_context_and_does_not_charge_envelope_budget() -> None:
    from pydantic_ai import ModelRetry

    retries: list[int] = []

    def retrying(ctx: RunContext) -> str:
        retries.append(ctx.retry)
        if ctx.retry < 2:
            raise ModelRetry("Try this operation again")
        return "recovered"

    call = ("execute_tool", {"name": "retrying", "arguments": {}})
    result, returns = await _run(
        (_source(Tool(retrying, max_retries=3)),),
        [call, call, call],
    )
    assert result.output_or_raise() == "done"
    assert retries == [0, 1, 2]
    assert returns[-1].content == "recovered"
    assert returns[-1].tool_call_id == "call-2"


async def test_unknown_execution_failure_is_bounded_and_does_not_retry_side_effects() -> None:
    effects = []

    def mutate() -> None:
        effects.append(True)
        raise RuntimeError("private provider details")

    with pytest.raises(RuntimeError, match="private provider details"):
        await _run(
            (_source(mutate),),
            [("execute_tool", {"name": "mutate", "arguments": {}})],
        )
    assert effects == [True]


async def test_progressive_run_program_reads_source_and_dispatches_through_current_manager(tmp_path: Path) -> None:
    from a13n_harness.environment import EnvironmentAction, EnvironmentMount, EnvironmentPermissionSet
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.providers.environment.direct_local.configuration import (
        DirectLocalEnvironmentConfiguration,
        DirectLocalRootConfiguration,
    )

    from .environment_helpers import DirectLocalSource

    (tmp_path / "job.codeact.py").write_text(
        "async def main(inputs):\n"
        "    found = await search_tools(query='double', toolset='crm')\n"
        "    match = found['tools'][0]\n"
        "    return await execute_tool(name=match['name'], arguments={'value': inputs['value']})\n",
        encoding="utf-8",
    )
    provider = DirectLocalSource(
        DirectLocalEnvironmentConfiguration(root=DirectLocalRootConfiguration(path=tmp_path)),
        environment_id="proxy-program-test",
    )
    environment = create_environment_runtime(
        mounts={
            "local": EnvironmentMount(
                source=provider,
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                working_directory="/",
            )
        },
        default_mount="local",
    )
    calls: list[int] = []

    def double(value: int) -> int:
        calls.append(value)
        return value * 2

    result, returns = await _run(
        (CodeActCapability(), _source(double)),
        [("run_program", {"path": "/workspace/job.codeact.py", "inputs": {"value": 21}})],
        bindings=RunBindings.embedded(environment=environment),
        usage_limits=UsageLimits(tool_calls_limit=3),
    )
    assert result.output_or_raise() == "done"
    assert returns[-1].content == 42
    assert calls == [21]
    assert result.usage.tool_calls == 3


@pytest.mark.parametrize("codeact", [False, True])
@pytest.mark.parametrize("mode", ["deny", "ask"])
async def test_progressive_target_permission_gate_precedes_business_dispatch(codeact: bool, mode: str) -> None:
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

    effects = []

    def forbidden() -> str:
        effects.append(True)
        return "not allowed"

    capabilities = (
        _source(forbidden),
        ToolPermissionsCapability(ToolPermissions(default="allow", rules={"tool/crm/forbidden": mode})),
    )
    call = ("execute_tool", {"name": "forbidden", "arguments": {}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await execute_tool(name='forbidden', arguments={})"})
    result, _ = await _run(capabilities, [call])
    assert result.status == ("suspended" if mode == "ask" and not codeact else "completed")
    assert not effects


@pytest.mark.parametrize("codeact", [False, True])
@pytest.mark.parametrize("mode", ["allow", "inherit"])
async def test_progressive_and_codeact_use_capability_approval_override(codeact: bool, mode: str) -> None:
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

    effects = []

    def change(ctx: RunContext[AgentContext]) -> str:
        effects.append(ctx.deps.tool_approval.approved)
        return "changed"

    capabilities = (
        _source(Tool(change, requires_approval=True)),
        ToolPermissionsCapability(ToolPermissions(rules={"tool/crm/change": mode})),
    )
    call = ("execute_tool", {"name": "change", "arguments": {}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await execute_tool(name='change', arguments={})"})
    result, _ = await _run(capabilities, [call])
    assert result.status == ("suspended" if mode == "inherit" and not codeact else "completed")
    assert effects == ([False] if mode == "allow" else [])


@pytest.mark.parametrize("resolution", ["approve", "deny", "permission-deny"])
async def test_discovered_codeact_tool_preserves_permissions_and_inline_approval(resolution: str) -> None:
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
    from pydantic_ai.capabilities import HandleDeferredToolCalls
    from pydantic_ai.tools import DeferredToolResults, ToolDenied

    effects: list[int] = []
    approvals: list[str] = []

    def change(value: int) -> int:
        effects.append(value)
        return value

    async def handler(ctx, requests):
        assert len(requests.approvals) == 1
        request = requests.approvals[0]
        approvals.append(request.tool_name)
        return DeferredToolResults(
            approvals={request.tool_call_id: True if resolution == "approve" else ToolDenied("No")}
        )

    source = Capability(
        toolsets=[
            CodeActPolicyToolset(
                FunctionToolset([Tool(change, defer_loading=True, requires_approval=True)], id="secure"),
                CodeActToolPolicy(default=True),
            )
        ]
    )
    capabilities = (
        source,
        CodeActCapability(),
        ToolSearch(strategy="keywords"),
        HandleDeferredToolCalls(handler=handler),
    )
    if resolution == "permission-deny":
        capabilities += (
            ToolPermissionsCapability(ToolPermissions(default="allow", rules={"tool/secure/change": "deny"})),
        )
    result, returns = await _run(
        capabilities,
        [("search_tools", {"queries": ["change"]}), ("run_code", {"code": "await change(value='3')"})],
    )
    assert result.output_or_raise() == "done"
    assert effects == ([3] if resolution == "approve" else [])
    assert approvals == ([] if resolution == "permission-deny" else ["change"])
    assert returns[-1].outcome == ("success" if resolution == "approve" else "failed")
