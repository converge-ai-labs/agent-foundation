"""Public projections of retained lifecycle facts, including historical records."""

from .domain import JsonObject, LifecycleEvent


def public_output_reference(reference: JsonObject) -> JsonObject:
    """Keep non-secret output metadata without its storage locator."""
    return {
        key: reference[key]
        for key in ("digest_sha256", "size_bytes", "content_type", "schema_version")
        if key in reference
    }


def public_lifecycle_payload(event_type: str, payload: JsonObject) -> JsonObject:
    result = dict(payload)
    if event_type.startswith("run_attempt."):
        result.pop("worker_id", None)
    if event_type == "run.completed":
        reference = result.get("output_object")
        if isinstance(reference, dict):
            result["output_object"] = public_output_reference(reference)
    return result


def public_lifecycle_event(event: LifecycleEvent) -> LifecycleEvent:
    return event.model_copy(
        update={
            "actor_id": None if event.actor_type == "worker" else event.actor_id,
            "payload": public_lifecycle_payload(event.event_type, event.payload),
        }
    )
