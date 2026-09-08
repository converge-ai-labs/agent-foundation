"""Pure preparation of an automatic asynchronous-result continuation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from a13n_service.digests import digest_request
from a13n_service.interactions.control_domain import ThreadInboxEntry, ThreadInboxKind, ThreadInboxStatus
from a13n_service.interactions.domain import (
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
    accepted_run,
)
from a13n_service.interactions.initialization import RunStateSeed, initialize_completed_continuation_state
from a13n_service.interactions.state import RunCheckpoint

from .result_payload import parse_async_subagent_result_entry


@dataclass(frozen=True, slots=True)
class PreparedAsyncResultSuccessor:
    """Complete state-first candidate for one consumed result entry."""

    inbox_entry_id: str
    run: Run
    state: RunCheckpoint


def prepare_async_result_successor(
    *,
    selected_parent: Run,
    selected_parent_state: RunCheckpoint,
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
            protocol_context=selected_parent_state.protocol_context,
            secret_bindings=selected_parent_state.secret_bindings,
        ),
        selected_parent_state,
    )
    request_fingerprint = digest_request(
        {
            "schema_version": "1",
            "inbox_entry_id": inbox_entry.id,
            "origin_run_id": origin_run.id,
            "parent_run_id": selected_parent.id,
            "input": accepted_input,
        }
    )
    run = accepted_run(
        now=created_at,
        id=successor_run_id,
        organization_id=selected_parent.organization_id,
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
        runtime_lock_digest=selected_parent.runtime_lock_digest,
        model_execution_observation=selected_parent.model_execution_observation,
        connector_connection_selections=selected_parent.connector_connection_selections,
        mcp_connection_selections=selected_parent.mcp_connection_selections,
        native_tool_contexts=selected_parent.native_tool_contexts,
        priority=selected_parent.priority,
        queue_name=selected_parent.queue_name,
        execution_budget=selected_parent.execution_budget,
        idempotency_key=f"async-result:{inbox_entry.id}",
        request_fingerprint=request_fingerprint,
        input_kind=RunInputKind.async_subagent_result,
        input=accepted_input,
    )
    return PreparedAsyncResultSuccessor(
        inbox_entry_id=inbox_entry.id,
        run=run,
        state=state,
    )


def _validate_authority(
    *,
    selected_parent: Run,
    selected_parent_state: RunCheckpoint,
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
        origin_run.organization_id != selected_parent.organization_id
        or origin_run.session_id != selected_parent.session_id
        or origin_run.thread_id != selected_parent.thread_id
        or origin_run.status in {RunStatus.failed, RunStatus.cancelled}
    ):
        raise ValueError("asynchronous result origin is not eligible for continuation")
    if (
        inbox_entry.kind is not ThreadInboxKind.async_subagent_result
        or inbox_entry.status is not ThreadInboxStatus.pending
        or inbox_entry.organization_id != selected_parent.organization_id
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
