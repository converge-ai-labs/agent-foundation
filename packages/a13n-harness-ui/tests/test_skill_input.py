from datetime import UTC, datetime
from xml.etree import ElementTree

from a13n_harness import ContentItem, ContentMetadata, HarnessEvent
from a13n_harness.events import input_events
from a13n_harness_ui.interactive.commands import CommandRegistry
from a13n_harness_ui.interactive.history import restore_transcript
from a13n_harness_ui.interactive.inline_attachments import InlineAttachments
from a13n_harness_ui.interactive.input_display import composer_history
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer, skill_ranges
from a13n_harness_ui.interactive.skill_references import SkillProcessor, preview_skill_ranges
from a13n_harness_ui.skill_input import prepare_skill_input, retained_skill_spans
from a13n_harness_ui.surfaces import (
    SkillCatalogItemView,
    SkillCatalogView,
    TranscriptEntry,
    TranscriptPage,
    TranscriptPart,
)
from a13n_harness_ui.thread_files import ComposerAttachmentReference, ComposerInput
from a13n_stream_protocol import HarnessAguiObserver
from a13n_stream_protocol.messages import project_input_content
from prompt_toolkit.document import Document
from prompt_toolkit.layout.processors import TransformationInput
from pydantic_ai.messages import TextContent


def catalog() -> SkillCatalogView:
    return SkillCatalogView(
        catalog_id="a" * 64,
        context_kind="draft",
        items=tuple(
            SkillCatalogItemView(
                item_id=str(index) * 64,
                name=name,
                description="Selected workflow",
                source_id="project&local",
                logical_path=f"/work/<skills>/{name}",
            )
            for index, name in enumerate(("review", "test"), 1)
        ),
    )


def test_annotations_keep_per_part_unicode_offsets_and_xml_in_first_appearance_order() -> None:
    source = (
        TextContent(
            "中文😀 $test $review $review",
            metadata={"source_id": "input-test", "harness_ui": {"composer": {"index": 0}}},
        ),
        TextContent("$review", metadata={"harness_ui": {"attachment": {"name": "literal.txt"}}}),
        "$rev",
        "iew",
        "$unknown $review, x$review",
        ContentItem("$review", ContentMetadata()),
    )
    result = prepare_skill_input(source, catalog(), ("review", "test"))
    assert not isinstance(result, str)
    assert isinstance(result[0], TextContent)
    assert result[0].content == source[0].content
    spans = result[0].metadata["harness_ui"]["skills"]
    assert [(s["name"], s["start"], s["end"]) for s in spans] == [
        ("test", 4, 9),
        ("review", 10, 17),
        ("review", 18, 25),
    ]
    assert "skills" not in source[0].metadata["harness_ui"]
    assert result[1:5] == source[1:5]
    assert isinstance(result[5], ContentItem)
    assert result[5].metadata.model_extra["harness_ui"]["skills"][0]["start"] == 0
    hint = result[-1]
    assert isinstance(hint, TextContent) and hint.metadata["display"] is False
    xml = ElementTree.fromstring(hint.content)
    assert xml.tag == "skill-selection" and xml.attrib == {"source": "harness-ui"}
    assert [(child.attrib["name"], child.attrib["source"], child.text) for child in xml] == [
        (name, "project&local", f"/work/<skills>/{name}/SKILL.md") for name in ("test", "review")
    ]
    assert prepare_skill_input("$review", catalog(), ()) == "$review"
    assert retained_skill_spans("replacement", {"skills": spans}) == []
    assert retained_skill_spans(source[0].content, {"skills": spans, "long_text": True}) == []


