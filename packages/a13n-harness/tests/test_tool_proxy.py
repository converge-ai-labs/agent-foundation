from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from a13n_harness import AbstractHarnessPlugin, AgentContext, AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import (
    CodeActCapability,
    ToolProxyCapability,
    ToolProxyConfig,
    ToolProxyGroup,
    ToolProxyPlan,
    ToolProxySelection,
)
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai import RunContext, Tool, ToolDefinition, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, Capability, ToolSearch
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _group(
    *tools: Any,
    group: str = "crm",
    eligible: bool = True,
    instructions: str = "Use exact customer IDs.",
    config: ToolProxyConfig | None = None,
) -> ToolProxyCapability:
    source = FunctionToolset(list(tools), instructions=instructions)
    return ToolProxyCapability(
        groups={
            group: ToolProxyGroup(
                CodeActPolicyToolset(source, CodeActToolPolicy(default=eligible)), f"{group} operations"
            )
        },
        config=config,
    )


async def _run(
    capabilities: tuple,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    inspect: Callable[[int, AgentInfo], None] | None = None,
    bindings: RunBindings | None = None,
    usage_limits: UsageLimits | None = None,
    toolset_instructions: bool = True,
    tool_proxy: ToolProxyPlan | None = None,
    plugins: tuple[AbstractHarnessPlugin, ...] = (),
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
        model=FunctionModel(stream_function=model),
        capabilities=capabilities,
        tool_proxy=tool_proxy,
        plugins=plugins,
    )
    result = await executable.run("test", bindings=bindings or RunBindings.embedded())
    return result, returns


