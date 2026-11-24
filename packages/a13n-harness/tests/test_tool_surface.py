from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from a13n_harness import (
    AgentDefinition,
    DefinitionError,
    HarnessBuilder,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.capabilities import CodeActCapability, SubagentCapability, ToolSearchCapability, ToolSearchGroup
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    ToolOutputPolicy,
    ToolPermissions,
    ToolPermissionsCapability,
)
from a13n_harness.tools.surface import UNATTENDED_TOOL_KEY
from a13n_harness.toolsets import (
    CodeActPolicyToolset,
    CodeActToolPolicy,
)
from pydantic_ai import CallDeferred, Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability, CapabilityOrdering, NativeTool
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset, WrapperToolset

pytestmark = pytest.mark.anyio


@dataclass
class _PassThroughToolset(WrapperToolset[Any]):
    pass


@dataclass
class _OutsideCodeActWrapper(AbstractCapability[Any]):
    id: str | None = "outside-codeact-wrapper"

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(CodeActCapability,))

    def get_wrapper_toolset(self, toolset: AbstractToolset[Any]) -> AbstractToolset[Any]:
        return _PassThroughToolset(toolset)


@dataclass
class _LateOutsideCodeActWrapper(AbstractCapability[Any]):
    id: str | None = "late-outside-codeact-wrapper"

    async def for_run(self, ctx: Any) -> AbstractCapability[Any]:
        del ctx
        return _OutsideCodeActWrapper(id=self.id)


def _metadata(tool_id: str, *, superseded_by: frozenset[str] = frozenset()) -> HarnessToolMetadata:
    return HarnessToolMetadata(
        tool_id=tool_id,
        effects=frozenset({"read"}),
        credential_audiences=(),
        idempotency="read_only",
        output_policy=ToolOutputPolicy(max_inline_bytes=512, max_output_bytes=1024),
        superseded_by_tool_ids=superseded_by,
    )


def _tool(
    name: str, tool_id: str, *, superseded_by: frozenset[str] = frozenset(), unattended: bool = False
) -> HarnessTool:
    def implementation() -> str:
        return name

    return HarnessTool(
        implementation,
        name=name,
        description=f"Invoke {name}.",
        harness_metadata=_metadata(tool_id, superseded_by=superseded_by),
        metadata={UNATTENDED_TOOL_KEY: True} if unattended else None,
    )


async def test_complete_candidate_surface_resolves_exact_managed_tool_ids() -> None:
    observed_names: set[str] = set()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed_names.update(tool.name for tool in info.function_tools)
        yield "done"

    tools = FunctionToolset(
        [
            _tool("preferred", "tools.preferred"),
            _tool("fallback", "tools.fallback", superseded_by=frozenset({"tools.preferred"})),
            _tool("unknown_target", "tools.unknown", superseded_by=frozenset({"tools.absent"})),
        ],
        id="surface-test-tools",
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(Capability(toolsets=[tools], id="surface-test"),),
    )

    result = await executable.run("inspect", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert observed_names == {"preferred", "unknown_target"}


async def test_present_supersession_cycle_fails_before_model_exposure() -> None:
    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        raise AssertionError("model must not receive a cyclic tool surface")
        yield "unreachable"

    tools = FunctionToolset(
        [
            _tool("first", "tools.first", superseded_by=frozenset({"tools.second"})),
            _tool("second", "tools.second", superseded_by=frozenset({"tools.first"})),
        ],
        id="cycle-test-tools",
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(Capability(toolsets=[tools], id="cycle-test"),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run("inspect", bindings=RunBindings.embedded())

    assert exc_info.value.code == "tool_supersession_cycle"


async def test_codeact_catalog_uses_only_the_effective_surface() -> None:
    runner_description = ""
    catalog = ""
    policy_toolset = CodeActPolicyToolset(
        wrapped=FunctionToolset(
            [
                _tool("preferred", "tools.preferred"),
                _tool("fallback", "tools.fallback", superseded_by=frozenset({"tools.preferred"})),
            ],
            id="codeact-surface-tools",
        ),
        policy=CodeActToolPolicy(default=True),
    )

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal runner_description, catalog
        del messages
        runner_description = next(tool.description or "" for tool in info.function_tools if tool.name == "run_code")
        catalog = info.instructions or ""
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(toolsets=[policy_toolset], id="codeact-surface-test"),
            CodeActCapability(),
        ),
    )

    result = await executable.run("inspect", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert "`preferred`" in catalog
    assert "`fallback`" not in catalog
    assert "`preferred`" not in runner_description


async def test_wrapper_cannot_be_ordered_outside_the_mandatory_surface() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(lambda messages, info: "done"),
            capabilities=(_OutsideCodeActWrapper(), CodeActCapability()),
        )

    assert exc_info.value.code == "tool_surface_order_invalid"


async def test_run_replacement_cannot_wrap_outside_the_mandatory_surface() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(lambda messages, info: "done"),
        capabilities=(_LateOutsideCodeActWrapper(), CodeActCapability()),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run("inspect", bindings=RunBindings.embedded())

    assert exc_info.value.code == "tool_surface_order_invalid"


@pytest.mark.parametrize("codeact", [False, True])
async def test_unattended_tool_surface_admits_only_declared_noninteractive_tools(codeact: bool) -> None:
    tools = FunctionToolset(
        [
            _tool("declared", "tools.declared", unattended=True),
            _tool("needs_permission", "tools.approval", unattended=True),
            _tool("undeclared", "tools.undeclared"),
        ],
        id="test-tools",
    )
    observed: set[str] = set()
    catalog = ""

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal catalog
        observed.update(tool.name for tool in info.function_tools)
        catalog = info.instructions or ""
        yield "done"

    toolset = CodeActPolicyToolset(wrapped=tools, policy=CodeActToolPolicy(default=True)) if codeact else tools
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(toolsets=[toolset], id="test-tools"),
            ToolPermissionsCapability(ToolPermissions(rules={"tools.approval": "ask"})),
            *((CodeActCapability(),) if codeact else ()),
        ),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded(unattended=True))
    assert result.output_or_raise() == "done"
    if codeact:
        assert "run_code" in observed
        assert "`declared`" in catalog
        assert "`needs_permission`" not in catalog and "`undeclared`" not in catalog
    else:
        assert observed == {"declared"}


