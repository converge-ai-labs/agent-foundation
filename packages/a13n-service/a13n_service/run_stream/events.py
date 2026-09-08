"""Canonical presentation envelopes derived from committed lifecycle facts."""

from a13n_service.interactions.domain import RunPayloadObjectRef
from a13n_service.lifecycle import LifecycleEvent

from .domain import RunStreamEvent, deterministic_item_id, deterministic_run_stream_event_id


def lifecycle_stream_event(event: LifecycleEvent) -> RunStreamEvent:
    if event.thread_id is None:
        raise ValueError("Run lifecycle projection requires Thread correlation")
    output = _run_output_item(event)
    payload = {
        "resource_type": event.entity_type.value,
        "resource_id": event.entity_id,
        "resource_seq": event.resource_seq,
        "resource_version": event.entity_version,
        "schema_version": event.schema_version,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "data": event.payload,
    }
    if output is not None:
        payload.update(
            item_kind="run_output",
            item_state="completed",
            content=output.model_dump(mode="json", by_alias=True),
        )

    return RunStreamEvent(
        event_id=deterministic_run_stream_event_id("lifecycle", event.id),
        event_type=event.event_type,
        run_id=event.run_id,
        thread_id=event.thread_id,
        run_attempt_id=event.run_attempt_id,
        harness_run_id=_harness_run_id(event),
        lifecycle_event_id=event.id,
        item_id=(deterministic_item_id(event.run_id, "run_output", event.run_id) if output is not None else None),
        occurred_at=event.occurred_at,
        payload=payload,
    )


def _harness_run_id(event: LifecycleEvent) -> str | None:
    if event.event_type != "run_attempt.running":
        return None
    value = event.payload.get("harness_run_id")
    if not isinstance(value, str) or not value:
        raise ValueError("RunAttempt running lifecycle event omitted Harness correlation")
    return value


def _run_output_item(event: LifecycleEvent) -> RunPayloadObjectRef | None:
    if event.event_type != "run.completed":
        return None
    output_object = event.payload.get("output_object")
    if isinstance(output_object, dict):
        return RunPayloadObjectRef.model_validate(output_object)
    return None
