from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from a13n_harness import AbstractHarnessPlugin, AgentContext, AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import (
    CodeActCapability,
    ProxyToolSearchConfig,
    ToolSearchCapability,
    ToolSearchGroup,
    ToolSearchPlan,
    ToolSearchSelection,
)
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai import RunContext, Tool, ToolDefinition, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, Capability, ToolSearch
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.profiles import ModelProfile
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
    config: ProxyToolSearchConfig | None = None,
) -> ToolSearchCapability:
    source = FunctionToolset(list(tools), instructions=instructions)
    return ToolSearchCapability(
        groups={
            group: ToolSearchGroup(
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
    tool_search: ToolSearchPlan | None = None,
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
        tool_search=tool_search,
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
        assert set(definitions) == {"discover_tools", "describe_tool", "call_tool", "list_tools", "list_tool_groups"}
        assert "enum" not in definitions["call_tool"].parameters_json_schema["properties"]["group"]
        assert "crm operations" not in definitions["discover_tools"].description
        assert "Double a customer" not in definitions["discover_tools"].description
        assert "<tool-name>tool_search</tool-name>" in info.instructions
        assert "Use exact customer IDs." not in info.instructions

    result, returns = await _run(
        (_group(double), Hooks()),
        [
            ("discover_tools", {"query": "double"}),
            ("call_tool", {"group": "crm", "tool": "double", "arguments": {"value": "21"}}),
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
    assert validated == ["discover_tools", "call_tool", "crm__double"]
    assert executed == validated


async def test_groups_support_same_local_name_and_configurable_control_names() -> None:
    def lookup() -> str:
        return "crm"

    def other() -> str:
        return "docs"

    config = ProxyToolSearchConfig(search_name="find_ops", call_name="invoke_op", max_results=1)
    result, returns = await _run(
        (
            ToolSearchCapability(
                groups={
                    "crm": ToolSearchGroup(FunctionToolset([lookup], id="crm"), "Customer operations"),
                    "docs": ToolSearchGroup(
                        FunctionToolset([Tool(other, name="lookup")], id="docs"), "Document operations"
                    ),
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
        catalog = info.instructions or ""
        assert "`discover_tools`" in catalog
        assert "`call_tool`" in catalog
        assert "crm__double" not in catalog
        assert "`double`" not in catalog
        assert "`discover_tools`" not in runner.description

    source = """found = await discover_tools(query='double', group='crm')
match = found['tools'][0]
await call_tool(group=match['group'], tool=match['tool'], arguments={'value': 21})"""
    capability = Capability(toolsets=[CodeActPolicyToolset(FunctionToolset([double]), CodeActToolPolicy(default=True))])
    result, returns = await _run(
        (CodeActCapability(), capability if use_plan else _group(double)),
        [("run_code", {"code": source})],
        inspect=inspect,
        tool_search=ToolSearchPlan(groups={"crm": ToolSearchSelection("CRM operations", capabilities=(capability,))})
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
        [("run_code", {"code": "await call_tool(group='crm', tool='forbidden', arguments={})"})],
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
        [("call_tool", {"group": "crm", "tool": "typed", "arguments": {"value": "invalid"}})],
    )
    assert result.output_or_raise() == "done"
    assert calls == []


async def test_proxy_composes_with_native_tool_search() -> None:
    def deferred() -> str:
        return "deferred"

    def normal() -> str:
        return "normal"

    def inspect(_step: int, info: AgentInfo) -> None:
        assert {"search_tools", "discover_tools", "call_tool"} <= {tool.name for tool in info.function_tools}

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
        (ToolSearchCapability(groups={"identity": ToolSearchGroup(Source(id="fresh"), "Current run")}),),
        [("call_tool", {"group": "identity", "tool": "identify", "arguments": {}})],
    )
    assert result.output_or_raise() == "done"
    assert isinstance(returns[-1].content, str)


async def test_search_budget_never_returns_partial_schema() -> None:
    def giant(value: str) -> str:
        return value

    result, returns = await _run(
        (_group(Tool(giant, description="x" * 2000), config=ProxyToolSearchConfig(max_search_bytes=1024)),),
        [("discover_tools", {"query": "giant", "detail": "full"})],
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
    call = ("call_tool", {"group": "crm", "tool": "write", "arguments": {"value": "7"}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await call_tool(group='crm', tool='write', arguments={'value': '7'})"})
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
    call = ("call_tool", {"group": "crm", "tool": "work", "arguments": {}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await call_tool(group='crm', tool='work', arguments={})"})
    result, _ = await _run(capabilities, [call], usage_limits=UsageLimits(tool_calls_limit=2))
    assert result.output_or_raise() == "done"
    assert effects == [True]
    assert usage == [0, 2]
    effects.clear()
    # The parent + target cannot fit; reject before the target has any effect.
    result, _ = await _run(capabilities, [call], usage_limits=UsageLimits(tool_calls_limit=1))
    assert effects == []


@pytest.mark.parametrize("resolution", ["approve", "deny", "unresolved"])
async def test_proxy_preserves_inline_approval_and_suspends_unresolved_target(resolution: str) -> None:
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
    result, returns = await _run(caps, [("call_tool", {"group": "crm", "tool": "change", "arguments": {"value": 3}})])
    if resolution == "unresolved":
        assert result.status == "suspended" and result.deferred is not None
        assert result.deferred.approvals[0].target_call.tool_name == "crm__change"
        assert effects == [] and returns == []
        return
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
        assert ("discover_tools" in names) == (step < 2)

    result, returns = await _run(
        (_group(Tool(lookup, prepare=prepare)),),
        [("discover_tools", {"query": "lookup"}), ("discover_tools", {"query": "lookup"})],
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
        [("discover_tools", {"query": ""}), ("call_tool", {"group": "crm", "tool": "old", "arguments": {}})],
    )
    assert result.output_or_raise() == "done"
    assert [tool["tool"] for tool in returns[0].content["tools"]] == ["new"]


@pytest.mark.parametrize("codeact", [False, True])
async def test_proxy_retains_multimodal_tool_return_content(codeact: bool) -> None:
    def rich() -> ToolReturn:
        return ToolReturn(return_value={"value": 1}, content="supplemental evidence", metadata={"source": "test"})

    caps = (_group(rich),)
    call = ("call_tool", {"group": "crm", "tool": "rich", "arguments": {}})
    if codeact:
        caps += (CodeActCapability(),)
        call = ("run_code", {"code": "await call_tool(group='crm', tool='rich', arguments={})"})
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
                    "code": "import asyncio\nawait asyncio.gather(call_tool(group='crm', tool='work', arguments={'value': 1}), call_tool(group='crm', tool='work', arguments={'value': 2}))"
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

    call = ("call_tool", {"group": "crm", "tool": "retrying", "arguments": {}})
    result, returns = await _run(
        (_group(Tool(retrying, max_retries=3)),),
        [call, call, call],
    )
    assert result.output_or_raise() == "done"
    assert retries == [0, 1, 2]
    assert returns[-1].content == "recovered"
    assert returns[-1].tool_call_id == "call-2"


async def test_final_directory_rejects_deferral_added_by_outer_capability() -> None:
    from a13n_harness.capabilities.tool_search import _ToolSearchSurfaceCapability
    from a13n_harness.toolsets.tool_search import _GroupedToolset, _ToolGroup

    def hidden() -> str:
        return "not loaded"

    source = Capability(
        toolsets=[
            _GroupedToolset(FunctionToolset([hidden]), {None: _ToolGroup("late", "Deferred source")}, "late-source")
        ],
        id="late-source",
        defer_loading=True,
    )
    result, returns = await _run(
        (_ToolSearchSurfaceCapability(ProxyToolSearchConfig(), frozenset({"late"})), source), []
    )
    assert result.status == "failed"
    assert returns == []


async def test_outer_prefix_preserves_group_identity_source_guidance_and_codeact_policy() -> None:
    def value() -> int:
        return 1

    source = CodeActPolicyToolset(
        FunctionToolset([value], instructions="Source-specific requirement"), CodeActToolPolicy(default=True)
    ).prefixed("outer")
    result, returns = await _run(
        (ToolSearchCapability(groups={"crm": ToolSearchGroup(source, "Customer data")}),),
        [
            ("discover_tools", {"query": "value", "detail": "full"}),
            ("call_tool", {"group": "crm", "tool": "outer_value", "arguments": {}}),
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
        [("call_tool", {"group": "crm", "tool": "mutate", "arguments": {}})],
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

    # 200 tools exceed the largest proxy page (max_results <= 100); no proxy bound scales with the group size.
    for count in (10, 200):
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
                config=ProxyToolSearchConfig(max_results=2),
            ),
        )
        if codeact:
            caps += (CodeActCapability(),)
        result, returns = await _run(
            caps,
            [
                ("discover_tools", {"query": "", "limit": 2}),
                ("discover_tools", {"query": "", "limit": 2, "offset": count - 1}),
                (
                    "call_tool",
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


async def test_proxy_run_program_reads_source_and_dispatches_through_current_manager(tmp_path: Path) -> None:
    from a13n_harness.environment import EnvironmentAction, EnvironmentMount, EnvironmentPermissionSet
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.providers.environment.direct_local.configuration import (
        DirectLocalEnvironmentConfiguration,
        DirectLocalRootConfiguration,
    )

    from .environment_helpers import DirectLocalSource

    (tmp_path / "job.codeact.py").write_text(
        "async def main(inputs):\n"
        "    found = await discover_tools(query='double', group='crm')\n"
        "    match = found['tools'][0]\n"
        "    return await call_tool(group=match['group'], tool=match['tool'], arguments={'value': inputs['value']})\n",
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
        (CodeActCapability(), _group(double)),
        [("run_program", {"path": "/workspace/job.codeact.py", "inputs": {"value": 21}})],
        bindings=RunBindings.embedded(environment=environment),
        usage_limits=UsageLimits(tool_calls_limit=3),
    )
    assert result.output_or_raise() == "done"
    assert returns[-1].content == 42
    assert calls == [21]
    assert result.usage.tool_calls == 3


@pytest.mark.parametrize("reference_root", [None, "/release-guidance"])
async def test_proxy_instruction_switch_and_custom_names_are_consistent(reference_root) -> None:
    def value() -> int:
        return 1

    config = ProxyToolSearchConfig(
        search_name="find_ops",
        describe_name="describe_op",
        call_name="invoke_op",
        groups_name="domains",
        list_name="list_ops",
    )
    for enabled in (True, False):

        def inspect(_step: int, info: AgentInfo, enabled: bool = enabled) -> None:
            instructions = info.instructions or ""
            assert ("<tool-name>tool_search</tool-name>" in instructions) is enabled
            assert "discover_tools" not in instructions
            assert "call_tool" not in instructions
            if enabled:
                assert "find_ops" in instructions
                assert "invoke_op" in instructions
                assert "describe_op" in instructions and "domains" in instructions and "list_ops" in instructions
                assert ("/release-guidance/harness/tool_search.md" in instructions) is (reference_root is not None)
                assert ("Exact local or" in instructions) is (reference_root is None)

        result, returns = await _run(
            (_group(value, config=config),),
            [("find_ops", {"query": "", "detail": "full"})],
            inspect=inspect,
            toolset_instructions=enabled,
            bindings=RunBindings.embedded(instruction_reference_root=reference_root),
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
        (ToolSearchCapability(groups={"combined": ToolSearchGroup(source, "Combined source")}),),
        [
            ("discover_tools", {"query": ""}),
            ("call_tool", {"group": "combined", "tool": "second", "arguments": {}}),
        ],
    )
    assert result.output_or_raise() == "done"
    assert bound == ["first", "second"]
    assert [tool["tool"] for tool in returns[0].content["tools"]] == ["first", "second"]
    assert returns[1].content == "second"
    assert len(executed) == len(set(executed)) == 6


def test_proxy_configuration_errors_explain_how_to_fix_the_source() -> None:
    from a13n_harness.tools.tool_search import validate_proxy_target
    from pydantic_ai.exceptions import UserError

    with pytest.raises(ValueError, match="start with a letter"):
        ProxyToolSearchConfig(search_name="bad name")
    with pytest.raises(ValueError, match=r"ToolSearchGroup\.description must be non-blank"):
        ToolSearchCapability(groups={"crm": ToolSearchGroup(FunctionToolset(), " ")})
    with pytest.raises(UserError, match="remove defer_loading=True"):
        validate_proxy_target(ToolDefinition(name="lookup", defer_loading=True))
    with pytest.raises(UserError, match="native=False, local=True"):
        validate_proxy_target(ToolDefinition(name="lookup", unless_native=True))
    validate_proxy_target(ToolDefinition(name="lookup", kind="external"))
    with pytest.raises(UserError, match="Leave control, output, and provider-native tools directly exposed"):
        validate_proxy_target(ToolDefinition(name="lookup", kind="output"))


async def test_unavailable_proxy_target_tells_agent_to_rediscover_without_executing() -> None:
    from pydantic_ai.messages import ToolReturnPart

    def value() -> int:
        pytest.fail("An unavailable name must never fall back to another tool")

    result, _ = await _run(
        (_group(value),),
        [("call_tool", {"group": "crm", "tool": "missing", "arguments": {}})],
    )
    assert result.output_or_raise() == "done"
    retry = next(
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.outcome == "retried"
    )
    error = json.loads(retry.content)["error"]
    assert (error["source"], error["code"], error["details"]["field"]) == ("system", "tool_unavailable", "tool")
    assert "Rediscover available tools" in error["details"]["hint"]


async def test_empty_groups_leave_direct_tools_unchanged() -> None:
    def status() -> str:
        return "ready"

    def inspect(_step: int, info: AgentInfo) -> None:
        assert [tool.name for tool in info.function_tools] == ["status"]
        assert "<tool-name>tool_search</tool-name>" not in (info.instructions or "")

    result, returns = await _run(
        (ToolSearchCapability(groups={}), Capability(toolsets=[FunctionToolset([status])])),
        [("status", {})],
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    assert returns[0].content == "ready"


async def test_group_mapping_is_consumed_at_construction() -> None:
    def value() -> int:
        return 42

    descriptor = ToolSearchGroup(source=FunctionToolset([value]), description="Customer records")
    assert not isinstance(descriptor, AbstractCapability)
    groups = {"crm": descriptor}
    proxy = ToolSearchCapability(groups=groups)
    groups.clear()
    result, returns = await _run((proxy,), [("call_tool", {"group": "crm", "tool": "value", "arguments": {}})])
    assert result.output_or_raise() == "done"
    assert returns[0].content == 42


def test_group_configuration_requires_resolved_sources_and_descriptors() -> None:
    with pytest.raises(TypeError, match="resolve Host configuration"):
        ToolSearchGroup(source="customer-plugin", description="Customer records")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"groups\['crm'\] must be ToolSearchGroup"):
        ToolSearchCapability(groups={"crm": FunctionToolset()})  # type: ignore[dict-item]


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
                    groups[name] = ToolSearchGroup(source=source, description=self.group_descriptions[name])
                else:
                    direct.append(source)
            if groups:
                direct.append(ToolSearchCapability(groups=groups))
            return tuple(direct)

        async def for_run(self, context: AgentContext):
            log.append("plugin:for_run")
            return self

        async def wrap_run(self, exchange, call_next):
            log.append("plugin:wrap_run")
            return await call_next(exchange)

    target = ("call_tool", {"group": "crm", "tool": "identify", "arguments": {}}) if grouped else ("identify", {})
    if codeact:
        code = "await call_tool(group='crm', tool='identify', arguments={})" if grouped else "await identify()"
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
            catalog = info.instructions or ""
            assert ("`call_tool`" in catalog) is grouped
            assert ("`identify`" in catalog) is not grouped
            assert "`call_tool`" not in runner.description
            assert "`identify`" not in runner.description
        else:
            assert ("identify" in names) is not grouped
            assert ("call_tool" in names) is grouped
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
    proxy = ToolSearchCapability(groups={"restricted": ToolSearchGroup(source, "Restricted source")})

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
    proxy = ToolSearchCapability(groups={"ordered": ToolSearchGroup(source, "Ordered tools")})

    def inspect(_step, info):
        assert "Custom container instructions." in info.instructions

    result, _ = await _run((proxy,), [], inspect=inspect)
    assert result.output_or_raise() == "done"
    assert events == ["outer", "inner"]


@pytest.mark.parametrize("instance_ordering", [False, True])
async def test_build_plan_preserves_cross_group_order_and_plugin_binding(instance_ordering: bool) -> None:
    from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
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
        tool_search=ToolSearchPlan(
            groups={
                "ordered": ToolSearchSelection("Ordered tools", capabilities=(first,), plugins=("ordered-plugin",)),
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

            return (Capability(id=self.name, toolsets=[FunctionToolset([identify], id=self.name)]),)

    def local() -> str:
        return "local"

    source = Capability(id="host-source", toolsets=[FunctionToolset([local])])
    result, returns = await _run(
        (source,),
        [
            ("call_tool", {"group": "first", "tool": "identify", "arguments": {}}),
            ("call_tool", {"group": "second", "tool": "identify", "arguments": {}}),
            ("call_tool", {"group": "first", "tool": "local", "arguments": {}}),
            ("identify", {}),
        ],
        plugins=(Plugin("plugin-one"), Plugin("plugin-two"), Plugin("plugin-direct")),
        tool_search=ToolSearchPlan(
            groups={
                "second": ToolSearchSelection("Second account", plugins=("plugin-two",)),
                "first": ToolSearchSelection(
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
    plan = ToolSearchPlan(
        groups={
            "account": ToolSearchSelection("Account tools", plugins=("plugin-one", "plugin-two")),
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
    selection = ToolSearchSelection("Host tools", capabilities=(source,))
    groups = {"host": selection}
    plan = ToolSearchPlan(groups=groups)
    groups.clear()
    assert "host" in plan.groups
    with pytest.raises(TypeError):
        plan.groups["other"] = selection  # type: ignore[index]
    with pytest.raises(ValueError, match="not selected"):
        plan._compose((), {})
    with pytest.raises(ValueError, match="multiple groups"):
        ToolSearchPlan(groups={"first": selection, "second": selection})._compose((source,), {})
    with pytest.raises(ValueError, match="unavailable plugin"):
        ToolSearchPlan(groups={"host": ToolSearchSelection("Host tools", plugins=("plugin-missing",))})._compose((), {})


@pytest.mark.parametrize("use_plan", [False, True])
async def test_grouped_custom_container_retains_native_binding_hooks_and_state(use_plan: bool) -> None:
    from copy import copy

    from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
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
        assert {tool.name for tool in info.function_tools} == {
            "discover_tools",
            "describe_tool",
            "call_tool",
            "list_tools",
            "list_tool_groups",
        }
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(source,)
        if use_plan
        else (ToolSearchCapability(groups={"group": ToolSearchGroup(source, "Tools")}),),
        tool_search=ToolSearchPlan(groups={"group": ToolSearchSelection("Tools", capabilities=(source,))})
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
        assert {tool.name for tool in info.function_tools} == {
            "discover_tools",
            "describe_tool",
            "call_tool",
            "list_tools",
            "list_tool_groups",
        }
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="call_tool",
                    json_args='{"group":"storage","tool":"write","arguments":{}}',
                    tool_call_id="call-write",
                )
            }
        elif requests == 2:
            yield "partial answer"
            raise ConnectionResetError("stream disconnected")
        else:
            assert any(part.content == "saved" for part in _returns(messages))
            yield "recovered"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(source,),
        tool_search=ToolSearchPlan(groups={"storage": ToolSearchSelection("Storage tools", capabilities=(source,))}),
        model_recovery=ModelRecoveryPolicy(
            enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
        ),
    )
    result = await executable.run("test", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "recovered"
    assert effects == ["written"]
    assert requests == 3


@pytest.mark.parametrize("codeact", [False, True])
@pytest.mark.parametrize("mode", ["deny", "ask"])
async def test_proxy_target_permission_gate_precedes_business_dispatch(codeact: bool, mode: str) -> None:
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

    effects = []

    def forbidden() -> str:
        effects.append(True)
        return "not allowed"

    capabilities = (
        _group(forbidden),
        ToolPermissionsCapability(ToolPermissions(default="allow", rules={"tool/native/forbidden": mode})),
    )
    call = ("call_tool", {"group": "crm", "tool": "forbidden", "arguments": {}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await call_tool(group='crm', tool='forbidden', arguments={})"})
    result, _ = await _run(capabilities, [call])
    assert result.status == ("suspended" if mode == "ask" and not codeact else "completed")
    assert not effects


@pytest.mark.parametrize("codeact", [False, True])
@pytest.mark.parametrize("mode", ["allow", "inherit"])
async def test_proxy_and_codeact_use_capability_approval_override(codeact: bool, mode: str) -> None:
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

    effects = []

    def change(ctx: RunContext[AgentContext]) -> str:
        effects.append(ctx.deps.tool_approval.approved)
        return "changed"

    capabilities = (
        _group(Tool(change, requires_approval=True)),
        ToolPermissionsCapability(ToolPermissions(rules={"tool/native/change": mode})),
    )
    call = ("call_tool", {"group": "crm", "tool": "change", "arguments": {}})
    if codeact:
        capabilities += (CodeActCapability(),)
        call = ("run_code", {"code": "await call_tool(group='crm', tool='change', arguments={})"})
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


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "native", "max_results": 5},
        {"mode": "native", "search_name": "search"},
        {"mode": "proxy", "strategy": "bm25"},
        {"mode": "native", "strategy": "keywords"},
        {"mode": "unknown"},
        {"mode": "proxy", "max_results": True},
    ],
)
def test_search_configuration_rejects_inapplicable_settings(value) -> None:
    from a13n_harness.tools.tool_search import ToolSearchConfig
    from pydantic import TypeAdapter, ValidationError

    with pytest.raises(ValidationError):
        TypeAdapter(ToolSearchConfig).validate_python(value)


@pytest.mark.parametrize("detail", ["summary", "full"])
async def test_list_pages_enumerate_every_tool_without_expanding_model_schema(detail: str) -> None:
    effects = []

    def lookup(value: int) -> int:
        effects.append(value)
        return value

    definitions = []

    def inspect(step: int, info: AgentInfo) -> None:
        # Compare model-facing definitions, excluding private dispatch bindings.
        definitions.append([replace(tool, metadata=None) for tool in info.function_tools])
        assert {tool.name for tool in info.function_tools} == {
            "discover_tools",
            "describe_tool",
            "call_tool",
            "list_tools",
            "list_tool_groups",
        }

    tools = [Tool(lookup, name=f"lookup_{index:02}") for index in range(23)]
    result, returns = await _run(
        (_group(*tools, config=ProxyToolSearchConfig(max_results=5)),),
        [("list_tools", {"detail": detail, "offset": offset}) for offset in range(0, 23, 5)]
        + [("call_tool", {"group": "crm", "tool": "lookup_22", "arguments": {"value": 42}})],
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    assert all(page.content["total"] == 23 for page in returns[:-1])
    assert [page.content["next_offset"] for page in returns[:-1]] == [5, 10, 15, 20, None]
    entries = [entry for page in returns[:-1] for entry in page.content["tools"]]
    assert [entry["tool"] for entry in entries] == [f"lookup_{index:02}" for index in range(23)]
    assert all(("parameters_json_schema" in entry) == (detail == "full") for entry in entries)
    assert all(current == definitions[0] for current in definitions)
    assert effects == [42]


async def test_proxy_schemas_stay_identical_across_different_group_inventories() -> None:
    def lookup() -> str:
        return "found"

    definitions = []

    def inspect(step: int, info: AgentInfo) -> None:
        # Compare model-facing definitions, excluding private dispatch bindings.
        definitions.append([replace(tool, metadata=None) for tool in info.function_tools])

    for group, count in (("alpha", 1), ("beta", 12)):
        result, returns = await _run(
            (_group(*(Tool(lookup, name=f"lookup_{i}") for i in range(count)), group=group),),
            [("list_tool_groups", {})],
            inspect=inspect,
        )
        assert result.output_or_raise() == "done"
        assert returns[0].content["groups"] == [{"group": group, "description": f"{group} operations", "count": count}]
    assert all(current == definitions[0] for current in definitions)


async def test_group_listing_paginates_and_custom_names_work_in_codeact() -> None:
    def lookup() -> str:
        return "found"

    groups = {
        name: ToolSearchGroup(
            CodeActPolicyToolset(FunctionToolset([lookup], id=name), CodeActToolPolicy(default=True)),
            f"{name} operations",
        )
        for name in ("alpha", "beta", "gamma")
    }
    code = """first = await domains()
second = await domains(offset=first['next_offset'])
items = await inventory(group=second['groups'][0]['group'], detail='full')
{'first': first, 'second': second, 'items': items}"""
    result, returns = await _run(
        (
            CodeActCapability(),
            ToolSearchCapability(
                groups=groups,
                config=ProxyToolSearchConfig(
                    groups_name="domains",
                    list_name="inventory",
                    max_results=2,
                ),
            ),
        ),
        [("run_code", {"code": code})],
    )
    assert result.output_or_raise() == "done"
    content = returns[-1].content
    assert content["first"]["total"] == 3
    assert content["first"]["next_offset"] == 2
    assert content["second"]["next_offset"] is None
    assert content["second"]["groups"] == [{"group": "gamma", "description": "gamma operations", "count": 1}]
    assert content["items"]["tools"][0]["tool"] == "lookup"


async def test_disjoint_tool_selections_keep_unselected_tools_direct_and_bind_source_once() -> None:
    bound = []
    invoked = []

    def read_file() -> str:
        invoked.append("files")
        return "file"

    def terminal() -> str:
        invoked.append("terminal")
        return "terminal"

    def computer() -> str:
        invoked.append("computer")
        return "desktop"

    class Source(AbstractCapability):
        id = "mixed"

        async def for_run(self, ctx):
            bound.append(ctx.deps.run_id)
            return self

        def get_toolset(self):
            return FunctionToolset([read_file, terminal, computer], instructions="Keep the source guidance.")

    source = Source()
    plan = ToolSearchPlan(
        groups={
            "desktop": ToolSearchSelection(
                "Computer", capabilities=(source,), tools={"mixed": frozenset({"computer"})}
            ),
            "terminal": ToolSearchSelection(
                "Terminal", capabilities=(source,), tools={"mixed": frozenset({"terminal"})}
            ),
        }
    )

    def inspect(_, info):
        names = {tool.name for tool in info.function_tools}
        assert "read_file" in names
        assert not names.intersection({"computer", "terminal", "desktop__computer", "terminal__terminal"})
        assert "Keep the source guidance." in (info.instructions or "")

    result, _ = await _run(
        (source,),
        [
            ("call_tool", {"group": "desktop", "tool": "computer", "arguments": {}}),
            ("read_file", {}),
            ("call_tool", {"group": "terminal", "tool": "terminal", "arguments": {}}),
        ],
        inspect=inspect,
        tool_search=plan,
    )
    assert result.output_or_raise() == "done"
    assert invoked == ["computer", "files", "terminal"]
    assert len(bound) == 1
    with pytest.raises(ValueError, match="multiple groups"):
        ToolSearchPlan(groups={"one": plan.groups["desktop"], "two": plan.groups["desktop"]})._compose((source,), {})


@pytest.mark.parametrize("names", [("lookup", "items__lookup"), ("items__lookup", "lookup")])
async def test_grouped_names_cannot_shadow_direct_tools_from_the_same_source(names: tuple[str, str]) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import UserError

    def lookup() -> str:
        return "found"

    source = Capability(id="mixed", toolsets=[FunctionToolset([Tool(lookup, name=name) for name in names])])
    plan = ToolSearchPlan(
        groups={
            "items": ToolSearchSelection("Item tools", capabilities=(source,), tools={"mixed": frozenset({"lookup"})})
        }
    )
    agent = Agent(
        FunctionModel(stream_function=lambda messages, info: _text_stream()),
        capabilities=plan._compose((source,), {}),
    )
    with pytest.raises(UserError, match="conflicting tool name 'items__lookup'"):
        await agent.run("test")


@pytest.mark.parametrize(("prior_sequential", "target_sequential"), [(True, False), (False, True), (False, False)])
async def test_proxy_target_keeps_its_runtime_execution_policy(
    monkeypatch, prior_sequential, target_sequential
) -> None:
    from a13n_harness import HarnessState
    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
    from a13n_harness.tool_execution import ToolExecutionRuntime

    runtime = ToolExecutionRuntime()
    state = HarnessState.new()
    entered, finish, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    invoked = []

    async def prior() -> None:
        entered.set()
        await finish.wait()

    runtime.admit(
        prior,
        thread_id=state.thread_id,
        run_id="prior",
        tool_call_id="prior",
        tool_name="prior",
        sequential=prior_sequential,
    )
    original_inline = runtime.inline

    async def inline(operation, *, thread_id, sequential):
        assert sequential == target_sequential
        queued.set()
        return await original_inline(operation, thread_id=thread_id, sequential=sequential)

    monkeypatch.setattr(runtime, "inline", inline)

    def lookup() -> str:
        invoked.append("lookup")
        return "found"

    async def model(messages, info):
        assert next(tool for tool in info.function_tools if tool.name == "call_tool").sequential
        if _returns(messages):
            yield "done"
        else:
            yield {
                0: DeltaToolCall(
                    name="call_tool", json_args=json.dumps({"group": "crm", "tool": "lookup", "arguments": {}})
                )
            }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(ToolExecutionCapability(), _group(Tool(lookup, sequential=target_sequential))),
    )
    await entered.wait()
    pending = asyncio.create_task(
        executable.run("lookup", previous_state=state, bindings=RunBindings.embedded(tool_execution=runtime))
    )
    try:
        await asyncio.wait_for(queued.wait(), 2)
        if prior_sequential or target_sequential:
            assert not invoked and not pending.done()
            finish.set()
        result = await asyncio.wait_for(pending, 2)
        assert result.output_or_raise() == "done"
        assert invoked == ["lookup"]
        assert _returns(result.all_messages())[-1].content == "found"
    finally:
        finish.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await runtime.close()


def _connector_surface(*tools: Any, group: str = "github") -> ToolSearchCapability:
    return ToolSearchCapability(
        groups={
            group: ToolSearchGroup(
                CodeActPolicyToolset(
                    FunctionToolset(list(tools), id="connector-source"), CodeActToolPolicy(default=True)
                ),
                "GitHub operations",
            )
        },
        config=ProxyToolSearchConfig(
            search_name="search_connector_tools",
            describe_name="describe_connector_tool",
            call_name="call_connector_tool",
            list_name="list_connector_tools",
            groups_name="list_connector_groups",
        ),
        surface_id="test.connector-tools",
    )


@pytest.mark.parametrize("use_plan", [False, True])
async def test_disjoint_proxy_surfaces_keep_all_controls_and_guidance_scoped(use_plan: bool) -> None:
    effects = []

    def customer() -> str:
        effects.append("crm")
        return "customer"

    def pull_request() -> str:
        effects.append("github")
        return "pull request"

    capabilities = [_connector_surface(pull_request)]
    plan = None
    if use_plan:
        source = Capability(tools=[customer], id="crm-source")
        capabilities.append(source)
        plan = ToolSearchPlan(groups={"crm": ToolSearchSelection("Customer operations", capabilities=(source,))})
    else:
        capabilities.append(_group(customer))

    def inspect(_step: int, info: AgentInfo) -> None:
        assert {tool.name for tool in info.function_tools} == {
            "discover_tools",
            "describe_tool",
            "call_tool",
            "list_tools",
            "list_tool_groups",
            "search_connector_tools",
            "describe_connector_tool",
            "call_connector_tool",
            "list_connector_tools",
            "list_connector_groups",
        }
        assert "search_connector_tools" in info.instructions
        assert "discover_tools" in info.instructions

    result, returns = await _run(
        tuple(capabilities),
        [
            ("discover_tools", {"query": ""}),
            ("search_connector_tools", {"query": ""}),
            ("list_tool_groups", {}),
            ("list_connector_groups", {}),
            ("list_tools", {}),
            ("list_connector_tools", {}),
            ("describe_tool", {"group": "crm", "tool": "customer"}),
            ("describe_connector_tool", {"group": "github", "tool": "pull_request"}),
            ("call_tool", {"group": "crm", "tool": "customer", "arguments": {}}),
            ("call_connector_tool", {"group": "github", "tool": "pull_request", "arguments": {}}),
        ],
        inspect=inspect,
        tool_search=plan,
    )
    assert result.output_or_raise() == "done"
    assert [entry["group"] for entry in returns[0].content["tools"]] == ["crm"]
    assert [entry["group"] for entry in returns[1].content["tools"]] == ["github"]
    assert [entry["group"] for entry in returns[2].content["groups"]] == ["crm"]
    assert [entry["group"] for entry in returns[3].content["groups"]] == ["github"]
    assert [entry["group"] for entry in returns[4].content["tools"]] == ["crm"]
    assert [entry["group"] for entry in returns[5].content["tools"]] == ["github"]
    assert returns[6].content["tool"]["tool"] == "customer"
    assert returns[7].content["tool"]["tool"] == "pull_request"
    assert [part.content for part in returns[-2:]] == ["customer", "pull request"]
    assert effects == ["crm", "github"]


@pytest.mark.parametrize("control", ["discover_tools", "list_tools", "describe_tool", "call_tool"])
async def test_proxy_controls_cannot_target_another_surface_group(control: str) -> None:
    effects = []

    def customer() -> str:
        return "customer"

    def pull_request() -> str:
        effects.append("write")
        return "pull request"

    arguments: dict[str, Any] = {"group": "github"}
    if control == "discover_tools":
        arguments["query"] = ""
    if control in {"describe_tool", "call_tool"}:
        arguments["tool"] = "pull_request"
    if control == "call_tool":
        arguments["arguments"] = {}
    result, returns = await _run((_group(customer), _connector_surface(pull_request)), [(control, arguments)])
    assert result.output_or_raise() == "done"
    assert not effects
    assert len(returns) == 1
    retry = returns[0]
    assert retry.outcome == "retried"
    assert retry.tool_name == control
    assert "pull_request" not in str(retry.content)
    assert "GitHub operations" not in str(retry.content)


async def test_codeact_uses_the_selected_proxy_controls_native_forwarder() -> None:
    effects = []

    def customer() -> str:
        return "customer"

    def pull_request() -> str:
        effects.append("github")
        return "pull request"

    result, returns = await _run(
        (_group(customer), _connector_surface(pull_request), CodeActCapability()),
        [
            ("run_code", {"code": "await call_tool(group='github', tool='pull_request', arguments={})"}),
            ("run_code", {"code": "await call_connector_tool(group='github', tool='pull_request', arguments={})"}),
        ],
    )
    assert result.output_or_raise() == "done"
    assert effects == ["github"]
    failure = json.loads(returns[0].content)
    assert failure["codeact"]["status"] == "failed"
    assert failure["error"]["nested_errors"][0]["type"] == "ModelRetry"
    assert "pull request" in str(returns[-1].content)


async def test_empty_proxy_surface_does_not_claim_another_surfaces_members() -> None:
    def customer() -> str:
        return "customer"

    def inspect(_step: int, info: AgentInfo) -> None:
        assert {tool.name for tool in info.function_tools} == {
            "discover_tools",
            "describe_tool",
            "call_tool",
            "list_tools",
            "list_tool_groups",
        }

    result, _ = await _run(
        (_group(customer), ToolSearchCapability(groups={}, surface_id="test.empty-surface")),
        [],
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"


async def test_overlapping_surface_groups_fail_instead_of_sharing_members() -> None:
    def customer() -> str:
        return "customer"

    def pull_request() -> str:
        return "pull request"

    result, returns = await _run(
        (_group(customer), _connector_surface(pull_request, group="crm")), [("list_tool_groups", {})]
    )
    assert result.status == "failed"
    assert returns == []


@pytest.mark.parametrize("binding_change", ["drop", "rename"])
async def test_scoped_proxy_surface_cannot_disappear_or_change_identity_during_run_binding(
    monkeypatch, binding_change: str
) -> None:
    from a13n_harness.capabilities.tool_search import _ToolSearchSurfaceCapability
    from a13n_harness.errors import DefinitionError

    async def bind(self, ctx):
        if self.id != "test.connector-tools":
            return self
        return Capability() if binding_change == "drop" else replace(self, id="test.unrecorded-surface")

    monkeypatch.setattr(_ToolSearchSurfaceCapability, "for_run", bind)

    def pull_request() -> str:
        return "pull request"

    with pytest.raises(DefinitionError) as error:
        await _run((_connector_surface(pull_request),), [("list_connector_groups", {})])
    assert error.value.code == "capability_scope_invalid"


@pytest.mark.parametrize("stage", ["validator", "execution"])
@pytest.mark.parametrize("page", [None, "files"])
async def test_proxy_retry_guidance_names_target_page_exactly_once(stage, page) -> None:
    from pydantic_ai import ModelRetry

    def validate(ctx, value: int):
        if stage == "validator":
            raise ModelRetry("Use another value")

    def lookup(value: int):
        raise ModelRetry("Use another value")

    result, returns = await _run(
        (
            _group(
                Tool(lookup, args_validator=validate, max_retries=1, metadata={"a13n.guidance": page} if page else {})
            ),
        ),
        [("call_tool", {"group": "crm", "tool": "lookup", "arguments": {"value": 1}})],
        bindings=RunBindings.embedded(instruction_reference_root="/release-guidance"),
    )
    assert result.output_or_raise() == "done"
    part = returns[0]
    assert part.outcome == "retried"
    assert part.tool_name == "call_tool" and part.tool_call_id == "call-0"
    expected = "Use another value"
    if page:
        expected += "\nGuidance: `/release-guidance/harness/files.md`. Read with view before deciding whether to retry."
    assert part.content == expected
    assert part.retry.attempt == 1 and part.retry.max_retries == 1


async def test_real_search_control_retry_points_to_its_own_guidance() -> None:
    def lookup() -> int:
        return 42

    result, returns = await _run(
        (_group(lookup),),
        [("discover_tools", {"query": "!!!"})],
        bindings=RunBindings.embedded(instruction_reference_root="/release-guidance"),
    )
    assert result.output_or_raise() == "done"
    part = returns[0]
    assert part.outcome == "retried" and part.tool_name == "discover_tools"
    assert part.content == (
        "Supply operation keywords or an exact tool name; use an empty query to browse.\n"
        "Guidance: `/release-guidance/harness/tool_search.md`. Read with view before deciding whether to retry."
    )
    assert part.retry.attempt == 1 and part.retry.max_retries == 5
