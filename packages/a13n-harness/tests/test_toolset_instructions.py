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
    reference_root: str | None = None,
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
        bindings=RunBindings.embedded(toolset_instructions=run_override, instruction_reference_root=reference_root),
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


@pytest.mark.parametrize("enabled", [False, True])
async def test_reference_delivery_preserves_essentials_and_the_instruction_switch(enabled: bool) -> None:
    root = "/guidance/release"
    instructions = await _capture_instructions(
        AgentSpec(instructions="Authored Agent instruction."),
        run_override=enabled,
        reference_root=root,
    )
    assert "Authored Agent instruction." in instructions
    assert "Capability instruction." in instructions
    assert (f"{root}/harness/summarize.md" in instructions) is enabled
    assert ("outside CodeAct" in instructions) is enabled
    assert "The user explicitly asks to summarize and continue" not in instructions
    assert "<!-- reference -->" not in instructions


@pytest.mark.parametrize("path", ["relative", "/guidance/../other", "/guidance/"])
def test_instruction_reference_root_requires_a_canonical_environment_path(path: str) -> None:
    with pytest.raises(ValueError):
        RunBindings.embedded(instruction_reference_root=path)


async def test_codeact_reference_delivery_changes_guidance_not_runner_schemas() -> None:
    from a13n_harness.capabilities import CodeActCapability, CodeActConfig

    observed = []

    async def model(messages, info):
        observed.append({tool.name: tool for tool in info.function_tools})
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(CodeActConfig()),),
    )
    for root in (None, "/release-guidance"):
        result = await executable.run("inspect", bindings=RunBindings.embedded(instruction_reference_root=root))
        assert result.output_or_raise() == "done"
    assert observed[0].keys() == observed[1].keys()
    for name in ("run_code", "run_program"):
        full, reference = observed[0][name], observed[1][name]
        assert full.parameters_json_schema == reference.parameters_json_schema
        assert "bisect" in full.description and "bisect" not in reference.description
        assert "/release-guidance/harness/codeact.md" in reference.description
        assert "only tool call" in reference.description
        assert "not transactional" in reference.description
        assert "no ambient filesystem" in reference.description
