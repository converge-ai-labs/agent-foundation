from __future__ import annotations

import pytest
from a13n_ui.errors import LivePresentationError
from a13n_ui.live import AgentUiLiveHub, AgentUiSummaryHub, LiveCursor, SummaryCursor
from ag_ui.core.events import TextMessageContentEvent

pytestmark = pytest.mark.anyio


def _text_event(value: str) -> TextMessageContentEvent:
    return TextMessageContentEvent(timestamp=1, message_id="message-1", delta=value)


async def test_live_hub_cutover_follows_complete_root_lineage_and_detaches_events() -> None:
    hub = AgentUiLiveHub(epoch="live-test", ring_size=4, subscriber_buffer_size=2)
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
    hub = AgentUiLiveHub(epoch="live-test", ring_size=2, subscriber_buffer_size=1)

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
    hub = AgentUiLiveHub()
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
    hub = AgentUiSummaryHub(epoch="live-test", ring_size=4, subscriber_buffer_size=2)
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
