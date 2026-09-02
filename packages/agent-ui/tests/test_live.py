from __future__ import annotations

import pytest
from a13n_ui.live import AgentUiLiveHub
from ag_ui.core.events import TextMessageContentEvent
from anyio import fail_after

pytestmark = pytest.mark.anyio


def _text_event(value: str) -> TextMessageContentEvent:
    return TextMessageContentEvent(timestamp=1, message_id="message-1", delta=value)


async def test_live_hub_replays_filtered_detached_events() -> None:
    hub = AgentUiLiveHub(ring_size=4, subscriber_buffer_size=2)
    await hub.publish(
        run_kind="root",
        session_id="session-1",
        thread_id="thread-1",
        run_id="run-1",
        events=(_text_event("first"),),
    )
    await hub.publish(
        run_kind="root",
        session_id="session-2",
        thread_id="thread-2",
        run_id="run-2",
        events=(_text_event("other"),),
    )

    async with hub.subscribe(session_id="session-1") as subscription:
        retained = await subscription.receive()
        assert retained.sequence == 1
        assert retained.payload is not None
        assert retained.payload["delta"] == "first"
        retained.payload["delta"] = "changed"

    snapshot = await hub.snapshot(session_id="session-1")
    assert snapshot[0].payload is not None
    assert snapshot[0].payload["delta"] == "first"
    await hub.close()


async def test_live_hub_drops_old_events_for_a_slow_subscriber() -> None:
    hub = AgentUiLiveHub(ring_size=2, subscriber_buffer_size=1)

    async with hub.subscribe(session_id="session-1") as subscription:
        for value in ("first", "second", "third"):
            await hub.publish(
                run_kind="root",
                session_id="session-1",
                thread_id="thread-1",
                run_id="run-1",
                events=(_text_event(value),),
            )
        with fail_after(1):
            newest = await subscription.receive()

    assert newest.payload is not None
    assert newest.payload["delta"] == "third"
    assert [item.sequence for item in await hub.snapshot()] == [2, 3]
    await hub.close()


async def test_live_hub_omits_oversized_payloads() -> None:
    hub = AgentUiLiveHub()
    await hub.publish(
        run_kind="child",
        session_id="session-1",
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
