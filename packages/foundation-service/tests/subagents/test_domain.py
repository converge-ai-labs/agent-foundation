from datetime import UTC, datetime

import pytest
from a13n_service.subagents import (
    AsyncSubagentResultInboxPayload,
    ChildCancellationPolicy,
    ChildResultVisibility,
    ChildRunRelationship,
    new_child_run_relationship_id,
)
from a13n_service.subagents.records import child_run_relationship_record
from pydantic import ValidationError

NOW = datetime(2026, 9, 3, 16, tzinfo=UTC)


def relationship() -> ChildRunRelationship:
    return ChildRunRelationship(
        id="crr_1234567890abcdef",
        parent_run_id="run_1234567890abcdef",
        parent_run_attempt_id="rat_1234567890abcdef",
        parent_run_attempt_generation=3,
        subagent_name="researcher",
        child_run_id="run_abcdef1234567890",
        child_thread_id="thread-abcdef1234567890abcdef1234567890",
        spawn_operation_id="call-research-1",
        cancellation_policy=ChildCancellationPolicy.independent,
        result_visibility=ChildResultVisibility.parent_thread,
        created_at=NOW,
    )


def test_relationship_record_round_trips_the_durable_contract() -> None:
    value = relationship()

    record = child_run_relationship_record(value, tenant_id="org_1234567890abcdef")

    assert record.tenant_id == "org_1234567890abcdef"
    assert record.to_resource() == value


def test_relationship_rejects_parent_as_its_own_child() -> None:
    with pytest.raises(ValidationError, match="distinct"):
        ChildRunRelationship.model_validate(
            {
                **relationship().model_dump(mode="python"),
                "child_run_id": "run_1234567890abcdef",
            }
        )


def test_result_payload_is_bounded_and_canonical() -> None:
    payload = AsyncSubagentResultInboxPayload(
        relationship_id="crr_1234567890abcdef",
        subagent_name="researcher",
        child_thread_id="thread-abcdef1234567890abcdef1234567890",
        child_run_id="run_abcdef1234567890",
        terminal_status="completed",
        result_payload={"answer": "done"},
        result_digest="a" * 64,
    )

    assert payload.canonical_bytes().startswith(b'{"child_run_id"')
    with pytest.raises(ValidationError, match="inline limit"):
        AsyncSubagentResultInboxPayload.model_validate(
            {
                **payload.model_dump(mode="python"),
                "result_payload": "x" * (256 * 1024),
            }
        )


def test_relationship_id_uses_the_assigned_kind_prefix() -> None:
    assert new_child_run_relationship_id().startswith("crr_")
