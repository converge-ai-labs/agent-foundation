from __future__ import annotations

import pytest
from a13n_harness_ui.display import baseline
from a13n_harness_ui.errors import LivePresentationError
from a13n_harness_ui.live import HarnessUiLiveHub, HarnessUiSummaryHub, LiveCursor, SummaryCursor
from a13n_stream_protocol.display import DisplayScope, DisplaySnapshot, DisplayState
from a13n_stream_protocol.projector import DisplayProjector
from ag_ui.core.events import RunErrorEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

pytestmark = pytest.mark.anyio


def _text_event(value: str) -> RunErrorEvent:
    return RunErrorEvent(timestamp=1, message=value)


def _producer(hub, thread="thread-root", run="run-root", *, parent=None, execution=None, batch=False):
    projector = DisplayProjector(baseline(run, None), batch=batch)
    hub.register_display(
        projector=projector,
        root_thread_id=parent or thread,
        thread_id=thread,
        parent_thread_id=parent,
        execution_id=execution,
        base_continuation_id="saved-before",
    )
    projector.scope(DisplayScope(id=run, run_id=run, thread_id=thread))
    return projector


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
        assert retained.payload["message"] == "child"
        retained.payload["message"] = "changed"

    snapshot = await hub.snapshot(root_thread_id="thread-1")
    assert snapshot[-1].payload is not None
    assert snapshot[-1].payload["message"] == "child"
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


async def test_large_baseline_is_chunked_and_detached_without_fragment_event_history() -> None:
    import json

    hub = HarnessUiLiveHub(ring_size=2)
    projector = _producer(hub)
    summary = "完整 summary.\n" * 8000
    projector.summary("run-root", "compact-1", "compaction", summary)
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        replay = subscription.root_stream
        assert replay is not None
        chunks = list(replay.chunks())
        assert len(chunks) > 1
        assert all(len(chunk.encode()) <= 48 * 1024 for chunk in chunks)
        parsed = DisplaySnapshot.model_validate(json.loads("".join(chunks))["display"])
        assert parsed.blocks[0].content["text"] == summary
        assert parsed.position == replay.summary.position
        projector.summary("run-root", "compact-1", "compaction", "changed")
        assert replay.display.blocks[0].content["text"] == summary
    await hub.close()


async def test_oversized_delta_is_explicit_gap_but_fresh_baseline_covers_it() -> None:
    hub = HarnessUiLiveHub()
    projector = _producer(hub)
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        projector.observe("run-root", 0, PartStartEvent(index=0, part=TextPart(content="x" * (300 * 1024))))
        event = await subscription.receive()
        assert event.event_type == "DISPLAY_DELTA"
        assert event.delta is None and event.payload_omitted
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        assert subscription.root_stream.display.blocks[0].content["text"] == "x" * (300 * 1024)
    await hub.close()


async def test_child_display_does_not_treat_input_or_compaction_as_an_answer() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_harness.capabilities import CompactionSummaryEvent
    from a13n_harness.model_context import ModelInputEvent
    from a13n_harness_ui.display_projection import child_presentation
    from a13n_stream_protocol.display import DisplayScope
    from a13n_stream_protocol.projector import DisplayProjector
    from pydantic_ai.messages import PartEndEvent, PartStartEvent, TextContent, TextPart

    from .display_fixtures import display_snapshot

    display = DisplayProjector(display_snapshot())
    display.scope(DisplayScope(id="run-1", run_id="run-1", thread_id="thread-1"))
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
        display.observe("run-1", 0, source.event)
    assert [activity.text for activity in child_presentation(display.capture()).activities] == ["actual answer"]


async def test_producer_bootstrap_covers_stalled_delivery_and_ring_eviction_atomically() -> None:
    hub = HarnessUiLiveHub(ring_size=2)
    projector = _producer(hub, batch=True)
    projector.observe("run-root", 0, PartStartEvent(index=0, part=TextPart(content="begin")))
    for index in range(40):
        projector.observe("run-root", 0, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=str(index))))
        projector.flush()
    # No public event consumer has run. A staged tail must be flushed before cutover.
    projector.observe("run-root", 0, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="pending")))
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        replay = subscription.root_stream
        assert replay is not None
        assert not hasattr(replay, "observer")
        assert replay.summary.base_continuation_id == "saved-before"
        assert replay.display.blocks[0].content["text"].endswith("pending")
        state = DisplayState(replay.display)
        original = list(replay.chunks())
        projector.observe("run-root", 0, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="next")))
        projector.flush()
        delivered = await subscription.receive()
        assert delivered.sequence > subscription.cursor.sequence
        assert delivered.delta.from_sequence == replay.display.position.sequence
        state.apply(delivered.delta)
        assert state.capture() == projector.capture()
        await hub.finish_root(thread_id="thread-root", run_id="run-root", saved_continuation_id="saved-after")
        assert "thread-root" not in hub._root_streams
        assert list(replay.chunks()) == original
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        assert subscription.root_stream is None
    await hub.close()


