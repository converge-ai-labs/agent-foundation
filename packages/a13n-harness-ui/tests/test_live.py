from __future__ import annotations

import pytest
from a13n_harness_ui.errors import LivePresentationError
from a13n_harness_ui.live import HarnessUiLiveHub, HarnessUiSummaryHub, LiveCursor, SummaryCursor
from ag_ui.core.events import TextMessageContentEvent

pytestmark = pytest.mark.anyio


def _text_event(value: str) -> TextMessageContentEvent:
    return TextMessageContentEvent(timestamp=1, message_id="message-1", delta=value)


async def test_live_hub_cutover_follows_complete_root_lineage_and_detaches_events() -> None:
    hub = HarnessUiLiveHub(epoch="live-test", ring_size=4, subscriber_buffer_size=2)
    await hub.publish(
        run_kind="root",
        root_thread_id="thread-1",
        parent_thread_id=None,
        thread_id="thread-1",
        run_id="run-1",
        events=(_text_event("before"),),
    )

    async with hub.subscribe(root_thread_id="thread-1") as subscription:
        assert subscription.cursor == LiveCursor(epoch="live-test", sequence=1)
        await hub.publish(
            run_kind="child",
            root_thread_id="thread-1",
            parent_thread_id="thread-1",
            thread_id="thread-child",
            run_id="run-child",
            execution_id="execution-1",
            events=(_text_event("child"),),
        )
        await hub.publish(
            run_kind="root",
            root_thread_id="thread-2",
            parent_thread_id=None,
            thread_id="thread-2",
            run_id="run-2",
            events=(_text_event("other"),),
        )
        retained = await subscription.receive()
        assert retained.sequence == 2
        assert retained.root_thread_id == "thread-1"
        assert retained.parent_thread_id == "thread-1"
        assert retained.thread_id == "thread-child"
        assert retained.payload is not None
        assert retained.payload["delta"] == "child"
        retained.payload["delta"] = "changed"

    snapshot = await hub.snapshot(root_thread_id="thread-1")
    assert snapshot[-1].payload is not None
    assert snapshot[-1].payload["delta"] == "child"
    await hub.close()


async def test_live_hub_requires_reset_when_subscriber_or_cursor_falls_behind() -> None:
    hub = HarnessUiLiveHub(epoch="live-test", ring_size=2, subscriber_buffer_size=1)

    async with hub.subscribe(root_thread_id="thread-1") as subscription:
        for value in ("first", "second"):
            await hub.publish(
                run_kind="root",
                root_thread_id="thread-1",
                parent_thread_id=None,
                thread_id="thread-1",
                run_id="run-1",
                events=(_text_event(value),),
            )
        with pytest.raises(LivePresentationError) as slow:
            await subscription.receive()
    assert slow.value.code == "live_cursor_expired"

    await hub.publish(
        run_kind="root",
        root_thread_id="thread-1",
        parent_thread_id=None,
        thread_id="thread-1",
        run_id="run-1",
        events=(_text_event("third"),),
    )
    with pytest.raises(LivePresentationError) as expired:
        async with hub.subscribe(after=LiveCursor(epoch="live-test", sequence=0)):
            pass
    assert expired.value.code == "live_cursor_expired"
    await hub.close()


async def test_live_hub_omits_oversized_payloads() -> None:
    hub = HarnessUiLiveHub()
    await hub.publish(
        run_kind="child",
        root_thread_id="thread-root",
        parent_thread_id="thread-root",
        thread_id="thread-1",
        run_id="run-1",
        execution_id="execution-1",
        events=(_text_event("x" * (65 * 1024)),),
    )

    event = (await hub.snapshot())[0]
    assert event.run_kind == "child"
    assert event.execution_id == "execution-1"
    assert event.payload is None
    assert event.payload_omitted
    await hub.close()


