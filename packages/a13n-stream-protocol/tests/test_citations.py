"""Provider citation shapes and public reply metadata across stream boundaries."""

import json
from datetime import UTC, datetime
from itertools import count

import pytest
from a13n_harness import HarnessEvent, HarnessExtensionEvent
from a13n_stream_protocol.citations import MAX_CITATION_BYTES, MAX_CITATIONS, project_citations
from a13n_stream_protocol.display import DisplayFold
from a13n_stream_protocol.observer import HarnessAguiObserver
from pydantic_ai.messages import (
    Citation,
    ContentCitationAnchor,
    DocumentCitationSource,
    MarkerCitationAnchor,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    WebCitationSource,
)


@pytest.mark.parametrize("provider", ["openai", "openai-codex", "anthropic", "bedrock", "xai"])
def test_native_text_part_citations_keep_part_local_offsets(provider):
    marker = provider in {"openai", "openai-codex", "xai"}
    late = provider == "xai"
    text = "[1]" if marker else "🙂 claim"
    anchor = MarkerCitationAnchor(start=0, end=len(text)) if marker else ContentCitationAnchor(start=0, end=len(text))
    citations = [
        Citation(
            [WebCitationSource("https://example.com"), DocumentCitationSource(document_id="file-1")], anchor=anchor
        )
    ]
    observer = HarnessAguiObserver()
    fold = DisplayFold("run")
    part_id = "provider-message"
    events = []
    for index, value in enumerate(("Intro ", text)):
        events.append(PartStartEvent(index=index, part=TextPart(value, id=part_id, provider_name=provider)))
        events.append(
            PartEndEvent(
                index=index, part=TextPart(value, id=part_id, citations=citations if index and not late else None)
            )
        )
    if late:
        events.append(PartDeltaEvent(index=1, delta=TextPartDelta("", citations_delta=citations)))
    for sequence, event in enumerate(events):
        fold.fold(
            [
                frame.model_dump(mode="json", by_alias=True)
                for frame in observer.observe(
                    HarnessEvent(
                        thread_id="thread", run_id="run", sequence=sequence, occurred_at=datetime.now(UTC), event=event
                    )
                )
            ]
        )
    first, second = [item for item in fold.items.values() if item.kind == "text_message"]
    assert first.content["text"] == "Intro " and "citations" not in first.content
    assert second.content["text"] == text and second.id != first.id
    assert second.content["citations"][0]["anchor"] == {
        "kind": "marker" if marker else "content",
        "start": 0,
        "end": len(text),
    }
    assert second.content["citations"][0]["sources"][1]["document_id"] == "file-1"


@pytest.mark.parametrize("boundary", ["part_end", "retry"])
def test_text_and_citations_delta_survives_until_closed(boundary):
    observer = HarnessAguiObserver()
    fold = DisplayFold("run")
    sequences = count()

    def feed(event):
        fold.fold(
            [
                frame.model_dump(mode="json", by_alias=True)
                for frame in observer.observe(
                    HarnessEvent(
                        thread_id="thread",
                        run_id="run",
                        sequence=next(sequences),
                        occurred_at=datetime.now(UTC),
                        event=event,
                    ),
                )
            ]
        )

    feed(PartStartEvent(index=0, part=TextPart("Initial")))
    citations = [Citation([WebCitationSource("https://example.com")], anchor=ContentCitationAnchor(start=0, end=12))]
    feed(PartDeltaEvent(index=0, delta=TextPartDelta(" claim", citations_delta=citations)))
    if boundary == "part_end":
        feed(PartEndEvent(index=0, part=TextPart("Initial claim")))
    else:
        feed(HarnessExtensionEvent(kind="lifecycle", payload={"type": "model_request_failed", "request_index": 0}))
        feed(HarnessExtensionEvent(kind="lifecycle", payload={"type": "model_request_started", "request_index": 1}))
        feed(PartStartEvent(index=0, part=TextPart("Replacement")))
    first = next(item for item in fold.items.values() if item.kind == "text_message")
    assert first.state == "completed"
    assert first.content["citations"][0]["anchor"]["end"] == 12


def test_projection_bounds_anchors_sources_and_utf8_budget():
    raw = Citation(
        [
            WebCitationSource("https://example.com/" + "x" * 2048),
            DocumentCitationSource(document_id="doc", title="T" * 280, excerpts=["E" * 600] * 4),
        ],
        anchor=ContentCitationAnchor(start=0, end=5),
    )
    [projected] = project_citations([raw, raw], text_length=4)
    assert projected.anchor is None
    [doc] = projected.sources
    assert len(doc.title) == 256 and [len(excerpt) for excerpt in doc.excerpts] == [512, 512]
    assert (
        len(project_citations(Citation([DocumentCitationSource(document_id=str(i))]) for i in range(80)))
        == MAX_CITATIONS
    )
    long = [Citation([DocumentCitationSource(document_id=str(i), excerpts=["🙂" * 512] * 2)]) for i in range(40)]
    bounded = project_citations(long)
    assert 1 < len(bounded) < len(long)
    assert bounded[-1].sources[0].document_id == str(len(bounded) - 1)
    payload = json.dumps(
        [citation.model_dump(mode="json") for citation in bounded], ensure_ascii=False, separators=(",", ":")
    )
    assert 20 * 1024 < len(payload.encode()) <= MAX_CITATION_BYTES


def test_citations_are_scoped_to_inline_child_even_with_matching_native_id():
    fold = DisplayFold("run")
    for scope in ({}, {"subagentRunId": "child"}):
        fold.fold(
            [
                {"type": "TEXT_MESSAGE_START", "messageId": "same", "role": "assistant", **scope},
                {"type": "TEXT_MESSAGE_CONTENT", "messageId": "same", "delta": "Claim", **scope},
                {"type": "TEXT_MESSAGE_END", "messageId": "same", **scope},
            ]
        )
    fold = DisplayFold.restore(fold.export())
    [delta] = fold.fold(
        [
            {
                "type": "CUSTOM",
                "name": "a13n.text_citations",
                "subagentRunId": "child",
                "value": {
                    "event": {
                        "message_id": "same",
                        "citations": [{"sources": [{"kind": "web", "url": "https://example.com"}]}],
                    }
                },
            }
        ]
    )
    assert delta.item is not None
    root, child = fold.items.values()
    assert "citations" not in root.content and child.id == delta.item.id
    assert child.content["citations"][0]["sources"][0]["url"] == "https://example.com"
