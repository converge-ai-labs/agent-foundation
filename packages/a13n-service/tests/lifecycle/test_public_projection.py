from copy import deepcopy

import pytest
from a13n_service.lifecycle.domain import LifecycleEvent
from a13n_service.lifecycle.projections import public_lifecycle_event
from a13n_service.run_stream.events import lifecycle_stream_event, public_stream_event
from tests.interactions.conftest import NOW, ORGANIZATION_ID, RUN_ID, THREAD_ID, USER_ID


def event(event_type: str = "run.completed") -> LifecycleEvent:
    return LifecycleEvent(
        seq=1,
        id="lev_1234567890abcdef",
        organization_id=ORGANIZATION_ID,
        entity_type="run",
        entity_id=RUN_ID,
        resource_seq=1,
        entity_version=1,
        event_type=event_type,
        schema_version="1",
        mutation_id="mut_1234567890abcdef",
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        payload={
            "output_object": {
                "object_key": "organizations/private/run/output.json",
                "digest_sha256": "a" * 64,
                "size_bytes": 256,
                "content_type": "application/json",
                "schema_version": "1",
            }
        },
        actor_type="worker",
        actor_id="wrk_private",
        occurred_at=NOW,
        created_at=NOW,
        hook_dispatch_state="pending",
        hook_dispatch_attempts=0,
        hook_dispatch_next_attempt_at=NOW,
        projection_state="projecting",
        projection_attempts=1,
        projection_lease_owner="wrk_projector",
        projection_lease_expires_at=NOW,
    )


def test_public_fact_and_stream_preserve_internal_storage_and_public_metadata() -> None:
    source = event()
    original = source.model_dump()
    public = public_lifecycle_event(source)
    assert public.actor_id is None
    assert public.payload["output_object"] == {
        "digest_sha256": "a" * 64,
        "size_bytes": 256,
        "content_type": "application/json",
        "schema_version": "1",
    }
    assert "projection_lease_owner" not in public.model_dump()
    assert public.projection_state == "projecting"
    assert source.projection_lease_owner == "wrk_projector"
    assert "projection_lease_owner" not in LifecycleEvent.model_json_schema(mode="serialization")["properties"]
    assert source.model_dump() == original

    retained = lifecycle_stream_event(source)
    original_stream = deepcopy(retained.model_dump())
    result = public_stream_event(retained)
    assert result.event_id == retained.event_id
    assert result.item_id == retained.item_id
    assert result.payload["actor_id"] is None
    assert result.payload["data"] == public.payload
    assert result.payload["content"] == public.payload["output_object"]
    assert retained.model_dump() == original_stream
    assert public_stream_event(result) == result


@pytest.mark.parametrize("event_type", ["run_attempt.leased", "run_attempt.running", "run_attempt.failed"])
def test_worker_instance_is_private_but_diagnostic_ids_remain(event_type: str) -> None:
    source = event(event_type).model_copy(
        update={
            "payload": {"worker_id": "wrk_private", "worker_build_id": "build-1", "harness_run_id": "harness-1"},
        }
    )
    public = public_lifecycle_event(source)
    assert public.payload == {"worker_build_id": "build-1", "harness_run_id": "harness-1"}
    assert public.actor_id is None
    stream = public_stream_event(lifecycle_stream_event(source))
    assert stream.payload["data"] == public.payload
    assert stream.payload["actor_id"] is None
    assert source.payload["worker_id"] == "wrk_private"


def test_user_identity_and_user_output_fields_are_not_redacted_by_name() -> None:
    source = event().model_copy(
        update={
            "actor_type": "user",
            "actor_id": USER_ID,
            "payload": {"output": {"worker_id": "customer-data", "object_key": "customer-data"}},
        }
    )
    result = public_lifecycle_event(source)
    assert result.actor_id == USER_ID
    assert result.payload == source.payload
