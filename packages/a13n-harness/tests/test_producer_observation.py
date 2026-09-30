from __future__ import annotations

import asyncio

import pytest
from a13n_harness.events import HarnessExtensionEvent, _RunEventEmitter

pytestmark = pytest.mark.anyio


async def test_extension_observation_precedes_backpressure_and_is_detached() -> None:
    captured: list[tuple[str, str, HarnessExtensionEvent]] = []
    observed = asyncio.Event()

    def observe(*, thread_id: str, run_id: str, event: HarnessExtensionEvent) -> None:
        captured.append((thread_id, run_id, event))
        if len(captured) == 2:
            observed.set()

    emitter = _RunEventEmitter("thread-a", "run-a", capacity=1, observer=observe)
    first = HarnessExtensionEvent(kind="diagnostic", payload={"message": "first"})
    await emitter.emit(first)
    emitter.start_consuming()
    pending = asyncio.create_task(emitter.emit(HarnessExtensionEvent(kind="diagnostic", payload={"message": "second"})))
    try:
        await asyncio.wait_for(observed.wait(), 2)
        assert not pending.done()
        assert len(captured) == 2
        assert captured[0][:2] == ("thread-a", "run-a")
        captured[0][2].payload["message"] = "mutated"
        assert (await emitter.next()).payload == {"message": "first"}
        await asyncio.wait_for(pending, 2)
        assert (await emitter.next()).payload == {"message": "second"}
        assert len(captured) == 2
    finally:
        emitter.close()
        await asyncio.gather(pending, return_exceptions=True)


async def test_observer_failure_does_not_publish_an_unobserved_extension() -> None:
    def observe(*, thread_id: str, run_id: str, event: HarnessExtensionEvent) -> None:
        raise ValueError("capture rejected")

    emitter = _RunEventEmitter("thread-a", "run-a", observer=observe)
    with pytest.raises(ValueError, match="capture rejected"):
        await emitter.emit(HarnessExtensionEvent(kind="diagnostic", payload={"message": "test"}))
    assert emitter.empty()
    emitter.close()


async def test_forwarding_does_not_repeat_child_producer_observation() -> None:
    from datetime import UTC, datetime

    from a13n_harness.events import HarnessEvent

    captured: list[str] = []

    def observe(*, thread_id: str, run_id: str, event: HarnessExtensionEvent) -> None:
        captured.append(run_id)

    parent = _RunEventEmitter("thread-parent", "run-parent", observer=observe)
    child = _RunEventEmitter("thread-child", "run-child", observer=observe)
    forwarding = parent.bind_child(child)
    extension = HarnessExtensionEvent(kind="diagnostic", payload={"message": "child"})
    await child.emit(extension)
    event = HarnessEvent(
        thread_id=child.thread_id,
        run_id=child.run_id,
        sequence=0,
        occurred_at=datetime.now(UTC),
        event=await child.next(),
    )
    await forwarding.forward(event)
    assert captured == ["run-child"]
    assert (await parent.next()).run_id == "run-child"
    forwarding.close()
    parent.close()
    child.close()
