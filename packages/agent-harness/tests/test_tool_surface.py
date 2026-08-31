from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from a13n_harness import (
    DefinitionError,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_harness.toolsets import (
    CodeActPolicyToolset,
    CodeActToolPolicy,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability, CapabilityOrdering
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
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


def _tool(name: str, tool_id: str, *, superseded_by: frozenset[str] = frozenset()) -> HarnessTool:
    def implementation() -> str:
        return name

    return HarnessTool(
        implementation,
        name=name,
        description=f"Invoke {name}.",
        harness_metadata=_metadata(tool_id, superseded_by=superseded_by),
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
        nonlocal runner_description
        del messages
        runner_description = next(tool.description or "" for tool in info.function_tools if tool.name == "run_code")
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
    assert "preferred" in runner_description
    assert "fallback" not in runner_description


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
