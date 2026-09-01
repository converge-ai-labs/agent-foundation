from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.capabilities import (
    HandoffCapability,
    HandoffConfiguration,
)
from pydantic_ai.agent.spec import AgentSpec as PydanticAgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


async def _capture_instructions(
    spec: PydanticAgentSpec,
    *,
    run_override: bool | None,
) -> str:
    captured: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info.instructions or "")
        yield "done"

    executable = HarnessBuilder().build(
        spec,
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Capability(instructions="Capability instruction."),
            HandoffCapability(HandoffConfiguration(include_summary_reminder=False)),
        ),
    )
    result = await executable.run(
        "inspect",
        bindings=RunBindings.embedded(toolset_instructions=run_override),
    )

    assert result.output_or_raise() == "done"
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize(
    ("spec_enabled", "run_override", "expected_toolset_guidance"),
    [
        (True, None, True),
        (False, None, False),
        (False, True, True),
        (True, False, False),
    ],
)
async def test_toolset_instruction_switch_uses_run_override_before_agent_default(
    spec_enabled: bool,
    run_override: bool | None,
    expected_toolset_guidance: bool,
) -> None:
    instructions = await _capture_instructions(
        AgentSpec(
            instructions="Authored Agent instruction.",
            toolset_instructions=spec_enabled,
        ),
        run_override=run_override,
    )

    assert "Authored Agent instruction." in instructions
    assert "Capability instruction." in instructions
    assert ('<tool-instruction name="summarize">' in instructions) is expected_toolset_guidance


async def test_native_pydantic_agent_spec_keeps_toolset_instructions_enabled() -> None:
    instructions = await _capture_instructions(PydanticAgentSpec(), run_override=None)

    assert '<tool-instruction name="summarize">' in instructions


def test_run_binding_rejects_non_boolean_toolset_instruction_override() -> None:
    with pytest.raises(TypeError, match="toolset_instructions must be a boolean or None"):
        RunBindings.embedded(toolset_instructions=1)  # type: ignore[arg-type]