def test_tui_live_history_and_local_echo_preserve_styles_without_catalog_reinterpretation() -> None:
    def text(value: str, index: int) -> TextContent:
        return TextContent(value, metadata={"source_id": "input-test", "harness_ui": {"composer": {"index": index}}})

    prepared = prepare_skill_input((text("😀 $review ", 0), text("$test", 1)), catalog(), ("review", "test"))
    observer = HarnessAguiObserver()
    live = StreamRenderer(Status())
    for sequence, event in enumerate(input_events(prepared, source="user", input_id="input-test")):
        for item in observer.observe(
            HarnessEvent(
                thread_id="thread-test",
                run_id="run-test",
                sequence=sequence,
                occurred_at=datetime.now(UTC),
                event=event,
            )
        ):
            live.ingest(item.type.value, item.model_dump(mode="json"))
    parts = []
    for item in prepared:
        value, meta = project_input_content(item)
        parts.append(TranscriptPart(kind="user", text=value, metadata=meta))
    history = StreamRenderer(Status())
    restore_transcript(
        history,
        TranscriptPage(total=1, entries=(TranscriptEntry(position=0, message_kind="request", parts=tuple(parts)),)),
    )
    registry = CommandRegistry()
    registry.set_skills(catalog())
    prompt = ComposerInput(("😀 $review ", "$test"))
    local = StreamRenderer(Status())
    local.local_input("input-test", prompt.display_text, preview_skill_ranges(prompt, registry))
    registry.set_skills(None)
    for renderer in (live, history, local):
        block = next(iter(renderer.transcript.blocks.values()))
        assert block.source == "> 😀 $review $test"
        assert block.skills == ((4, 11), (12, 17))
        renderer.transcript.render(80)
        styled = "".join(text for row in renderer.transcript.rows for style, text in row if "underline" in style)
        assert styled == "$review$test"
        renderer.transcript.close()
    assert parts[0].metadata.model_extra["harness_ui"]["skills"][0]["start"] == 2


def test_history_keeps_later_skill_spans_after_first_part_long_text_replacement() -> None:
    prepared = prepare_skill_input(
        (
            TextContent("$review", metadata={"source_id": "input-test", "harness_ui": {"composer": {"index": 0}}}),
            TextContent(" $test", metadata={"source_id": "input-test", "harness_ui": {"composer": {"index": 1}}}),
        ),
        catalog(),
        ("review", "test"),
    )
    parts = []
    for item in prepared:
        value, metadata = project_input_content(item)
        parts.append(TranscriptPart(kind="user", text=value, metadata=metadata))
    parts[0].metadata.model_extra["harness_ui"]["long_text"] = True
    parts[0] = parts[0].model_copy(update={"text": "Long text saved to attachment"})
    combined = composer_history(tuple(parts))[0]
    offset = len(parts[0].text)
    assert combined.text == parts[0].text + " $test"
    assert skill_ranges(combined.text, combined.metadata) == ((offset + 1, offset + 6),)
    assert parts[0].metadata.model_extra["harness_ui"]["long_text"] is True
    assert parts[1].metadata.model_extra["harness_ui"]["skills"][0]["start"] == 1


def test_non_object_metadata_namespace_does_not_break_input_or_history() -> None:
    for namespace in (None, "legacy", []):
        source = TextContent("$review", metadata={"harness_ui": namespace})
        assert retained_skill_spans(source.content, namespace) == []
        value, metadata = project_input_content(source)
        assert skill_ranges(value, metadata) == ()
        result = prepare_skill_input((source,), catalog(), ("review",))
        value, metadata = project_input_content(result[0])
        assert skill_ranges(value, metadata) == ((0, 7),)
        assert source.metadata["harness_ui"] == namespace


def test_tui_processor_and_reference_matching_do_not_cross_attachments() -> None:
    registry = CommandRegistry()
    registry.set_skills(catalog())
    attachments = InlineAttachments()
    token = attachments.reserve()
    text = f"$rev{token}iew {token}$review"
    processor = SkillProcessor(registry, attachments)
    transformed = processor.apply_transformation(
        TransformationInput(
            buffer_control=None,
            document=Document(text),
            lineno=0,
            source_to_display=lambda x: x,
            fragments=[("", text)],
            width=80,
            height=24,
        )
    )
    assert "".join(t for _, t in transformed.fragments) == text
    assert "".join(t for style, t in transformed.fragments if "input-area.skill" in style) == "$review"
    assert (
        registry.skill_references(ComposerInput(("$rev", ComposerAttachmentReference("attachment-test"), "iew"))) == ()
    )
    assert [ref.name for ref in registry.skill_references(ComposerInput(("$review", "$review $test")))] == [
        "review",
        "test",
    ]
