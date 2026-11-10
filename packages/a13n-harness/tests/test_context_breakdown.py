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
