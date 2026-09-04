"""Pure preparation of an automatic asynchronous-result continuation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from a13n_service.agents.domain import canonical_digest
from a13n_service.interactions.control_domain import ThreadInboxEntry, ThreadInboxKind, ThreadInboxStatus
from a13n_service.interactions.domain import (
    RecoveryUsage,
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
)
from a13n_service.interactions.initialization import RunStateSeed, initialize_completed_continuation_state
from a13n_service.interactions.state import RunStateEnvelope

from .result_payload import parse_async_subagent_result_entry


@dataclass(frozen=True, slots=True)
class PreparedAsyncResultSuccessor:
    """Complete state-first candidate for one consumed result entry."""

    inbox_entry_id: str
    run: Run
    state: RunStateEnvelope


def prepare_async_result_successor(
    *,
    selected_parent: Run,
    selected_parent_state: RunStateEnvelope,
    origin_run: Run,
    inbox_entry: ThreadInboxEntry,
    successor_run_id: str,
    created_at: datetime,
) -> PreparedAsyncResultSuccessor:
    """Construct a continuation using only frozen Run and inbox authority."""

    _validate_authority(
        selected_parent=selected_parent,
        selected_parent_state=selected_parent_state,
        origin_run=origin_run,
        inbox_entry=inbox_entry,
    )
    payload = parse_async_subagent_result_entry(inbox_entry)
    accepted_input = payload.as_json()
    config = selected_parent_state.effective_agent_config
    state = initialize_completed_continuation_state(
        RunStateSeed(
            run_id=successor_run_id,
            agent_id=selected_parent.agent_id,
            agent_revision_id=selected_parent.agent_revision_id,
            effective_agent_config=config,
        ),
        selected_parent_state,
    )
    request_fingerprint = canonical_digest(
        {
            "schema_version": "1",
            "inbox_entry_id": inbox_entry.id,
            "origin_run_id": origin_run.id,
            "parent_run_id": selected_parent.id,
            "input": accepted_input,
        }
    )
    run = Run(
        id=successor_run_id,
        version=1,
        tenant_id=selected_parent.tenant_id,
        authority_principal=origin_run.authority_principal,
        session_id=selected_parent.session_id,
        thread_id=selected_parent.thread_id,
        parent_run_id=selected_parent.id,
        lineage_kind=RunLineageKind.continue_,
        trigger_type="async_subagent_result",
        trigger_entity_type="thread_inbox",
        trigger_entity_id=inbox_entry.id,
        agent_id=selected_parent.agent_id,
        agent_revision_id=selected_parent.agent_revision_id,
        effective_agent_config_digest=config.content_digest,
        encrypted_config_payload=selected_parent.encrypted_config_payload,
        runtime_lock_digest=selected_parent.runtime_lock_digest,
        model_execution_observation=selected_parent.model_execution_observation,
        connector_connection_selections=selected_parent.connector_connection_selections,
        mcp_connection_selections=selected_parent.mcp_connection_selections,
        native_tool_contexts=selected_parent.native_tool_contexts,
        priority=selected_parent.priority,
        queue_name=selected_parent.queue_name,
        available_at=created_at,
        next_attempt_fence=1,
        recovery_budget=selected_parent.recovery_budget,
        attempts_started=0,
        recovery_attempts_started=0,
        handoffs_completed=0,
        usage_charged=RecoveryUsage(),
        idempotency_key=f"async-result:{inbox_entry.id}",
        request_fingerprint=request_fingerprint,
        status=RunStatus.accepted,
        input_kind=RunInputKind.async_subagent_result,
        input=accepted_input,
        created_at=created_at,
        updated_at=created_at,
    )
    return PreparedAsyncResultSuccessor(
        inbox_entry_id=inbox_entry.id,
        run=run,
        state=state,
    )


def _validate_authority(
    *,
    selected_parent: Run,
    selected_parent_state: RunStateEnvelope,
    origin_run: Run,
    inbox_entry: ThreadInboxEntry,
) -> None:
    if selected_parent.status is not RunStatus.completed:
        raise ValueError("automatic result successor requires a completed selected parent")
    if (
        selected_parent_state.run_id != selected_parent.id
        or selected_parent_state.thread_id != selected_parent.thread_id
        or selected_parent_state.checkpoint_kind != "completed"
        or selected_parent_state.agent_id != selected_parent.agent_id
        or selected_parent_state.agent_revision_id != selected_parent.agent_revision_id
        or selected_parent_state.effective_agent_config.content_digest != selected_parent.effective_agent_config_digest
        or selected_parent_state.runtime_lock_digest != selected_parent.runtime_lock_digest
    ):
        raise ValueError("selected parent state does not match its sealed Run")
    if (
        origin_run.tenant_id != selected_parent.tenant_id
        or origin_run.session_id != selected_parent.session_id
        or origin_run.thread_id != selected_parent.thread_id
        or origin_run.status in {RunStatus.failed, RunStatus.cancelled}
    ):
        raise ValueError("asynchronous result origin is not eligible for continuation")
    if (
        inbox_entry.kind is not ThreadInboxKind.async_subagent_result
        or inbox_entry.status is not ThreadInboxStatus.pending
        or inbox_entry.tenant_id != selected_parent.tenant_id
        or inbox_entry.thread_id != selected_parent.thread_id
        or inbox_entry.origin_run_id != origin_run.id
        or inbox_entry.target_run_id is not None
        or inbox_entry.source_waiting_run_id is not None
    ):
        raise ValueError("automatic result successor requires an exact unbound inbox entry")


__all__ = [
    "PreparedAsyncResultSuccessor",
    "prepare_async_result_successor",
]