@pytest.mark.parametrize("codeact", [False, True])
async def test_unattended_proxy_directory_uses_same_admitted_surface(codeact: bool) -> None:
    tools = FunctionToolset(
        [
            _tool("declared", "test.declared", unattended=True),
            _tool("must_ask", "test.ask", unattended=True),
            _tool("must_deny", "test.deny", unattended=True),
            _tool("undeclared", "test.undeclared"),
        ],
        id="search-test",
    )

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        assert not any(isinstance(tool, WebSearchTool) for tool in info.model_request_parameters.native_tools)
        result = next(
            (part.content for message in messages for part in message.parts if isinstance(part, ToolReturnPart)), None
        )
        if result is None:
            assert "list_tools" in {tool.name for tool in info.function_tools}
            yield {0: DeltaToolCall(name="list_tools", json_args="{}")}
        else:
            assert "declared" in str(result) and "undeclared" not in str(result)
            assert "must_ask" not in str(result) and "must_deny" not in str(result)
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(
            ToolSearchCapability(groups={"fixtures": ToolSearchGroup(tools, "Fixture tools")}),
            ToolPermissionsCapability(ToolPermissions(rules={"test.ask": "ask", "test.deny": "deny"})),
            NativeTool(WebSearchTool()),
            *((CodeActCapability(),) if codeact else ()),
        ),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded(unattended=True))
    assert result.output_or_raise() == "done"


async def test_unattended_inline_child_filters_tools_and_denies_unexpected_deferral() -> None:
    def defer() -> str:
        raise CallDeferred()

    async def child_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        assert {tool.name for tool in info.function_tools} == {"declared", "defer"}
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returned:
            yield {0: DeltaToolCall(name="defer", json_args="{}")}
        else:
            assert "denied in unattended mode" in str(returned[-1].content)
            yield "child continued"

    async def parent_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returned:
            assert "delegate" in {tool.name for tool in info.function_tools}
            yield {0: DeltaToolCall(name="delegate", json_args='{"subagent":"child","prompt":"inspect"}')}
        else:
            assert "child continued" in str(returned[-1].content)
            yield "parent continued"

    tools = FunctionToolset(
        [
            _tool("declared", "child.declared", unattended=True),
            _tool("undeclared", "child.undeclared"),
            Tool(defer, metadata={UNATTENDED_TOOL_KEY: True}),
        ],
        id="child-tools",
    )
    child = AgentDefinition(
        agent=AgentSpec(),
        definition_id="surface-child",
        output_type=str,
        model=FunctionModel(stream_function=child_model),
        capabilities=(Capability(toolsets=[tools]),),
    )
    parent = AgentDefinition(
        agent=AgentSpec(),
        definition_id="surface-parent",
        output_type=str,
        model=FunctionModel(stream_function=parent_model),
        capabilities=(SubagentCapability(),),
        subagents=(SubagentDefinition(name="child", description="Inspect tool exposure", agent=child),),
    )
    result = await HarnessBuilder().build(parent).run("inspect", bindings=RunBindings.embedded(unattended=True))
    assert result.output_or_raise() == "parent continued"
