"""Canonical presentation envelopes derived from committed lifecycle facts."""

from a13n_service.interactions.domain import RunPayloadObjectRef
from a13n_service.lifecycle import LifecycleEvent
from a13n_service.lifecycle.domain import LIFECYCLE_EVENT_TYPES
from a13n_service.lifecycle.projections import public_lifecycle_payload, public_output_reference

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


def public_stream_event(event: RunStreamEvent) -> RunStreamEvent:
    """Project at delivery so live and previously retained events share the boundary."""
    if event.event_type not in LIFECYCLE_EVENT_TYPES:
        return event
    payload = dict(event.payload)
    data = payload.get("data")
    if isinstance(data, dict):
        payload["data"] = public_lifecycle_payload(event.event_type, data)
    if payload.get("actor_type") == "worker":
        payload["actor_id"] = None
    content = payload.get("content")
    if event.event_type == "run.completed" and payload.get("item_kind") == "run_output" and isinstance(content, dict):
        payload["content"] = public_output_reference(content)
    return event.model_copy(update={"payload": payload})
