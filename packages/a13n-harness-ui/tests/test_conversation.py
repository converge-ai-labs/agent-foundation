from datetime import UTC, datetime

from a13n_harness import HarnessEvent
from a13n_harness.model_context import ModelInputEvent
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
                BinaryContent(data=b"private", media_type="image/png", vendor_metadata=attachment),
            ]
        )
        == "diagram.png"
    )
    assert input_excerpt(["Explain it", TextContent("path", metadata=attachment)]) == "Explain it"


def test_collector_preserves_first_input_and_pairs_delivered_steering():
    previous = ConversationExcerpt(
        first_input="Original task", latest_input="Older question", latest_reply="Older answer", reply_kind="final"
    )
    collector = ExcerptCollector(previous, run_id="run-root")
    collector.observe(event(ModelInputEvent(content=["New question"])))
    assert collector.value.first_input == "Original task"
    assert collector.value.latest_input == "New question"
    assert collector.value.latest_reply == ""
    collector.observe(event(PartEndEvent(index=0, part=TextPart("Working on it"))))
    assert collector.value.reply_kind == "progress"
    collector.observe(event(ModelInputEvent(content=["child task"]), run_id="run-child"))
    collector.observe(
        event(
            ModelInputEvent(
                content=[TextContent("tool finished", metadata={"a13n.steering-source": "background_process"})]
            )
        )
    )
    collector.observe(
        event(
            ModelInputEvent(
                content=[TextContent("child finished", metadata={"a13n.steering-source": "async_subagent"})]
            )
        )
    )
    assert collector.value.latest_input == "New question"
    collector.observe(
        event(
            EnqueuedMessagesEvent(
                enqueue_id="input-1", messages=[ModelRequest(parts=[UserPromptPart("Actually, do this")])]
            )
        )
    )
    assert collector.value.latest_input == "Actually, do this"
    assert collector.value.latest_reply == ""
    assert collector.value.reply_kind == "none"
    assert previous.latest_input == "Older question"


def test_context_only_events_do_not_replace_compacted_conversation_excerpts():
    previous = ConversationExcerpt(
        first_input="Original task", latest_input="Recent question", latest_reply="Saved answer", reply_kind="final"
    )
    collector = ExcerptCollector(previous, run_id="run-root")
    collector.observe(event(ModelInputEvent(content=[TextContent("compacted context", metadata={"display": False})])))
    assert collector.finish(None) == previous
    assert not collector.changed
