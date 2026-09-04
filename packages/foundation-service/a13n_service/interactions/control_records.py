"""Canonical control-domain to relational record construction."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON

from .control_domain import QueuedSubmission, ThreadInboxEntry
from .control_models import QueuedSubmissionRecord, ThreadInboxCounterRecord, ThreadInboxRecord
from .domain import Thread


def inbox_counter_record(thread: Thread) -> ThreadInboxCounterRecord:
    return ThreadInboxCounterRecord(
        tenant_id=thread.tenant_id,
        thread_id=thread.id,
        next_delivery_sequence=1,
        pending_count=0,
        pending_bytes=0,
    )


def thread_inbox_record(value: ThreadInboxEntry) -> ThreadInboxRecord:
    object_ref = value.payload_object
    inline = "payload" in value.model_fields_set
    return ThreadInboxRecord(
        id=value.id,
        tenant_id=value.tenant_id,
        thread_id=value.thread_id,
        kind=value.kind.value,
        delivery_sequence=value.delivery_sequence,
        accepted_against_run_id=value.accepted_against_run_id,
        target_run_id=value.target_run_id,
        source_waiting_run_id=value.source_waiting_run_id,
        origin_run_id=value.origin_run_id,
        async_subagent_relationship_id=value.async_subagent_relationship_id,
        payload_schema_version=value.payload_schema_version,
        payload_json=_inline_json(value.payload) if inline else None,
        payload_object_key=None if object_ref is None else object_ref.object_key,
        payload_object_digest_sha256=None if object_ref is None else object_ref.digest_sha256,
        payload_object_size_bytes=None if object_ref is None else object_ref.size_bytes,
        payload_object_content_type=None if object_ref is None else object_ref.content_type,
        payload_object_schema_version=None if object_ref is None else object_ref.schema_version,
        status=value.status.value,
        consumed_by_run_id=value.consumed_by_run_id,
        consumed_state_digest_sha256=value.consumed_state_digest_sha256,
        consumed_checkpoint_seq=value.consumed_checkpoint_seq,
        expires_at=value.expires_at,
        created_at=value.created_at,
        finalized_at=value.finalized_at,
    )


def queued_submission_record(value: QueuedSubmission, *, tenant_id: str) -> QueuedSubmissionRecord:
    return QueuedSubmissionRecord(
        id=value.queued_submission_id,
        version=value.version,
        tenant_id=tenant_id,
        thread_id=value.thread_id,
        authority_principal_type=value.authority_principal.principal_type.value,
        authority_principal_id=value.authority_principal.principal_id,
        position=value.position,
        submission_json=value.submission.model_dump(mode="json", by_alias=True, exclude_none=True),
        submission_digest_sha256=value.submission_digest_sha256,
        consumed_run_id=value.consumed_run_id,
        consumed_at=value.consumed_at,
        failure_json=None if value.failure is None else value.failure.model_dump(mode="json"),
        failed_at=value.failed_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _inline_json(value: Any) -> Any:
    return JSON.NULL if value is None else value


__all__ = ["inbox_counter_record", "queued_submission_record", "thread_inbox_record"]
