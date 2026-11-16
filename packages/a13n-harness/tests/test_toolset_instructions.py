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


@pytest.mark.parametrize("inline,programs", [(True, True), (True, False), (False, True)])
async def test_codeact_reference_delivery_changes_guidance_not_runner_schemas(inline, programs) -> None:
    from a13n_harness.capabilities import CodeActCapability, CodeActConfig

    observed = []

    async def model(messages, info):
        observed.append({tool.name: tool for tool in info.function_tools})
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(CodeActConfig(inline=inline, programs=programs)),),
    )
    for root in (None, "/release-guidance"):
        result = await executable.run("inspect", bindings=RunBindings.embedded(instruction_reference_root=root))
        assert result.output_or_raise() == "done"
    assert observed[0].keys() == observed[1].keys()
    first = "run_code" if inline else "run_program"
    for name in (name for name in ("run_code", "run_program") if name in observed[0]):
        full, reference = observed[0][name], observed[1][name]
        assert full.parameters_json_schema == reference.parameters_json_schema
        assert ("<run-code>" in full.description) is (name == "run_code")
        assert ("<run-program>" in full.description) is (name == "run_program")
        if name != first:
            assert full.description == reference.description
            assert f"The shared CodeAct rules are in the {first} description." in full.description
            assert "<tool-name>" not in full.description
            assert "not transactional" not in full.description
            continue
        assert "<tool-name>codeact</tool-name>" in full.description
        assert "bisect" in full.description and "bisect" not in reference.description
        assert "/release-guidance/harness/codeact.md" in reference.description
        assert "only tool call" in reference.description
        assert "not transactional" in reference.description
        assert "no ambient filesystem" in reference.description


def test_guidance_reference_explains_when_to_read_without_changing_inline_delivery() -> None:
    from a13n_harness.guidance import render_guidance

    content = "<essential-rules>Rules</essential-rules>\n\n<!-- reference -->\n\n<workflow>Details</workflow>"
    assert render_guidance(content) == "<essential-rules>Rules</essential-rules>\n\n\n\n<workflow>Details</workflow>"
    assert render_guidance(content, "/guidance/harness/example.md") == (
        "<essential-rules>Rules</essential-rules>\n\n<reference>\n"
        "Workflows and troubleshooting: `/guidance/harness/example.md`. "
        "Read it with view before a multi-step task or after an error.\n</reference>"
    )


@pytest.mark.parametrize("missing_tag", ["<run-code>", "</run-code>", "<run-program>", "</run-program>"])
def test_runner_descriptions_require_complete_sections(missing_tag: str) -> None:
    from a13n_harness.toolsets._instructions import tool_instruction
    from a13n_harness.toolsets.codeact import _runner_descriptions

    guidance = tool_instruction("codeact").replace(missing_tag, "")
    with pytest.raises(ValueError, match="CodeAct guidance requires a complete <run-"):
        _runner_descriptions(guidance, inline=True, programs=True)


@pytest.mark.parametrize(
    "page,kwargs",
    [
        (
            "tool_search",
            dict(
                search_name="find_ops",
                describe_name="describe_op",
                call_name="invoke_op",
                groups_name="domains",
                list_name="list_ops",
            ),
        ),
        (
            "subagents",
            dict(available_subagents="researcher: bounded research"),
        ),
        ("delegate", dict(available_subagents="researcher: bounded research")),
    ],
)
def test_formatted_guidance_has_no_reference_replacement_fields(page, kwargs) -> None:
    from string import Formatter

    from a13n_harness.toolsets._instructions import tool_instruction

    content = files("a13n_harness.toolsets.prompts").joinpath("guidance", f"{page}.md").read_text()
    _, marker, reference = content.partition("<!-- reference -->")
    if marker:
        assert all(field is None for _, field, _, _ in Formatter().parse(reference))
    for root in (None, "/release-guidance"):
        rendered = tool_instruction(page, reference_root=root).format(**kwargs)
        assert kwargs[next(iter(kwargs))] in rendered


