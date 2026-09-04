"""Canonical asynchronous child domain-to-relational construction."""

from .domain import ChildRunRelationship
from .models import ChildRunRelationshipRecord


def child_run_relationship_record(
    value: ChildRunRelationship,
    *,
    tenant_id: str,
) -> ChildRunRelationshipRecord:
    return ChildRunRelationshipRecord(
        id=value.id,
        tenant_id=tenant_id,
        parent_run_id=value.parent_run_id,
        parent_run_attempt_id=value.parent_run_attempt_id,
        parent_run_attempt_generation=value.parent_run_attempt_generation,
        subagent_name=value.subagent_name,
        child_run_id=value.child_run_id,
        child_thread_id=value.child_thread_id,
        cancellation_policy=value.cancellation_policy.value,
        result_visibility=value.result_visibility.value,
        created_at=value.created_at,
    )


__all__ = ["child_run_relationship_record"]
