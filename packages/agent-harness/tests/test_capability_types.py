from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
from a13n_harness import (
    AgentContext,
    DefinitionError,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.capability_types import (
    CapabilityTypeCatalog,
    CapabilityTypeRegistration,
)
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability, WebSearch
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


@dataclass
class _DeclaredCapability(AbstractCapability[AgentContext]):
    prefix: str = "default"
    id: str | None = "declared-feature"

    @classmethod
    def get_serialization_name(cls) -> str:
        return "declared_feature"


@dataclass
class _NativeNameCollision(AbstractCapability[AgentContext]):
    @classmethod
    def get_serialization_name(cls) -> str:
        return "WebSearch"


@dataclass
class _HarnessNameCollision(AbstractCapability[AgentContext]):
    @classmethod
    def get_serialization_name(cls) -> str:
        return "InvocationPolicyCapability"


@dataclass
class _InvalidRunReplacementId(AbstractCapability[AgentContext]):
    id: str | None = "valid.id"

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        del ctx
        return Capability(id="invalid:runtime")


def _catalog() -> CapabilityTypeCatalog:
    return CapabilityTypeCatalog(
        (
            CapabilityTypeRegistration(
                serialization_name="declared_feature",
                capability_type=_DeclaredCapability,
            ),
        )
    )


async def test_builder_uses_only_its_exact_custom_capability_type_catalog() -> None:
    spec = AgentSpec(
        model="logical:test",
        capabilities=[{"name": "declared_feature", "arguments": {"prefix": "trusted"}}],
    )

    with pytest.raises(DefinitionError) as missing_catalog:
        HarnessBuilder().build(spec, output_type=str)
    assert missing_catalog.value.code == "agent_build_failed"

    executable = HarnessBuilder(capability_type_catalog=_catalog()).build(spec, output_type=str)
    leaves: list[AbstractCapability[AgentContext]] = []
    executable._agent.root_capability.apply(leaves.append)
    declared = [capability for capability in leaves if isinstance(capability, _DeclaredCapability)]

    assert len(declared) == 1
    assert declared[0].prefix == "trusted"


async def test_capability_type_catalog_rejects_name_mismatch_and_native_collision() -> None:
    with pytest.raises(DefinitionError) as mismatch:
        CapabilityTypeRegistration(
            serialization_name="wrong_name",
            capability_type=_DeclaredCapability,
        )
    assert mismatch.value.code == "capability_type_name_mismatch"

    with pytest.raises(DefinitionError) as native_collision:
        CapabilityTypeCatalog.from_types((_NativeNameCollision,))
    assert native_collision.value.code == "capability_type_catalog_invalid"

    with pytest.raises(DefinitionError) as harness_collision:
        CapabilityTypeCatalog.from_types((_HarnessNameCollision,))
    assert harness_collision.value.code == "capability_type_catalog_invalid"

    with pytest.raises(DefinitionError) as redundant_native:
        CapabilityTypeCatalog.from_types((WebSearch,))
    assert redundant_native.value.code == "capability_type_catalog_invalid"


async def test_capability_ids_allow_dots_but_not_colons() -> None:
    HarnessBuilder().build(
        AgentSpec(model="logical:test"),
        output_type=str,
        capabilities=(Capability(id="valid.id"),),
    )

    with pytest.raises(DefinitionError) as error:
        HarnessBuilder().build(
            AgentSpec(model="logical:test"),
            output_type=str,
            capabilities=(Capability(id="invalid:id"),),
        )

    assert error.value.code == "capability_id_invalid"


async def test_run_replacement_capability_ids_must_not_contain_colons() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(lambda messages, info: "done"),
        capabilities=(_InvalidRunReplacementId(),),
    )

    with pytest.raises(DefinitionError) as error:
        await executable.run("test", bindings=RunBindings.embedded())

    assert error.value.code == "capability_id_invalid"


async def test_bare_capability_functions_are_rejected_from_definition_and_run_sources() -> None:
    async def dynamic(ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        del ctx
        return Capability(id="dynamic")

    with pytest.raises(DefinitionError) as definition_error:
        HarnessBuilder().build(
            AgentSpec(model="logical:test"),
            output_type=str,
            capabilities=cast(Any, (dynamic,)),
        )
    assert definition_error.value.code == "capability_type_invalid"

    executable = HarnessBuilder().build(AgentSpec(model="logical:test"), output_type=str)
    with pytest.raises(DefinitionError) as run_error:
        executable.stream(
            "run",
            bindings=RunBindings.embedded(capabilities=cast(Any, (dynamic,))),
        )
    assert run_error.value.code == "capability_type_invalid"


async def test_agent_spec_tool_timeout_does_not_apply_to_capability_owned_tools() -> None:
    async def slow_tool() -> str:
        await asyncio.sleep(0.02)
        return "slow result"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if len(messages) == 1:
            yield {
                0: DeltaToolCall(
                    name="slow_tool",
                    json_args="{}",
                    tool_call_id="tool-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(tool_timeout=0.001),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[slow_tool], id="slow-feature"),),
    )

    result = await executable.run("go", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"


async def test_capability_owned_toolset_is_exposed_and_dispatched() -> None:
    toolset_ids: list[str | None] = []

    def double(value: int) -> int:
        return value * 2

    @dataclass
    class CaptureToolset(AbstractCapability[AgentContext]):
        async def before_model_request(self, ctx: RunContext[AgentContext], request_context: Any) -> Any:
            toolset_ids.append(ctx.tools["double"].toolset_id)
            return request_context

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if len(messages) == 1:
            yield {
                0: DeltaToolCall(
                    name="double",
                    json_args='{"value": 4}',
                    tool_call_id="tool-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Capability(
                toolsets=[FunctionToolset([double], id="math-tools")],
                id="math-feature",
            ),
            CaptureToolset(id="capture"),
        ),
    )

    result = await executable.run("go", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert toolset_ids
    assert all(toolset_id is not None and "math-tools" in toolset_id for toolset_id in toolset_ids)