@pytest.mark.parametrize(
    "page,essential,detail",
    [
        ("environment-shell", "Do not reissue it.", "Live Shell controls end at execution settlement"),
        ("tool_search", "A summary is not a calling contract.", "Exact local or"),
        ("environment-routing", "Choose the target before acting.", "Absolute file paths and shell"),
    ],
)
def test_reference_pages_keep_essentials_and_inline_delivery_keeps_details(page, essential, detail) -> None:
    from a13n_harness.toolsets._instructions import tool_instruction

    inline = tool_instruction(page)
    reference = tool_instruction(page, reference_root="/release-guidance")
    assert essential in inline and essential in reference
    assert detail in inline and detail not in reference
    assert f"/release-guidance/harness/{page}.md" in reference


async def test_real_first_party_retry_tools_declare_reference_bearing_pages() -> None:
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities import BrowserUseCapability, CodeActCapability, ToolSearchCapability, ToolSearchGroup
    from a13n_harness.capabilities.browser import BrowserBackend
    from a13n_harness.guidance import TOOL_GUIDANCE_KEY, guidance_has_reference
    from pydantic_ai import FunctionToolset

    collected = {}

    def lookup() -> int:
        return 42

    async def model(messages, info):
        collected.update({tool.name: (tool.metadata or {}).get(TOOL_GUIDANCE_KEY) for tool in info.function_tools})
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            BrowserUseCapability(AsyncMock(spec=BrowserBackend)),
            CodeActCapability(),
            ToolSearchCapability(groups={"test": ToolSearchGroup(FunctionToolset([lookup]), "Test operations")}),
        ),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    browser = {name for name in collected if name.startswith("browser_")}
    assert len(browser) == 11
    expected = {
        **dict.fromkeys(browser, "browser"),
        **dict.fromkeys(("store", "load", "forget"), "codeact"),
        **dict.fromkeys(
            ("discover_tools", "describe_tool", "call_tool", "list_tools", "list_tool_groups"), "tool_search"
        ),
    }
    assert {name: page for name, page in collected.items() if page is not None} == expected
    for page in set(expected.values()):
        assert guidance_has_reference(page)


def test_missing_guidance_page_raises_instead_of_disabling_hints() -> None:
    from a13n_harness.guidance import guidance_has_reference

    with pytest.raises(FileNotFoundError):
        guidance_has_reference("missing-guidance-page")


@pytest.mark.parametrize(
    "page,sentence",
    [
        ("environment-shell", "Prefer file tools for ordinary file reading, listing, searching and content editing."),
        ("environment-shell", "Use the `cwd` argument instead of embedding `cd` in a command."),
        ("environment-shell", "Treat `exec-*` and `process-*` references as opaque."),
        ("environment-shell", "Never invent or alter them or use them as native process IDs."),
        (
            "environment-shell",
            "Use `shell_wait` with your last returned `stdout.next_offset` and `stderr.next_offset` to wait boundedly and read the next available output page.",
        ),
        ("environment-shell", "Reads are non-consuming, so repeating the same offsets is safe."),
        (
            "environment-shell",
            "A Host-held execution delivers its result automatically when it finishes, even after your turn ends.",
        ),
        (
            "tool_search",
            "Unless a complete current definition is already known, call `{describe_name}(group=..., tool=...)` with the returned exact identity.",
        ),
        ("tool_search", "On unavailable tools or schema errors, refresh the definition."),
        ("tool_search", "When CodeAct is present, await these same functions with keyword arguments."),
        ("tool_search", "Only business targets marked `codeact_eligible=true` may execute inside CodeAct."),
        ("tool_search", "Do not call a discovered business name as a Python function."),
    ],
)
def test_normal_operation_rules_remain_inline_without_duplicate_reference_text(page, sentence) -> None:
    from a13n_harness.guidance import REFERENCE_MARKER, load_guidance

    essentials, _, reference = load_guidance(page).partition(REFERENCE_MARKER)
    assert essentials.count(sentence) == 1
    assert sentence not in reference