async def test_summary_hub_emits_lightweight_replayable_invalidation_hints() -> None:
    hub = HarnessUiSummaryHub(epoch="live-test", ring_size=4, subscriber_buffer_size=2)
    await hub.publish(kind="configuration")

    async with hub.subscribe(after=SummaryCursor(epoch="live-test", sequence=0)) as subscription:
        first = await subscription.receive()
        assert first.kind == "configuration"
        await hub.publish(
            kind="child_execution",
            root_thread_id="thread-root",
            thread_id="thread-child",
            execution_id="execution-1",
        )
        child = await subscription.receive()

    assert child.root_thread_id == "thread-root"
    assert child.thread_id == "thread-child"
    assert child.execution_id == "execution-1"
    await hub.close()


async def test_large_compaction_summary_reaches_live_renderer_without_payload_omission() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_harness.capabilities import CompactionSummaryEvent
    from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
    from a13n_stream_protocol import HarnessAguiObserver

    summary = "Complete summary.\n" * 8000
    source = HarnessEvent(
        thread_id="thread-1",
        run_id="run-1",
        sequence=0,
        occurred_at=datetime.now(UTC),
        event=CompactionSummaryEvent(operation_id="compact-1", summary=summary),
    )
    hub = HarnessUiLiveHub()
    await hub.publish(
        run_kind="root",
        root_thread_id="thread-1",
        parent_thread_id=None,
        thread_id="thread-1",
        run_id="run-1",
        events=HarnessAguiObserver().observe(source),
    )
    renderer = StreamRenderer(Status())
    for event in await hub.snapshot():
        assert not event.payload_omitted
        renderer.ingest(event.event_type, event.payload, run_id=event.run_id)
    assert next(iter(renderer.transcript.blocks.values())).source == "Compacting context…"
    renderer.ingest(
        "CUSTOM",
        {
            "name": "a13n.harness.context",
            "value": {"event": {"payload": {"type": "compaction_completed", "operation_id": "compact-1"}}},
        },
        run_id="run-1",
    )
    blocks = list(renderer.transcript.blocks.values())
    assert blocks[0].source.endswith(summary)
    assert blocks[0].kind == "compact"
    assert not renderer.assistant_seen


async def test_large_fragment_batch_reaches_an_active_bounded_subscriber() -> None:
    import asyncio
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_harness.toolsets.events import FileEditAppliedEvent
    from a13n_stream_protocol import CustomEventAssembler, HarnessAguiObserver

    content = "line\n" * 40000
    source = HarnessEvent(
        thread_id="thread-one",
        run_id="run-one",
        sequence=0,
        occurred_at=datetime.now(UTC),
        event=FileEditAppliedEvent(file_path="large.txt", before=content, after=content + "final"),
    )
    events = HarnessAguiObserver().observe(source)
    assert len(events) > 64
    hub = HarnessUiLiveHub()
    assembler = CustomEventAssembler()
    complete = []
    async with hub.subscribe() as subscription:

        async def consume():
            for _ in events:
                event = await subscription.__anext__()
                assert event.payload is not None
                result = assembler.accept(event.payload)
                if result is not None:
                    complete.append(result)

        consumer = asyncio.create_task(consume())
        try:
            await hub.publish(
                run_kind="root",
                root_thread_id="thread-one",
                parent_thread_id=None,
                thread_id="thread-one",
                run_id="run-one",
                events=events,
            )
            await asyncio.wait_for(consumer, 3)
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
    assert len(complete) == 1
    assert complete[0]["value"]["event"]["after"] == content + "final"
    assert not assembler.gap
    await hub.close()


async def test_child_display_does_not_treat_input_or_compaction_as_an_answer() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_harness.capabilities import CompactionSummaryEvent
    from a13n_harness.model_context import ModelInputEvent
    from a13n_harness_ui.subagent_operator import CompactChildDisplay, _DisplayCompactor
    from a13n_stream_protocol import HarnessAguiObserver
    from pydantic_ai.messages import PartEndEvent, PartStartEvent, TextContent, TextPart

    observer = HarnessAguiObserver()
    display = _DisplayCompactor(CompactChildDisplay())
    for sequence, event in enumerate(
        [
            ModelInputEvent(
                content=[TextContent("visible question"), TextContent("hidden guidance", metadata={"display": False})]
            ),
            CompactionSummaryEvent(operation_id="compact-1", summary="summary content"),
            PartStartEvent(index=0, part=TextPart("actual answer")),
            PartEndEvent(index=0, part=TextPart("actual answer")),
        ]
    ):
        source = HarnessEvent(
            thread_id="thread-1", run_id="run-1", sequence=sequence, occurred_at=datetime.now(UTC), event=event
        )
        display.observe(observer.observe(source))
    assert [activity.text for activity in display.snapshot().activities] == ["actual answer"]