async def test_child_baseline_has_own_producer_under_same_family_cutover() -> None:
    hub = HarnessUiLiveHub()
    root = _producer(hub, batch=True)
    child = _producer(
        hub, thread="thread-child", run="run-child", parent="thread-root", execution="exec-one", batch=True
    )
    root.observe("run-root", 0, PartStartEvent(index=0, part=TextPart(content="root")))
    child.observe("run-child", 0, PartStartEvent(index=0, part=TextPart(content="child")))
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        assert (
            subscription.root_stream.display.position.producer
            != subscription.child_streams[0].display.position.producer
        )
        assert subscription.child_streams[0].display.blocks[0].content["text"] == "child"
        assert subscription.child_streams[0].summary.execution_id == "exec-one"
        child.observe("run-child", 0, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="next")))
        child.flush()
        event = await subscription.receive()
        assert event.sequence > subscription.cursor.sequence
        assert event.execution_id == "exec-one"
    await hub.close()


async def test_unsaved_root_retention_is_bounded_and_never_evicts_active_runs() -> None:
    hub = HarnessUiLiveHub(ring_size=2)
    for index in range(20):
        _producer(hub, f"thread-active-{index}", f"run-active-{index}")
    for index in range(17):
        thread = f"thread-{index}"
        _producer(hub, thread, f"run-{index}")
        await hub.finish_root(thread_id=thread, run_id=f"run-{index}", saved_continuation_id=None)
    assert len(hub._terminal_streams) == 16
    assert "thread-0" not in hub._root_streams
    for index in range(20):
        assert f"thread-active-{index}" in hub._root_streams
        assert f"thread-active-{index}" in hub._root_rings
    assert len(hub._root_rings) == 36
    _producer(hub, "thread-16", "run-replacement")
    await hub.finish_root(thread_id="thread-16", run_id="run-16", saved_continuation_id="old-save")
    async with hub.subscribe(root_thread_id="thread-16") as subscription:
        assert subscription.root_stream.summary.run_id == "run-replacement"
    assert "thread-16" not in hub._terminal_streams
    await hub.close()
    assert not hub._root_streams


async def test_root_replay_is_not_evicted_by_another_roots_events() -> None:
    hub = HarnessUiLiveHub(epoch="live-test", ring_size=2)

    async def publish(root: str) -> None:
        await hub.publish(
            run_kind="root",
            root_thread_id=root,
            parent_thread_id=None,
            thread_id=root,
            run_id="run-one",
            events=(_text_event(root),),
        )

    await publish("quiet")
    async with hub.subscribe(root_thread_id="quiet") as subscription:
        cursor = subscription.cursor
    for _ in range(10):
        await publish("noisy")
    await publish("quiet")
    async with hub.subscribe(root_thread_id="quiet", after=cursor) as resumed:
        assert (await resumed.receive()).root_thread_id == "quiet"
    # Sparse global sequence numbers do not imply that this root lost events.
    async with hub.subscribe(root_thread_id="never-published", after=cursor):
        pass
    await publish("quiet")
    await publish("quiet")
    with pytest.raises(LivePresentationError, match="no longer retained"):
        async with hub.subscribe(root_thread_id="quiet", after=cursor):
            pass
    # Root-ring cardinality is bounded too; evicted roots explicitly reset.
    for index in range(17):
        await publish(f"other-{index}")
    assert len(hub._root_rings) == 16
    with pytest.raises(LivePresentationError, match="no longer retained"):
        async with hub.subscribe(root_thread_id="quiet", after=cursor):
            pass
    await hub.close()


async def test_baseline_retains_controls_by_source_and_process_with_explicit_omission() -> None:
    from ag_ui.core.events import CustomEvent

    hub = HarnessUiLiveHub()
    _producer(hub)
    controls = [
        CustomEvent(name="a13n.harness.usage", value={"run_id": run, "event": {"payload": {"source": run}}})
        for run in ("run-root", "inline-child")
    ]
    controls.extend(
        CustomEvent(
            name="a13n.shell.status", value={"run_id": "run-root", "event": {"process_id": process, "phase": "running"}}
        )
        for process in ("process-one", "process-two")
    )
    controls.append(
        CustomEvent(name="a13n.harness.recovery", value={"run_id": "run-root", "event": {"text": "x" * (65 * 1024)}})
    )
    await hub.publish(
        run_kind="root",
        root_thread_id="thread-root",
        thread_id="thread-root",
        parent_thread_id=None,
        run_id="run-root",
        events=controls,
    )
    async with hub.subscribe(root_thread_id="thread-root") as subscription:
        retained = subscription.root_stream.controls
        assert len(retained) == 5
        assert {
            event.payload["value"]["run_id"]
            for event in retained
            if event.payload and event.payload.get("name") == "a13n.harness.usage"
        } == {"run-root", "inline-child"}
        assert {
            event.payload["value"]["event"]["process_id"]
            for event in retained
            if event.payload and event.payload.get("name") == "a13n.shell.status"
        } == {"process-one", "process-two"}
        assert retained[-1].payload is None and retained[-1].payload_omitted
    await hub.close()
