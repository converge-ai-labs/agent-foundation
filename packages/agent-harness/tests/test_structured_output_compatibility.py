from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.models.structured_output import StructuredOutputAutoToolChoiceCapability
from pydantic import BaseModel, Field
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models._tool_choice import resolve_tool_choice
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import Tool

pytestmark = pytest.mark.anyio


class _Score(BaseModel):
    value: int = Field(ge=3)


def _compatibility_capabilities(executable: Any) -> list[StructuredOutputAutoToolChoiceCapability]:
    leaves: list[object] = []
    executable._agent.root_capability.apply(leaves.append)
    return [capability for capability in leaves if isinstance(capability, StructuredOutputAutoToolChoiceCapability)]


async def test_structured_output_uses_provider_auto_choice_and_keeps_local_validation() -> None:
    calls: list[AgentInfo] = []

    async def model_stream(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[DeltaToolCalls]:
        calls.append(info)
        value = 2 if len(calls) == 1 else 3
        yield {
            0: DeltaToolCall(
                name=info.output_tools[0].name,
                json_args=f'{{"value": {value}}}',
                tool_call_id=f"output-{len(calls)}",
            )
        }

    def lookup() -> str:
        return "available"

    executable = HarnessBuilder().build(
        AgentSpec(model_settings={"tool_choice": "none"}, retries={"output": 1}),
        output_type=_Score,
        model=FunctionModel(stream_function=model_stream),
        capabilities=(Capability(id="test.lookup", tools=[Tool(lookup)]),),
    )

    result = await executable.run("score this", bindings=RunBindings.embedded())

    assert result.output_or_raise() == _Score(value=3)
    assert len(_compatibility_capabilities(executable)) == 1
    assert len(calls) == 2
    assert all([tool.name for tool in info.function_tools] == ["lookup"] for info in calls)
    assert all(info.model_settings is not None and info.model_settings.get("tool_choice") == "auto" for info in calls)


async def test_plain_text_preserves_explicit_none_tool_choice() -> None:
    calls: list[AgentInfo] = []

    async def model_stream(_messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(info)
        yield "summary"

    executable = HarnessBuilder().build(
        AgentSpec(model_settings={"tool_choice": "none"}),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
    )

    result = await executable.run("summarize", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "summary"
    assert _compatibility_capabilities(executable) == []
    assert len(calls) == 1
    assert calls[0].output_tools == []
    assert resolve_tool_choice(calls[0].model_settings, calls[0].model_request_parameters) == "none"


def test_structured_output_compatibility_is_injected_for_structured_output() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=dict[str, Any],
        model=FunctionModel(lambda _messages, _info: {"value": 1}),
    )
    assert len(_compatibility_capabilities(executable)) == 1
