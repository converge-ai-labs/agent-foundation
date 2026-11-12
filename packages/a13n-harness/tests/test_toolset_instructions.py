from __future__ import annotations

import re
from collections.abc import AsyncIterator
from importlib.resources import files

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
    assert ("<tool-name>summarize</tool-name>" in instructions) is expected_toolset_guidance


async def test_native_pydantic_agent_spec_keeps_toolset_instructions_enabled() -> None:
    instructions = await _capture_instructions(PydanticAgentSpec(), run_override=None)

    assert "<tool-name>summarize</tool-name>" in instructions


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


@pytest.mark.parametrize("root", [None, "/release-guidance"])
def test_operational_instructions_use_flat_attribute_free_xml_sections(root: str | None) -> None:
    from a13n_harness.toolsets._instructions import tool_instruction

    prompts = files("a13n_harness.toolsets.prompts").joinpath("guidance")
    for path in prompts.iterdir():
        if not path.name.endswith(".md"):
            continue
        name = path.name.removesuffix(".md")
        instruction = tool_instruction(name, reference_root=root)
        identity = f"<tool-name>{name}</tool-name>\n"
        assert instruction.startswith(identity)
        # Inline code can quote runtime XML such as `<memory-context>`; it is not section markup.
        body = re.sub(r"`[^`]*`", "", instruction.removeprefix(identity))
        assert not re.search(r"(?m)^#+ ", body), name
        tags = re.findall(r"</?[^>]+>", body)
        assert tags, name
        section = None
        for markup in tags:
            match = re.fullmatch(r"<(/?)([a-z][a-z-]*)>", markup)
            assert match is not None, (name, markup)
            closing, tag = match.groups()
            if closing:
                assert section == tag, (name, markup)
                section = None
            else:
                assert section is None, (name, markup)
                section = tag
        assert section is None, name


@pytest.mark.parametrize("root", [None, "/release-guidance"])
def test_file_guidance_scopes_grouped_sections_in_both_delivery_modes(root: str | None) -> None:
    from a13n_harness.toolsets._instructions import tool_instruction

    instruction = tool_instruction("files", reference_root=root)
    assert "apply only the sections for tools exposed in this Run" in instruction
    assert ("/release-guidance/harness/files.md" in instruction) is (root is not None)


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