async def test_root_observer_bootstrap_survives_ring_eviction_and_publication_races() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_stream_protocol import HarnessAguiObserver
    from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

    observer = HarnessAguiObserver()
    hub = HarnessUiLiveHub(ring_size=2)

    def observe(sequence, value):
        return observer.observe(
            HarnessEvent(
                thread_id="thread-root",
                run_id="run-root",
                sequence=sequence,
                occurred_at=datetime.now(UTC),
                event=value,
            )
        )

    async def publish(events):
        await hub.publish(
            run_kind="root",
            root_thread_id="thread-root",
            parent_thread_id=None,
            thread_id="thread-root",
            run_id="run-root",
            events=events,
            observer=observer,
            base_continuation_id="saved-before",
        )

    await publish(observe(1, PartStartEvent(index=0, part=TextPart(content="begin"))))
    for index in range(2, 40):
        await publish(observe(index, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=str(index)))))
    count = observer.event_count
    # Accumulated but not published yet: this must appear only in subsequent live delivery.
    pending = observe(40, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="pending")))
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        replay = subscription.root_stream
        assert replay is not None
        assert replay.observer is observer  # Existing owner, no second payload store.
        assert replay.summary.event_count == count
        assert replay.summary.base_continuation_id == "saved-before"
        batches = list(replay.batches())
        events = [item for batch in batches for item in batch]
        assert all(len(batch) <= 16 for batch in batches)
        assert [item.index for item in events] == list(range(count))
        assert any(item.payload and item.payload.get("delta") == "begin" for item in events)
        assert not any(item.payload and item.payload.get("delta") == "pending" for item in events)
        await publish(pending)
        delivered = await subscription.receive()
        assert delivered.sequence > subscription.root_stream.summary.event_count
        assert delivered.payload is not None and delivered.payload["delta"] == "pending"
        assert [item for batch in replay.batches() for item in batch] == events
        await hub.finish_root(thread_id="thread-root", run_id="run-root", saved_continuation_id="saved-after")
        assert "thread-root" not in hub._root_streams
        assert list(replay.batches()) == batches  # Existing delivery retains its finite reference.
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        assert subscription.root_stream is None
    await hub.close()


async def test_unsaved_root_retention_is_bounded_and_never_evicts_active_runs() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_stream_protocol import HarnessAguiObserver
    from pydantic_ai.messages import PartStartEvent, TextPart

    hub = HarnessUiLiveHub(ring_size=2)

    async def start(thread_id, run_id):
        observer = HarnessAguiObserver()
        events = observer.observe(
            HarnessEvent(
                thread_id=thread_id,
                run_id=run_id,
                sequence=1,
                occurred_at=datetime.now(UTC),
                event=PartStartEvent(index=0, part=TextPart(content="output")),
            )
        )
        await hub.publish(
            run_kind="root",
            root_thread_id=thread_id,
            parent_thread_id=None,
            thread_id=thread_id,
            run_id=run_id,
            events=events,
            observer=observer,
        )

    await start("thread-active", "run-active")
    for index in range(257):
        thread_id = f"thread-{index}"
        await start(thread_id, f"run-{index}")
        await hub.finish_root(thread_id=thread_id, run_id=f"run-{index}", saved_continuation_id=None)
    assert len(hub._terminal_streams) == 256
    assert "thread-0" not in hub._root_streams
    assert "thread-active" in hub._root_streams
    await start("thread-256", "run-replacement")
    await hub.finish_root(thread_id="thread-256", run_id="run-256", saved_continuation_id="old-save")
    async with hub.subscribe(root_thread_id="thread-256") as subscription:
        assert subscription.root_stream is not None
        assert subscription.root_stream.summary.run_id == "run-replacement"
    assert "thread-256" not in hub._terminal_streams
    await hub.close()
    assert not hub._root_streams
