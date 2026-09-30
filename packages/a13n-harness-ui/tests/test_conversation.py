from datetime import UTC, datetime

import pytest
from a13n_harness import ContentItem, ContentMetadata, HarnessEvent
from a13n_harness.events import InputTextEvent, input_events
from a13n_harness_ui.conversation import ConversationExcerpt, ExcerptCollector, excerpt_text, input_excerpt
from pydantic_ai.messages import (
    BinaryContent,
    EnqueuedMessagesEvent,
    ModelRequest,
    PartEndEvent,
    TextContent,
    TextPart,
    UserPromptPart,
)


def event(value, *, run_id="run-root"):
    return HarnessEvent(thread_id="thread-root", run_id=run_id, sequence=0, occurred_at=datetime.now(UTC), event=value)


def test_excerpts_are_bounded_plain_text_and_media_is_payload_free():
    assert excerpt_text("hello\n\tworld\x1b\x00") == "hello world"
    assert len(excerpt_text("x" * 5000)) == 2048
    assert excerpt_text("x" * 5000).endswith("…")
    assert input_excerpt([BinaryContent(data=b"private bytes", media_type="image/png")]) == "[image/png]"
    assert input_excerpt([TextContent("injected", metadata={"display": False}), "Actual question"]) == "Actual question"
    attachment = {"harness_ui": {"attachment": {"name": "diagram.png"}}}
    assert (
        input_excerpt(
            [
                TextContent("internal attachment path", metadata=attachment),
                ContentItem(
                    BinaryContent(data=b"private", media_type="image/png"), ContentMetadata.model_validate(attachment)
                ),
            ]
        )
        == "diagram.png"
    )
    assert input_excerpt(["Explain it", TextContent("path", metadata=attachment)]) == "Explain it"


@pytest.mark.parametrize("limit", [1, 2, 5, 512, 2048])
@pytest.mark.parametrize(
    "text",
    [
        "",
        " \t\n\x00\x1b\u200b",
        "\t alpha\x00beta\n\u3000中文\u00a0end \r",
        "x" * 2048,
        "x" * 2048 + "\n\t\x00\u200b",
        "x" * 2048 + "\n\t\x00\u200b y",
        "ab\x00cd\tef",
    ],
)
def test_excerpt_matches_complete_normalization_at_truncation_boundaries(text, limit):
    normalized = " ".join("".join(char for char in text if char.isprintable() or char.isspace()).split())
    expected = normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"
    assert excerpt_text(text, limit) == expected


def test_excerpt_stops_scanning_after_truncation_is_known():
    visited = 0

    class CountedText(str):
        def __iter__(self):
            nonlocal visited
            for character in super().__iter__():
                visited += 1
                yield character

    text = CountedText("x" * (1024 * 1024))
    assert excerpt_text(text) == "x" * 2047 + "…"
    assert visited == 2049


def test_collector_preserves_first_input_and_pairs_delivered_steering():
    previous = ConversationExcerpt(
        first_input="Original task", latest_input="Older question", latest_reply="Older answer", reply_kind="final"
    )
    collector = ExcerptCollector(previous, run_id="run-root")
    collector.observe(event(InputTextEvent(input_id="input-new", source="user", content="New question")))
    assert collector.value.first_input == "Original task"
    assert collector.value.latest_input == "New question"
    assert collector.value.latest_reply == ""
    collector.observe(event(PartEndEvent(index=0, part=TextPart("Working on it"))))
    assert collector.value.reply_kind == "progress"
    collector.observe(
        event(InputTextEvent(input_id="input-child", source="user", content="child task"), run_id="run-child")
    )
    collector.observe(
        event(InputTextEvent(input_id="notification-one", source="background_process", content="tool finished"))
    )
    collector.observe(
        event(InputTextEvent(input_id="notification-two", source="async_subagent", content="child finished"))
    )
    assert collector.value.latest_input == "New question"
    collector.observe(
        event(
            EnqueuedMessagesEvent(
                enqueue_id="input-1", messages=[ModelRequest(parts=[UserPromptPart("Actually, do this")])]
            )
        )
    )
    assert collector.value.latest_input == "New question"
    collector.observe(event(InputTextEvent(input_id="input-1", source="steering", content="Actually, do this")))
    assert collector.value.latest_input == "Actually, do this"
    assert collector.value.latest_reply == ""
    assert collector.value.reply_kind == "none"
    assert previous.latest_input == "Older question"


def test_context_only_events_do_not_replace_compacted_conversation_excerpts():
    previous = ConversationExcerpt(
        first_input="Original task", latest_input="Recent question", latest_reply="Saved answer", reply_kind="final"
    )
    collector = ExcerptCollector(previous, run_id="run-root")
    collector.observe(event(InputTextEvent(input_id="context-one", source="context", content="compacted context")))
    assert collector.finish(None) == previous
    assert not collector.changed


@pytest.mark.parametrize("source", ["context", "recovery", "async_subagent", "background_process"])
def test_generated_input_never_changes_authored_excerpts(source):
    previous = ConversationExcerpt(
        first_input="Question", latest_input="Question", latest_reply="Answer", reply_kind="final"
    )
    collector = ExcerptCollector(previous, run_id="run-root")
    collector.observe(event(InputTextEvent(input_id="generated", source=source, content="Generated but displayable")))
    assert collector.finish(None) == previous
    assert not collector.changed


def test_multipart_input_groups_match_checkpoint_excerpt_and_text_wins_over_attachments():
    content = [
        ContentItem(
            BinaryContent(b"private", media_type="image/png"),
            ContentMetadata(harness_ui={"attachment": {"name": "diagram.png"}}),
        ),
        "Explain",
        "this image",
    ]
    collector = ExcerptCollector(ConversationExcerpt(), run_id="run-root")
    for observation in input_events(content, source="user", input_id="multipart"):
        collector.observe(event(observation))
    assert collector.value.first_input == collector.value.latest_input == input_excerpt(content) == "Explain this image"
    for observation in input_events(["Next", "question"], source="steering", input_id="next"):
        collector.observe(event(observation))
    assert collector.value.first_input == "Explain this image"
    assert collector.value.latest_input == "Next question"
