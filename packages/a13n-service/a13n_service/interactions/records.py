"""Canonical domain-to-relational interaction record construction."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import JSON

from .domain import Run, RunAttempt, Session, Thread
from .models import RunAttemptRecord, RunRecord, SessionRecord, ThreadRecord


def session_record(value: Session) -> SessionRecord:
    return SessionRecord(
        id=value.id,
        organization_id=value.organization_id,
        workspace_id=value.workspace_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def thread_record(value: Thread) -> ThreadRecord:
    return ThreadRecord(
        id=value.id,
        version=value.version,
        queue_version=value.queue_version,
        organization_id=value.organization_id,
        session_id=value.session_id,
        role=value.role.value,
        origin_kind=value.origin_kind.value,
        origin_thread_id=value.origin_thread_id,
        origin_run_id=value.origin_run_id,
        head_run_id=value.head_run_id,
        current_run_id=value.current_run_id,
        default_environment_id=value.default_environment_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def run_record(value: Run) -> RunRecord:
    input_object = _fields(value.input_object)
    output_object = _fields(value.output_object)
    sealed = _fields(value.sealed_state)
    input_inline = "input" in value.model_fields_set
    output_inline = "output" in value.model_fields_set
    return RunRecord(
        id=value.id,
        version=value.version,
        organization_id=value.organization_id,
        authority_principal_type=value.authority_principal.principal_type.value,
        authority_principal_id=value.authority_principal.principal_id,
        session_id=value.session_id,
        thread_id=value.thread_id,
        parent_run_id=value.parent_run_id,
        retry_of_run_id=value.retry_of_run_id,
        lineage_kind=value.lineage_kind.value,
        trigger_type=value.trigger_type,
        trigger_entity_type=value.trigger_entity_type,
        trigger_entity_id=value.trigger_entity_id,
        parent_agent_instance_id=value.parent_agent_instance_id,
        delegation_id=value.delegation_id,
        parent_tool_call_id=value.parent_tool_call_id,
        agent_id=value.agent_id,
        agent_revision_id=value.agent_revision_id,
        effective_agent_config_digest=value.effective_agent_config_digest,
        environment_id=value.environment_id,
        environment_access=value.environment_access,
        environment_use_started_at=value.environment_use_started_at,
        runtime_lock_digest=value.runtime_lock_digest,
        model_execution_observation_json=_json(value.model_execution_observation),
        connector_connection_selections_json=list(value.connector_connection_selections),
        mcp_connection_selections_json=list(value.mcp_connection_selections),
        native_tool_contexts_json=list(value.native_tool_contexts),
        priority=value.priority,
        queue_name=value.queue_name,
        available_at=value.available_at,
        current_run_attempt_id=value.current_run_attempt_id,
        execution_policy_version=value.execution_budget.policy_version,
        max_attempts=value.execution_budget.max_attempts,
        max_handoffs=value.execution_budget.max_handoffs,
        execution_deadline_at=value.execution_budget.execution_deadline_at,
        max_usage_json=_optional_json(value.execution_budget.max_usage),
        attempts_started=value.attempts_started,
        attempts_charged=value.attempts_charged,
        handoffs_completed=value.handoffs_completed,
        usage_charged_json=_json(value.usage_charged),
        idempotency_key=value.idempotency_key,
        request_fingerprint=value.request_fingerprint,
        status=value.status.value,
        wait_reason=None if value.wait_reason is None else value.wait_reason.value,
        pending_json=_optional_json(value.pending),
        input_kind=value.input_kind.value,
        input_json=_inline_json(value.input) if input_inline else None,
        input_object_key=input_object.get("object_key"),
        input_object_digest_sha256=input_object.get("digest_sha256"),
        input_object_size_bytes=input_object.get("size_bytes"),
        input_object_content_type=input_object.get("content_type"),
        input_object_schema_version=input_object.get("schema_version"),
        input_text=value.input_text,
        output_json=_inline_json(value.output) if output_inline else None,
        output_object_key=output_object.get("object_key"),
        output_object_digest_sha256=output_object.get("digest_sha256"),
        output_object_size_bytes=output_object.get("size_bytes"),
        output_object_content_type=output_object.get("content_type"),
        output_object_schema_version=output_object.get("schema_version"),
        output_text=value.output_text,
        failure_json=_optional_json(value.failure),
        sealed_state_digest_sha256=sealed.get("digest_sha256"),
        sealed_state_size_bytes=sealed.get("size_bytes"),
        sealed_state_content_type=sealed.get("content_type"),
        sealed_state_envelope_schema_version=sealed.get("envelope_schema_version"),
        sealed_state_harness_schema_version=sealed.get("harness_schema_version"),
        sealed_state_checkpoint_seq=sealed.get("checkpoint_seq"),
        sealed_state_committed_by_run_attempt_id=sealed.get("committed_by_run_attempt_id"),
        created_at=value.created_at,
        updated_at=value.updated_at,
        started_at=value.started_at,
        waiting_at=value.waiting_at,
        completed_at=value.completed_at,
        sealed_at=value.sealed_at,
    )


def run_attempt_record(value: RunAttempt) -> RunAttemptRecord:
    return RunAttemptRecord(
        id=value.id,
        version=value.version,
        organization_id=value.organization_id,
        run_id=value.run_id,
        attempt_number=value.attempt_number,
        status=value.status.value,
        replaces_run_attempt_id=value.replaces_run_attempt_id,
        start_reason=value.start_reason,
        worker_id=value.worker_id,
        worker_build_id=value.worker_build_id,
        runtime_lock_digest=value.runtime_lock_digest,
        harness_run_id=value.harness_run_id,
        model_execution_observation_json=_json(value.model_execution_observation),
        lease_token_digest=value.lease_token_digest,
        lease_expires_at=value.lease_expires_at,
        heartbeat_at=value.heartbeat_at,
        usage_json=_json(value.usage),
        yield_reason=None if value.yield_reason is None else value.yield_reason.value,
        failure_json=_optional_json(value.failure),
        created_at=value.created_at,
        started_at=value.started_at,
        finished_at=value.finished_at,
        updated_at=value.updated_at,
    )


def _fields(value: BaseModel | None) -> dict[str, Any]:
    return {} if value is None else value.model_dump(mode="json", by_alias=True)


def _json(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True)


def _optional_json(value: BaseModel | None) -> dict[str, Any] | None:
    return None if value is None else _json(value)


def _inline_json(value: Any) -> Any:
    return JSON.NULL if value is None else value


__all__ = ["run_attempt_record", "run_record", "session_record", "thread_record"]