async def test_proxy_discovers_exact_schemas_and_dispatches_through_original_hooks() -> None:
    effects: list[int] = []
    validated: list[str] = []
    executed: list[str] = []

    def double(value: int) -> int:
        """Double a customer value."""
        effects.append(value)
        return value * 2

    @dataclass
    class Hooks(AbstractCapability):
        async def after_tool_validate(self, ctx, *, call, tool_def, args):
            validated.append(call.tool_name)
            return args

        async def before_tool_execute(self, ctx, *, call, tool_def, args):
            executed.append(call.tool_name)
            return args

    def inspect(_step: int, info: AgentInfo) -> None:
        definitions = {tool.name: tool for tool in info.function_tools}
        assert set(definitions) == {"search_proxy_tools", "call_proxy_tool"}
        assert definitions["call_proxy_tool"].parameters_json_schema["properties"]["group"]["enum"] == ["crm"]
        assert "crm operations" in definitions["search_proxy_tools"].description
        assert "Double a customer" not in definitions["search_proxy_tools"].description
        assert "Grouped tool discovery" in info.instructions
        assert "Use exact customer IDs." not in info.instructions

    result, returns = await _run(
        (_group(double), Hooks()),
        [
            ("search_proxy_tools", {"query": "double"}),
            ("call_proxy_tool", {"group": "crm", "tool": "double", "arguments": {"value": "21"}}),
        ],
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    assert effects == [21]
    found = returns[0].content["tools"][0]
    assert found["parameters_json_schema"]["properties"]["value"]["type"] == "integer"
    assert found["instructions"] == ["Use exact customer IDs."]
    assert found["codeact_eligible"] is True
    assert returns[-1].content == 42
    assert validated == ["search_proxy_tools", "call_proxy_tool", "crm__double"]
    assert executed == validated


async def test_groups_support_same_local_name_and_configurable_control_names() -> None:
    def lookup() -> str:
        return "crm"

    def other() -> str:
        return "docs"

    config = ToolProxyConfig(search_name="find_ops", call_name="invoke_op", max_results=1)
    result, returns = await _run(
        (
            ToolProxyCapability(
                groups={
                    "crm": ToolProxyGroup(FunctionToolset([lookup]), "Customer operations"),
                    "docs": ToolProxyGroup(FunctionToolset([Tool(other, name="lookup")]), "Document operations"),
                },
                config=config,
            ),
        ),
        [
            ("find_ops", {"query": "lookup"}),
            ("find_ops", {"query": "lookup", "offset": 1}),
            ("invoke_op", {"group": "docs", "tool": "lookup", "arguments": {}}),
        ],
    )
    assert result.output_or_raise() == "done"
    assert returns[0].content["total"] == 2
    assert returns[0].content["next_offset"] == 1
    assert returns[1].content["tools"][0]["group"] == "docs"
    assert returns[2].content == "docs"


@pytest.mark.parametrize("use_plan", [False, True])
async def test_codeact_can_search_and_call_proxy_without_expanding_business_catalog(use_plan: bool) -> None:
    calls: list[int] = []

    def double(value: int) -> int:
        calls.append(value)
        return value * 2

    def inspect(_step: int, info: AgentInfo) -> None:
        runner = next(tool for tool in info.function_tools if tool.name == "run_code")
        assert "search_proxy_tools" in runner.description
        assert "call_proxy_tool" in runner.description
        assert "crm__double" not in runner.description
        assert "async def double" not in runner.description

    source = """found = await search_proxy_tools(query='double', group='crm')
match = found['tools'][0]
await call_proxy_tool(group=match['group'], tool=match['tool'], arguments={'value': 21})"""
    capability = Capability(toolsets=[CodeActPolicyToolset(FunctionToolset([double]), CodeActToolPolicy(default=True))])
    result, returns = await _run(
        (CodeActCapability(), capability if use_plan else _group(double)),
        [("run_code", {"code": source})],
        inspect=inspect,
        tool_proxy=ToolProxyPlan(groups={"crm": ToolProxySelection("CRM operations", capabilities=(capability,))})
        if use_plan
        else None,
    )
    assert result.output_or_raise() == "done"
    assert returns[-1].content == 42
    assert calls == [21]


async def test_codeact_proxy_does_not_grant_target_eligibility() -> None:
    calls = []

    def forbidden() -> str:
        calls.append(True)
        return "forbidden"

    result, returns = await _run(
        (CodeActCapability(), _group(forbidden, eligible=False)),
        [("run_code", {"code": "await call_proxy_tool(group='crm', tool='forbidden', arguments={})"})],
    )
    assert result.output_or_raise() == "done"
    assert calls == []
    # Pre-dispatch errors can be retry prompts rather than ToolReturnParts.
    assert not any(part.content == "forbidden" for part in returns)


async def test_invalid_arguments_do_not_execute_target() -> None:
    calls = []

    def typed(value: int) -> int:
        calls.append(value)
        return value

    result, _ = await _run(
        (_group(typed),),
        [("call_proxy_tool", {"group": "crm", "tool": "typed", "arguments": {"value": "invalid"}})],
    )
    assert result.output_or_raise() == "done"
    assert calls == []


async def test_proxy_composes_with_native_tool_search() -> None:
    def deferred() -> str:
        return "deferred"

    def normal() -> str:
        return "normal"

    def inspect(_step: int, info: AgentInfo) -> None:
        assert {"search_tools", "search_proxy_tools", "call_proxy_tool"} <= {tool.name for tool in info.function_tools}

    result, _ = await _run(
        (
            _group(normal),
            ToolSearch(strategy="keywords"),
            Capability(tools=[Tool(deferred, defer_loading=True)]),
        ),
        [],
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"


async def test_capability_group_wraps_a_fresh_run_replacement() -> None:
    @dataclass
    class Source(AbstractCapability):
        async def for_run(self, ctx: RunContext):
            def identify() -> str:
                return ctx.deps.run_id

            return Capability(tools=[identify], id="fresh")

    result, returns = await _run(
        (ToolProxyCapability(groups={"identity": ToolProxyGroup(Source(id="fresh"), "Current run")}),),
        [("call_proxy_tool", {"group": "identity", "tool": "identify", "arguments": {}})],
    )
    assert result.output_or_raise() == "done"
    assert isinstance(returns[-1].content, str)


async def test_search_budget_never_returns_partial_schema() -> None:
    def giant(value: str) -> str:
        return value

    result, returns = await _run(
        (_group(Tool(giant, description="x" * 2000), config=ToolProxyConfig(max_search_bytes=1024)),),
        [("search_proxy_tools", {"query": "giant"})],
    )
    assert result.output_or_raise() == "done"
    assert returns[-1].outcome == "failed"
    assert "no partial schema" in returns[-1].content


@pytest.mark.parametrize("codeact", [False, True])
async def test_proxy_preserves_managed_authorization_and_successful_call_accounting(codeact: bool) -> None:
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

    capabilities = (_group(HarnessTool(write, harness_metadata=_metadata())), InspectUsage())
    call = ("call_proxy_tool", {"group": "crm", "tool": "write", "arguments": {"value": "7"}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await call_proxy_tool(group='crm', tool='write', arguments={'value': '7'})"})
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
async def test_proxy_usage_limit_admits_one_actual_target_not_a_second_router_call(codeact: bool) -> None:
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

    capabilities = (_group(work), Usage())
    call = ("call_proxy_tool", {"group": "crm", "tool": "work", "arguments": {}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await call_proxy_tool(group='crm', tool='work', arguments={})"})
    result, _ = await _run(capabilities, [call], usage_limits=UsageLimits(tool_calls_limit=2))
    assert result.output_or_raise() == "done"
    assert effects == [True]
    assert usage == [0, 2]
    effects.clear()
    # The parent + target cannot fit; reject before the target has any effect.
    result, _ = await _run(capabilities, [call], usage_limits=UsageLimits(tool_calls_limit=1))
    assert effects == []


@pytest.mark.parametrize("resolution", ["approve", "deny", "unresolved"])
async def test_proxy_preserves_inline_approval_without_leaking_unresolved_nested_calls(resolution: str) -> None:
    from pydantic_ai.capabilities import HandleDeferredToolCalls
    from pydantic_ai.tools import DeferredToolResults, ToolDenied

    effects = []

    def change(value: int) -> int:
        effects.append(value)
        return value

    async def handler(ctx, requests):
        assert len(requests.approvals) == 1
        request = requests.approvals[0]
        assert request.tool_name == "crm__change"
        return DeferredToolResults(
            approvals={request.tool_call_id: True if resolution == "approve" else ToolDenied("No")}
        )

    caps = (_group(Tool(change, requires_approval=True)),)
    if resolution != "unresolved":
        caps += (HandleDeferredToolCalls(handler=handler),)
    result, returns = await _run(
        caps, [("call_proxy_tool", {"group": "crm", "tool": "change", "arguments": {"value": 3}})]
    )
    assert result.output_or_raise() == "done"
    assert effects == ([3] if resolution == "approve" else [])
    assert returns[-1].outcome == ("success" if resolution == "approve" else "failed")


async def test_group_source_preparation_updates_schema_and_disappearing_groups_remove_controls() -> None:
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
        assert ("search_proxy_tools" in names) == (step < 2)

    result, returns = await _run(
        (_group(Tool(lookup, prepare=prepare)),),
        [("search_proxy_tools", {"query": "lookup"}), ("search_proxy_tools", {"query": "lookup"})],
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    schemas = [item.content["tools"][0]["parameters_json_schema"] for item in returns]
    assert schemas[0]["properties"]["region"]["enum"] == ["region-1"]
    assert schemas[1]["properties"]["region"]["enum"] == ["region-2"]


async def test_proxy_supersession_removes_target_from_search_and_codeact() -> None:
    from a13n_harness.tools import HarnessTool

    from .test_managed_invocation import _metadata

    def old() -> str:
        raise AssertionError("superseded")

    def new() -> str:
        return "new"

    result, returns = await _run(
        (
            CodeActCapability(),
            _group(
                HarnessTool(old, harness_metadata=replace(_metadata("old"), superseded_by_tool_ids=frozenset({"new"}))),
                HarnessTool(new, harness_metadata=_metadata("new")),
            ),
        ),
        [("search_proxy_tools", {"query": ""}), ("call_proxy_tool", {"group": "crm", "tool": "old", "arguments": {}})],
    )
    assert result.output_or_raise() == "done"
    assert [tool["tool"] for tool in returns[0].content["tools"]] == ["new"]


@pytest.mark.parametrize("codeact", [False, True])
async def test_proxy_retains_multimodal_tool_return_content(codeact: bool) -> None:
    def rich() -> ToolReturn:
        return ToolReturn(return_value={"value": 1}, content="supplemental evidence", metadata={"source": "test"})

    caps = (_group(rich),)
    call = ("call_proxy_tool", {"group": "crm", "tool": "rich", "arguments": {}})
    if codeact:
        caps += (CodeActCapability(),)
        call = ("run_code", {"code": "await call_proxy_tool(group='crm', tool='rich', arguments={})"})
    result, returns = await _run(caps, [call])
    assert result.output_or_raise() == "done"
    assert returns[-1].content == {"value": 1}
    assert any(
        "supplemental evidence" in str(part.content)
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


async def test_grouped_sequential_tool_keeps_awaitable_barrier_in_codeact() -> None:
    import asyncio

    events = []

    async def work(value: int) -> int:
        events.append(f"start-{value}")
        await asyncio.sleep(0.001)
        events.append(f"end-{value}")
        return value

    result, returns = await _run(
        (CodeActCapability(), _group(Tool(work, sequential=True))),
        [
            (
                "run_code",
                {
                    "code": "import asyncio\nawait asyncio.gather(call_proxy_tool(group='crm', tool='work', arguments={'value': 1}), call_proxy_tool(group='crm', tool='work', arguments={'value': 2}))"
                },
            )
        ],
    )
    assert result.output_or_raise() == "done"
    assert returns[-1].content == [1, 2]
    assert events == ["start-1", "end-1", "start-2", "end-2"]


async def test_proxy_keeps_target_retry_context_and_does_not_charge_envelope_budget() -> None:
    from pydantic_ai import ModelRetry

    retries: list[int] = []

    def retrying(ctx: RunContext) -> str:
        retries.append(ctx.retry)
        if ctx.retry < 2:
            raise ModelRetry("Try this operation again")
        return "recovered"

    call = ("call_proxy_tool", {"group": "crm", "tool": "retrying", "arguments": {}})
    result, returns = await _run(
        (_group(Tool(retrying, max_retries=3)),),
        [call, call, call],
    )
    assert result.output_or_raise() == "done"
    assert retries == [0, 1, 2]
    assert returns[-1].content == "recovered"
    assert returns[-1].tool_call_id == "call-2"


async def test_final_directory_rejects_deferral_added_by_outer_capability() -> None:
    from a13n_harness.capabilities.tool_proxy import _ToolProxySurfaceCapability
    from a13n_harness.toolsets.tool_proxy import _GroupedToolset

    def hidden() -> str:
        return "not loaded"

    source = Capability(
        toolsets=[_GroupedToolset(FunctionToolset([hidden]), "late", "Deferred source", "late-source")],
        id="late-source",
        defer_loading=True,
    )
    result, returns = await _run((_ToolProxySurfaceCapability(), source), [])
    assert result.status == "failed"
    assert returns == []


async def test_outer_prefix_preserves_group_identity_source_guidance_and_codeact_policy() -> None:
    def value() -> int:
        return 1

    source = CodeActPolicyToolset(
        FunctionToolset([value], instructions="Source-specific requirement"), CodeActToolPolicy(default=True)
    ).prefixed("outer")
    result, returns = await _run(
        (ToolProxyCapability(groups={"crm": ToolProxyGroup(source, "Customer data")}),),
        [
            ("search_proxy_tools", {"query": "value"}),
            ("call_proxy_tool", {"group": "crm", "tool": "outer_value", "arguments": {}}),
        ],
    )
    assert result.output_or_raise() == "done"
    assert returns[0].content["tools"][0]["instructions"] == ["Source-specific requirement"]
    assert returns[0].content["tools"][0]["codeact_eligible"] is True
    assert returns[1].content == 1


async def test_unknown_execution_failure_is_bounded_and_does_not_retry_side_effects() -> None:
    effects = []

    def mutate() -> None:
        effects.append(True)
        raise RuntimeError("private provider details")

    result, returns = await _run(
        (_group(mutate),),
        [("call_proxy_tool", {"group": "crm", "tool": "mutate", "arguments": {}})],
    )
    assert result.output_or_raise() == "done"
    assert effects == [True]
    assert returns[-1].outcome == "failed"
    assert "private provider details" not in returns[-1].content
    assert "uncertain" in returns[-1].content


@pytest.mark.parametrize("codeact", [False, True])
async def test_large_groups_keep_model_surface_constant_and_search_bounded(codeact: bool) -> None:
    surfaces: list[str] = []

    def value(index: int) -> int:
        return index

    for count in (10, 1000):
        observed: list[str] = []

        def inspect(_step: int, info: AgentInfo, observed: list[str] = observed) -> None:
            definitions = [
                {"name": tool.name, "description": tool.description, "schema": tool.parameters_json_schema}
                for tool in info.function_tools
            ]
            observed.append(json.dumps(definitions, sort_keys=True))
            assert all(not tool.name.startswith("crm__") for tool in info.function_tools)

        caps = (
            _group(
                *(Tool(value, name=f"operation_{index:04}") for index in range(count)),
                config=ToolProxyConfig(max_results=2),
            ),
        )
        if codeact:
            caps += (CodeActCapability(),)
        result, returns = await _run(
            caps,
            [
                ("search_proxy_tools", {"query": "", "limit": 2}),
                ("search_proxy_tools", {"query": "", "limit": 2, "offset": count - 1}),
                (
                    "call_proxy_tool",
                    {"group": "crm", "tool": f"operation_{count - 1:04}", "arguments": {"index": count}},
                ),
            ],
            inspect=inspect,
        )
        assert result.output_or_raise() == "done"
        assert returns[0].content["total"] == count
        assert len(returns[0].content["tools"]) == 2
        assert returns[0].content["next_offset"] == 2
        assert len(json.dumps(returns[0].content).encode()) <= 32_768
        assert returns[1].content["next_offset"] is None
        assert len(returns[1].content["tools"]) == 1
        assert returns[2].content == count
        surfaces.append(observed[0])
    assert surfaces[0] == surfaces[1]


@pytest.mark.parametrize("use_plan", [False, True])
async def test_proxy_run_program_reads_source_and_dispatches_through_current_manager(
    tmp_path: Path, use_plan: bool
) -> None:
    from a13n_environment import DirectLocalProviderConfiguration, DirectLocalRootConfiguration
    from a13n_harness.environment import EnvironmentAction, EnvironmentPermissionSet
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.environment.providers import EnvironmentRuntimeMount

    from .environment_helpers import DirectLocalEnvironmentProviderBinding

    (tmp_path / "job.codeact.py").write_text(
        "async def main(inputs):\n"
        "    found = await search_proxy_tools(query='double', group='crm')\n"
        "    match = found['tools'][0]\n"
        "    return await call_proxy_tool(group=match['group'], tool=match['tool'], arguments={'value': inputs['value']})\n",
        encoding="utf-8",
    )
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(root=DirectLocalRootConfiguration(path=tmp_path)),
        environment_id="proxy-program-test",
    )
    environment = create_environment_runtime(
        mounts={
            "local": EnvironmentRuntimeMount(
                binding=provider,
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

    capability = Capability(toolsets=[CodeActPolicyToolset(FunctionToolset([double]), CodeActToolPolicy(default=True))])
    result, returns = await _run(
        (CodeActCapability(), capability if use_plan else _group(double)),
        [("run_program", {"path": "/workspace/job.codeact.py", "inputs": {"value": 21}})],
        tool_proxy=ToolProxyPlan(groups={"crm": ToolProxySelection("CRM operations", capabilities=(capability,))})
        if use_plan
        else None,
        bindings=RunBindings.embedded(environment=environment),
        usage_limits=UsageLimits(tool_calls_limit=3),
    )
    assert result.output_or_raise() == "done"
    assert returns[-1].content == 42
    assert calls == [21]
    assert result.usage.tool_calls == 3


async def test_proxy_instruction_switch_and_custom_names_are_consistent() -> None:
    def value() -> int:
        return 1

    config = ToolProxyConfig(search_name="find_ops", call_name="invoke_op")
    for enabled in (True, False):

        def inspect(_step: int, info: AgentInfo, enabled: bool = enabled) -> None:
            instructions = info.instructions or ""
            assert ("Grouped tool discovery" in instructions) is enabled
            assert "search_proxy_tools" not in instructions
            assert "call_proxy_tool" not in instructions
            if enabled:
                assert "find_ops" in instructions
                assert "invoke_op" in instructions

        result, returns = await _run(
            (_group(value, config=config),),
            [("find_ops", {"query": ""})],
            inspect=inspect,
            toolset_instructions=enabled,
        )
        assert result.output_or_raise() == "done"
        assert returns[0].content["tools"][0]["instructions"] == (["Use exact customer IDs."] if enabled else [])


async def test_group_wraps_combined_capabilities_without_duplicate_tools_or_hooks() -> None:
    from pydantic_ai.capabilities import CombinedCapability

    bound: list[str] = []
    executed: list[str] = []

    @dataclass
    class Source(AbstractCapability):
        id: str

        async def for_run(self, ctx):
            bound.append(self.id)
            return self

        def get_toolset(self):
            def identify() -> str:
                return self.id

            return FunctionToolset([Tool(identify, name=self.id)])

        async def before_tool_execute(self, ctx, *, call, tool_def, args):
            executed.append(f"{self.id}:{call.tool_name}")
            return args

    source = CombinedCapability([Source(id="first"), CombinedCapability([Source(id="second")])])
    result, returns = await _run(
        (ToolProxyCapability(groups={"combined": ToolProxyGroup(source, "Combined source")}),),
        [
            ("search_proxy_tools", {"query": ""}),
            ("call_proxy_tool", {"group": "combined", "tool": "second", "arguments": {}}),
        ],
    )
    assert result.output_or_raise() == "done"
    assert bound == ["first", "second"]
    assert [tool["tool"] for tool in returns[0].content["tools"]] == ["first", "second"]
    assert returns[1].content == "second"
    assert len(executed) == len(set(executed)) == 6


def test_proxy_configuration_errors_explain_how_to_fix_the_source() -> None:
    from a13n_harness.tools.tool_proxy import validate_proxy_target
    from pydantic_ai.exceptions import UserError

    with pytest.raises(ValueError, match="start with a letter"):
        ToolProxyConfig(search_name="bad name")
    with pytest.raises(ValueError, match=r"ToolProxyGroup\.description must be non-blank"):
        ToolProxyCapability(groups={"crm": ToolProxyGroup(FunctionToolset(), " ")})
    with pytest.raises(UserError, match="remove defer_loading=True"):
        validate_proxy_target(ToolDefinition(name="lookup", defer_loading=True))
    with pytest.raises(UserError, match="native=False, local=True"):
        validate_proxy_target(ToolDefinition(name="lookup", unless_native=True))


async def test_unavailable_proxy_target_tells_agent_to_rediscover_without_executing() -> None:
    from pydantic_ai.messages import RetryPromptPart

    def value() -> int:
        pytest.fail("An unavailable name must never fall back to another tool")

    result, _ = await _run(
        (_group(value),),
        [("call_proxy_tool", {"group": "crm", "tool": "missing", "arguments": {}})],
    )
    assert result.output_or_raise() == "done"
    retry = next(
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    )
    assert "crm/missing" in retry.content
    assert "Search again" in retry.content


async def test_empty_groups_leave_direct_tools_unchanged() -> None:
    def status() -> str:
        return "ready"

    def inspect(_step: int, info: AgentInfo) -> None:
        assert [tool.name for tool in info.function_tools] == ["status"]
        assert "Grouped tool discovery" not in (info.instructions or "")

    result, returns = await _run(
        (ToolProxyCapability(groups={}), Capability(toolsets=[FunctionToolset([status])])),
        [("status", {})],
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    assert returns[0].content == "ready"


async def test_group_mapping_is_consumed_at_construction() -> None:
    def value() -> int:
        return 42

    descriptor = ToolProxyGroup(source=FunctionToolset([value]), description="Customer records")
    assert not isinstance(descriptor, AbstractCapability)
    groups = {"crm": descriptor}
    proxy = ToolProxyCapability(groups=groups)
    groups.clear()
    result, returns = await _run((proxy,), [("call_proxy_tool", {"group": "crm", "tool": "value", "arguments": {}})])
    assert result.output_or_raise() == "done"
    assert returns[0].content == 42


def test_group_configuration_requires_resolved_sources_and_descriptors() -> None:
    with pytest.raises(TypeError, match="resolve Host configuration"):
        ToolProxyGroup(source="customer-plugin", description="Customer records")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"groups\['crm'\] must be ToolProxyGroup"):
        ToolProxyCapability(groups={"crm": FunctionToolset()})  # type: ignore[dict-item]


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("codeact", [False, True])
async def test_host_plugin_selects_presentation_after_agent_binding(grouped: bool, codeact: bool) -> None:
    log: list[str] = []
    runs: list[str] = []
    executed: list[str] = []

    class BoundSource(Capability):
        async def before_run(self, ctx):
            log.append("source:before_run")

        async def before_tool_execute(self, ctx, *, call, tool_def, args):
            executed.append(call.tool_name)
            return args

    class Source(AbstractCapability):
        def for_agent(self, agent):
            log.append("source:for_agent")
            return self

        async def for_run(self, ctx):
            runs.append(ctx.deps.run_id)

            def identify() -> str:
                return ctx.deps.run_id

            return BoundSource(
                instructions="Preserve capability instructions.",
                toolsets=[
                    CodeActPolicyToolset(
                        FunctionToolset([identify], instructions="Use the current run identity."),
                        CodeActToolPolicy(default=True),
                    )
                ],
            )

    def build_sources():
        log.append("factory")

        def status() -> str:
            return "ready"

        return {"crm": Source(), "host": Capability(toolsets=[FunctionToolset([status])])}

    @dataclass
    class HostToolsPlugin(AbstractHarnessPlugin):
        source_factory: Callable[[], Mapping[str, AbstractCapability]]
        group_descriptions: Mapping[str, str]
        sources: Mapping[str, AbstractCapability] = field(default_factory=dict)

        @property
        def plugin_id(self) -> str:
            return "host-tools"

        def for_agent(self):
            log.append("plugin:for_agent")
            return replace(self, sources=dict(self.source_factory()))

        def get_capabilities(self):
            assert self.sources  # Native for_agent must precede contribution extraction.
            log.append("plugin:get_capabilities")
            direct = []
            groups = {}
            for name, source in self.sources.items():
                if name in self.group_descriptions:
                    groups[name] = ToolProxyGroup(source=source, description=self.group_descriptions[name])
                else:
                    direct.append(source)
            if groups:
                direct.append(ToolProxyCapability(groups=groups))
            return tuple(direct)

        async def for_run(self, context: AgentContext):
            log.append("plugin:for_run")
            return self

        def wrap_run(self, exchange, call_next):
            log.append("plugin:wrap_run")
            return call_next(exchange)

    target = ("call_proxy_tool", {"group": "crm", "tool": "identify", "arguments": {}}) if grouped else ("identify", {})
    if codeact:
        code = "await call_proxy_tool(group='crm', tool='identify', arguments={})" if grouped else "await identify()"
        target = ("run_code", {"code": code})
    calls = [target, ("status", {})]
    requests = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        names = {tool.name for tool in info.function_tools}
        assert "status" in names
        assert "crm__identify" not in names
        assert "Preserve capability instructions." in (info.instructions or "")
        if codeact:
            runner = next(tool for tool in info.function_tools if tool.name == "run_code")
            assert ("call_proxy_tool" in runner.description) is grouped
            assert ("async def identify" in runner.description) is not grouped
        else:
            assert ("identify" in names) is not grouped
            assert ("call_proxy_tool" in names) is grouped
        index = requests
        requests += 1
        if index < len(calls):
            name, args = calls[index]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{index}")}
        else:
            yield "done"

    plugin = HostToolsPlugin(build_sources, {"crm": "Customer records"} if grouped else {})
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        plugins=(plugin,),
        capabilities=(CodeActCapability(),) if codeact else (),
    )
    assert log == ["plugin:for_agent", "factory", "plugin:get_capabilities", "source:for_agent"]
    for _ in range(2):
        requests = 0
        result = await executable.run("test", bindings=RunBindings.embedded())
        assert result.output_or_raise() == "done"
        returns = _returns(result.all_messages())
        assert [part.content for part in returns] == [runs[-1], "ready"]
    assert len(set(runs)) == 2
    for event in ("factory", "plugin:get_capabilities", "source:for_agent"):
        assert log.count(event) == 1
    for event in ("source:before_run", "plugin:for_run", "plugin:wrap_run"):
        assert log.count(event) == 2
    assert executed.count("crm__identify" if grouped else "identify") == 2
    assert executed.count("status") == 2


@pytest.mark.parametrize("plugin_source", [False, True])
def test_grouping_does_not_authorize_reserved_source_capabilities(plugin_source: bool) -> None:
    from a13n_harness import DefinitionError
    from a13n_harness.capabilities import CodeActCapability
    from a13n_harness.tools.surface import ToolSurfaceCapability

    # A plugin cannot contribute definition-only CodeAct; neither source can
    # contribute mandatory surface infrastructure, even behind a group wrapper.
    source = CodeActCapability() if plugin_source else ToolSurfaceCapability()
    proxy = ToolProxyCapability(groups={"restricted": ToolProxyGroup(source, "Restricted source")})

    class Plugin(AbstractHarnessPlugin):
        @property
        def plugin_id(self) -> str:
            return "restricted-tools"

        def get_capabilities(self):
            return (proxy,)

    with pytest.raises(DefinitionError) as error:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(lambda messages, info: "done"),
            capabilities=() if plugin_source else (proxy,),
            plugins=(Plugin(),) if plugin_source else (),
        )
    assert error.value.code == "capability_scope_invalid"
    assert error.value.details["source"] == ("plugin" if plugin_source else "definition")


async def test_grouped_combined_preserves_independent_native_ordering_and_instructions() -> None:
    from pydantic_ai.capabilities import CapabilityOrdering, CombinedCapability

    events: list[str] = []

    class Outer(Capability):
        def get_ordering(self):
            return CapabilityOrdering(position="outermost")

        async def before_run(self, ctx):
            events.append("outer")

    class Inner(Capability):
        def get_ordering(self):
            return CapabilityOrdering(position="innermost")

        async def before_run(self, ctx):
            events.append("inner")

    class Container(CombinedCapability):
        def get_instructions(self):
            return "Custom container instructions."

    source = Container([Outer(id="outer"), Inner(id="inner")])
    proxy = ToolProxyCapability(groups={"ordered": ToolProxyGroup(source, "Ordered tools")})

    def inspect(_step, info):
        assert "Custom container instructions." in info.instructions

    result, _ = await _run((proxy,), [], inspect=inspect)
    assert result.output_or_raise() == "done"
    assert events == ["outer", "inner"]


@pytest.mark.parametrize("instance_ordering", [False, True])
async def test_build_plan_preserves_cross_group_order_and_plugin_binding(instance_ordering: bool) -> None:
    from a13n_harness.capabilities import ToolProxyPlan, ToolProxySelection
    from pydantic_ai.capabilities import CapabilityOrdering

    events: list[str] = []

    class First(Capability):
        async def before_run(self, ctx):
            events.append("first")

    first = First(id="first")

    class Middle(Capability):
        def get_ordering(self):
            return CapabilityOrdering(wrapped_by=(first if instance_ordering else First,))

        async def before_run(self, ctx):
            events.append("middle")

    middle = Middle(id="middle")

    class Last(Capability):
        def get_ordering(self):
            return CapabilityOrdering(wrapped_by=(middle if instance_ordering else Middle,))

        async def before_run(self, ctx):
            events.append("last")

    last = Last(id="last")

    class Plugin(AbstractHarnessPlugin):
        @property
        def plugin_id(self):
            return "ordered-plugin"

        def for_agent(self):
            events.append("plugin:bind")
            return self

        def get_capabilities(self):
            events.append("plugin:collect")
            return (last,)

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text_stream()),
        capabilities=(middle, first),
        plugins=(Plugin(),),
        tool_proxy=ToolProxyPlan(
            groups={
                "ordered": ToolProxySelection("Ordered tools", capabilities=(first,), plugins=("ordered-plugin",)),
            }
        ),
    )
    result = await executable.run("test", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert events == ["plugin:bind", "plugin:collect", "first", "middle", "last"]


async def _text_stream() -> AsyncIterator[str]:
    yield "done"


async def test_plan_groups_exact_plugin_instances_and_keeps_unlisted_sources_direct() -> None:
    events: list[str] = []

    @dataclass
    class Plugin(AbstractHarnessPlugin):
        name: str

        @property
        def plugin_id(self):
            return self.name

        def for_agent(self):
            events.append(f"bind:{self.name}")
            return self

        def get_capabilities(self):
            events.append(f"collect:{self.name}")

            def identify() -> str:
                return self.name

            return (Capability(id=self.name, toolsets=[FunctionToolset([identify])]),)

    def local() -> str:
        return "local"

    source = Capability(id="host-source", toolsets=[FunctionToolset([local])])
    result, returns = await _run(
        (source,),
        [
            ("call_proxy_tool", {"group": "first", "tool": "identify", "arguments": {}}),
            ("call_proxy_tool", {"group": "second", "tool": "identify", "arguments": {}}),
            ("call_proxy_tool", {"group": "first", "tool": "local", "arguments": {}}),
            ("identify", {}),
        ],
        plugins=(Plugin("plugin-one"), Plugin("plugin-two"), Plugin("plugin-direct")),
        tool_proxy=ToolProxyPlan(
            groups={
                "second": ToolProxySelection("Second account", plugins=("plugin-two",)),
                "first": ToolProxySelection(
                    "First account and Host tools", capabilities=(source,), plugins=("plugin-one",)
                ),
            }
        ),
    )
    assert result.output_or_raise() == "done"
    assert [item.content for item in returns] == ["plugin-one", "plugin-two", "local", "plugin-direct"]
    assert events == [
        "bind:plugin-one",
        "collect:plugin-one",
        "bind:plugin-two",
        "collect:plugin-two",
        "bind:plugin-direct",
        "collect:plugin-direct",
    ]


async def test_plan_duplicate_tool_error_names_group_and_both_plugin_instances() -> None:
    from pydantic_ai.exceptions import UserError

    @dataclass
    class Plugin(AbstractHarnessPlugin):
        name: str

        @property
        def plugin_id(self):
            return self.name

        def get_capabilities(self):
            def lookup() -> str:
                return self.name

            return (Capability(toolsets=[FunctionToolset([lookup])]),)

    from a13n_harness.plugins import bind_agent_plugins
    from pydantic_ai import Agent

    _, contributions = bind_agent_plugins((Plugin("plugin-one"), Plugin("plugin-two")))
    sources = tuple(source for values in contributions.values() for source in values)
    plan = ToolProxyPlan(
        groups={
            "account": ToolProxySelection("Account tools", plugins=("plugin-one", "plugin-two")),
        }
    )
    agent = Agent(
        FunctionModel(stream_function=lambda messages, info: _text_stream()),
        capabilities=plan._compose(sources, contributions),
    )
    with pytest.raises(UserError) as error:
        await agent.run("test")
    for name in ("account", "plugin-one", "plugin-two", "lookup"):
        assert name in str(error.value)


def test_plan_is_immutable_and_rejects_unselected_or_duplicate_sources() -> None:
    source = Capability()
    selection = ToolProxySelection("Host tools", capabilities=(source,))
    groups = {"host": selection}
    plan = ToolProxyPlan(groups=groups)
    groups.clear()
    assert "host" in plan.groups
    with pytest.raises(TypeError):
        plan.groups["other"] = selection  # type: ignore[index]
    with pytest.raises(ValueError, match="not selected"):
        plan._compose((), {})
    with pytest.raises(ValueError, match="multiple groups"):
        ToolProxyPlan(groups={"first": selection, "second": selection})._compose((source,), {})
    with pytest.raises(ValueError, match="unavailable plugin"):
        ToolProxyPlan(groups={"host": ToolProxySelection("Host tools", plugins=("plugin-missing",))})._compose((), {})


@pytest.mark.parametrize("use_plan", [False, True])
async def test_grouped_custom_container_retains_native_binding_hooks_and_state(use_plan: bool) -> None:
    from copy import copy

    from a13n_harness.capabilities import ToolProxyPlan, ToolProxySelection
    from pydantic_ai.capabilities import CombinedCapability

    events: list[str] = []

    class Source(Capability):
        async def for_run(self, ctx):
            events.append("child:run")

            def lookup() -> str:
                return "result"

            return Capability(id=self.id, toolsets=[FunctionToolset([lookup])])

    class Container(CombinedCapability):
        bound_run: str | None = None

        def for_agent(self, agent):
            events.append("container:agent")
            return super().for_agent(agent)

        async def for_run(self, ctx):
            events.append("container:run")
            bound = copy(await super().for_run(ctx))
            bound.bound_run = ctx.deps.run_id
            return bound

        async def before_run(self, ctx):
            assert self.bound_run == ctx.deps.run_id
            events.append("container:before")
            await super().before_run(ctx)

        def get_instructions(self):
            return "Container-owned instructions."

    source = Container([Source(id="source")])

    async def model(messages, info):
        assert "Container-owned instructions." in info.instructions
        assert {tool.name for tool in info.function_tools} == {"search_proxy_tools", "call_proxy_tool"}
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(source,)
        if use_plan
        else (ToolProxyCapability(groups={"group": ToolProxyGroup(source, "Tools")}),),
        tool_proxy=ToolProxyPlan(groups={"group": ToolProxySelection("Tools", capabilities=(source,))})
        if use_plan
        else None,
    )
    for _ in range(2):
        assert (await executable.run("test", bindings=RunBindings.embedded())).output_or_raise() == "done"
    assert source.bound_run is None
    assert events == ["container:agent", *["container:run", "child:run", "container:before"] * 2]


async def test_build_plan_survives_model_recovery_without_replaying_completed_target() -> None:
    from a13n_harness import ModelRecoveryPolicy

    effects: list[str] = []
    requests = 0

    def write() -> str:
        effects.append("written")
        return "saved"

    source = Capability(toolsets=[FunctionToolset([write])])

    async def model(messages, info):
        nonlocal requests
        requests += 1
        assert {tool.name for tool in info.function_tools} == {"search_proxy_tools", "call_proxy_tool"}
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="call_proxy_tool",
                    json_args='{"group":"storage","tool":"write","arguments":{}}',
                    tool_call_id="call-write",
                )
            }
        elif requests == 2:
            yield "partial answer"
            raise RuntimeError("stream disconnected")
        else:
            assert any(part.content == "saved" for part in _returns(messages))
            yield "recovered"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(source,),
        tool_proxy=ToolProxyPlan(groups={"storage": ToolProxySelection("Storage tools", capabilities=(source,))}),
        model_recovery=ModelRecoveryPolicy(
            enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
        ),
    )
    result = await executable.run("test", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "recovered"
    assert effects == ["written"]
    assert requests == 3
