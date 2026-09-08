from __future__ import annotations

from datetime import UTC, datetime

from a13n_harness import HarnessEvent
from a13n_stream_protocol import CustomEventAssembler, HarnessAguiObserver, fragment_custom_event
from ag_ui.core.events import CustomEvent
from pydantic_ai.messages import UnknownCapabilityEvent


def test_arbitrary_custom_payload_round_trips_without_semantic_projection() -> None:
    source = CustomEvent(name="customer.progress", value={"nested": ['\x00"\\中文\n' * 18000], "state": {"count": 4}})
    frames = fragment_custom_event(source, identity="one")
    assert len(frames) > 1
    assembler = CustomEventAssembler()
    results = []
    for frame in frames:
        assert len(frame.model_dump_json().encode()) < 64 * 1024
        result = assembler.accept(frame.model_dump(mode="json"))
        if result is not None:
            results.append(result)
    assert results == [source.model_dump(mode="json")]
    assert not assembler.gap


def test_unknown_native_capability_preserves_kind_envelope_and_nested_payload() -> None:
    event = UnknownCapabilityEvent(
        kind="customer.unknown", tool_call_id="call-one", data={"nested": {"custom": [1, "value"]}}
    )
    item = HarnessEvent(
        thread_id="thread-one", run_id="run-one", sequence=1, occurred_at=datetime.now(UTC), event=event
    )
    (projected,) = HarnessAguiObserver().observe(item)
    assert isinstance(projected, CustomEvent)
    assert projected.name == "customer.unknown"
    assert projected.value["event"]["nested"] == {"custom": [1, "value"]}
    assert projected.value["event"]["tool_call_id"] == "call-one"
    assert projected.value["run_id"] == "run-one"


def test_gaps_and_memory_limits_never_publish_partial_custom_events() -> None:
    source = CustomEvent(name="customer.large", value="x" * 100000)
    frames = fragment_custom_event(source, identity="one")
    for assembler, selected in (
        (CustomEventAssembler(), frames[1:]),
        (CustomEventAssembler(), [frames[0], *frames[2:]]),
        (CustomEventAssembler(max_bytes=1), frames),
    ):
        assert all(assembler.accept(frame.model_dump(mode="json")) is None for frame in selected)
        assert assembler.gap


def test_processor_filters_complete_custom_events_before_size_framing() -> None:
    for size in (10, 100000):
        event = UnknownCapabilityEvent(kind="customer.private", data={"body": "x" * size})
        item = HarnessEvent(
            thread_id="thread-one", run_id="run-one", sequence=1, occurred_at=datetime.now(UTC), event=event
        )
        observer = HarnessAguiObserver(
            processor=lambda source, converted: (
                None if isinstance(converted, CustomEvent) and converted.name == "customer.private" else converted
            )
        )
        assert observer.observe(item) == ()
        assert observer.snapshot() == ()


def test_interleaved_events_remain_independently_correlated() -> None:
    one = CustomEvent(name="customer.one", value="a" * 100000)
    two = CustomEvent(name="customer.two", value="b" * 100000)
    assembler = CustomEventAssembler()
    complete = []
    for pair in zip(
        fragment_custom_event(one, identity="one"), fragment_custom_event(two, identity="two"), strict=True
    ):
        for frame in pair:
            event = assembler.accept(frame.model_dump(mode="json"))
            if event is not None:
                complete.append(event)
    assert complete == [one.model_dump(mode="json"), two.model_dump(mode="json")]
