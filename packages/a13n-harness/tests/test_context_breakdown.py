from a13n_harness.content import ContentItem, ContentMetadata, input_request
from a13n_harness.context_breakdown import ContextItemUsage, InstructionCatalog, measure_context
from a13n_harness.model_context import ModelContextBlock, ModelContextPlacement
from pydantic_ai.messages import (
    BinaryContent,
    CapabilityInstructionSource,
    InstructionDeltaPart,
    InstructionId,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition


def test_measure_only_retained_visible_input_and_attribute_each_character_once():
    visible = ToolDefinition(name="read", description="Read a file.")
    hidden = ToolDefinition(name="hidden", defer_loading=True)
    proxy = ToolDefinition(name="invoke", metadata={"a13n.tool_search.control": "call"})
    call = ToolCallPart("invoke", {"group": "fs", "tool": "read", "arguments": {"path": "SKILL.md"}}, "c1")
    memory = ToolReturnPart("memory_file_view", {"memory": "personal", "text": "private content"}, "c2")
    request = input_request(
        ["你好", ContentItem("outline", ContentMetadata(display=False, source_id="a13n.workspace-outline"))]
    )
    breakdown = measure_context(
        [
            request,
            ModelResponse(parts=[call, ToolCallPart("memory_file_view", {"memory": "personal"}, "c2")]),
            ModelRequest(parts=[ToolReturnPart("invoke", "skill body", "c1"), memory]),
        ],
        ModelRequestParameters(function_tools=[visible, hidden, proxy]),
    )
    g = breakdown.groups
    assert breakdown.counted_input_characters == sum(group.characters for group in g.values())
    assert set(g["tools"].items) == {"read", "invoke"}
    assert "skills" not in g
    assert g["conversation"].characters == 2
    assert g["environment_context"].characters == len("outline")
    item = g["tool_calls"].items["fs.read"]
    assert item.calls == 1 and item.argument_characters == len(call.args_as_json_str())
    assert item.result_characters == len("skill body")
    assert g["tool_calls"].items["memory_file_view"].result_characters == 0
    assert g["memory"].items["personal"].characters == len(memory.model_response_str())
    assert "private content" not in breakdown.model_dump_json()


def test_unmeasured_media_does_not_enter_text_counts():
    image = BinaryContent(data=b"arbitrary bytes", media_type="image/png")
    audio = BinaryContent(data=b"sample", media_type="audio/wav")
    breakdown = measure_context(
        [input_request(["caption", image]), ModelRequest(parts=[ToolReturnPart("listen", [audio, "heard"])])],
        ModelRequestParameters(),
    )
    assert set(breakdown.unmeasured_content) == {"audio", "other"}
    assert breakdown.groups["conversation"].characters == len("caption")
    assert breakdown.groups["tool_calls"].items["listen"].result_characters == len("heard")


def test_producer_groups_and_exact_catalog_match():
    block = ModelContextBlock(
        "custom.source", ModelContextPlacement.INPUT_PREAMBLE, "abc", context_group="runtime_state"
    )
    key = InstructionId(CapabilityInstructionSource("custom.catalog"))
    catalog = InstructionCatalog("skills", "current entry", {"one": ContextItemUsage(characters=5)})
    parts = [InstructionDeltaPart(id=key, content=text) for text in ("old entry", catalog.text)]
    breakdown = measure_context(
        [input_request([block.content_item()]), ModelRequest(parts=parts)],
        ModelRequestParameters(),
        {str(key.source): catalog},
    )
    assert breakdown.groups["runtime_state"].characters == 3
    skills = breakdown.groups["skills"]
    assert skills.characters == len("old entry") + len(catalog.text)
    assert skills.items["one"].characters == 5


def test_large_skill_descriptions_leave_room_for_every_category():
    description = "技能说明" * 150
    entries = {f"skill_{i}": ContextItemUsage(characters=len(description)) for i in range(51)}
    catalog = InstructionCatalog("skills", description * len(entries), entries)
    key = InstructionId(CapabilityInstructionSource("custom.skills"))
    tools = [ToolDefinition(name=f"tool_{i}") for i in range(56)]
    calls = [ToolCallPart(tool.name, {}, f"call_{i}") for i, tool in enumerate(tools[:6])]
    mounts = [
        ModelContextBlock(
            "custom.memory", ModelContextPlacement.INPUT_PREAMBLE, name, memory_mount=name, context_group="memory"
        ).content_item()
        for name in ("team", "personal")
    ]
    result = measure_context(
        [
            ModelRequest(parts=[InstructionDeltaPart(id=key, content=catalog.text)]),
            input_request(mounts),
            ModelResponse(parts=calls),
        ],
        ModelRequestParameters(function_tools=tools),
        {str(key.source): catalog},
    )
    assert {key: len(group.items) for key, group in result.groups.items()} == {
        "skills": 51,
        "tools": 56,
        "memory": 2,
        "tool_calls": 6,
    }
    assert all(group.omitted_items == 0 for group in result.groups.values())
    assert description not in result.model_dump_json()
    assert ContextItemUsage.model_validate({"characters": 5, "description": "old record"}).model_dump() == {
        "characters": 5
    }


def test_source_details_partition_retained_inputs_without_disclosing_content():
    def block(source, key, text, group):
        return ModelContextBlock(
            source, ModelContextPlacement.REQUEST_EPILOGUE, text, key=key, context_group=group
        ).content_item()

    def text(value, **metadata):
        return ContentItem(value, ContentMetadata.model_validate(metadata))

    request = input_request(
        [
            *(block("a13n.environment-mounts", None, value, "environment_context") for value in ("one", "two", "one")),
            block("a13n.file-context", "file:/secret/project", "abcd", "environment_context"),
            block("a13n.file-context", "files", "xy", "environment_context"),
            block("a13n.file-context", "project-guidance", "abc", "environment_context"),
            block("a13n.working-state.notes", None, "notes", "runtime_state"),
            block("tool_execution.summary", None, "a list", "runtime_state"),
            block("a13n.runtime-context", None, "time", "runtime_state"),
            block("custom.private-source", None, "?", "runtime_state"),
            text("hi", **{"a13n.input-provenance": {"kind": "task"}}),
            text("ok!", source_id="custom-unrestricted-id", harness_ui={"composer": {"index": 0}}),
            text("summary", **{"a13n.context": "handoff"}),
            text("notice", **{"a13n.steering-source": "async_subagent", "a13n.input-provenance": {"kind": "steer"}}),
            text("ref", harness_ui={"composer": {"index": 0}, "attachment": {}}),
            text("recovery", **{"a13n.input-provenance": {"kind": "recovery"}}),
            "<notes>ambiguous</notes>",
        ],
        metadata={"a13n.context": "handoff"},
    )
    result = measure_context(
        [
            request,
            ModelResponse(parts=[TextPart("reply"), ThinkingPart("think")]),
            ModelResponse(parts=[TextPart("compact")], metadata={"keep": "compact"}),
        ],
        ModelRequestParameters(),
    )
    env = result.groups["environment_context"]
    assert env.items["Environment mounts"].model_dump() == {"characters": 9, "occurrences": 3, "distinct_versions": 2}
    assert {k: v.characters for k, v in env.items.items()} == {
        "Environment mounts": 9,
        "Project files": 4,
        "File path list": 2,
        "Project guidance index": 3,
    }
    assert {k: v.characters for k, v in result.groups["runtime_state"].items.items()} == {
        "Notes": 5,
        "Tool execution status": 6,
        "Time and usage": 4,
        "Unattributed": 1,
    }
    assert {k: v.characters for k, v in result.groups["conversation"].items.items()} == {
        "User": 5,
        "Agent": 10,
        "Context summaries": 14,
        "Unattributed": len("<notes>ambiguous</notes>"),
        "Input references": 3,
        "Runtime notices": 6,
        "Context recovery": 8,
    }
    for group in result.groups.values():
        assert sum(item.characters for item in group.items.values()) == group.characters
    assert "/secret" not in result.model_dump_json() and "private-source" not in result.model_dump_json()
