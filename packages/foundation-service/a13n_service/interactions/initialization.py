"""Pure state initialization for accepted Run lineage operations."""

from __future__ import annotations

from a13n_harness import HarnessState

from a13n_service.agents.domain import EffectiveAgentConfig

from .domain import ObjectId, RunInputKind, RunLineageKind, StrictModel, ThreadId
from .state import HostContinuationState, RunStateEnvelope


class RunStateSeed(StrictModel):
    run_id: ObjectId
    agent_id: ObjectId
    agent_revision_id: ObjectId
    effective_agent_config: EffectiveAgentConfig


def initialize_start_state(seed: RunStateSeed) -> RunStateEnvelope:
    return _initial_envelope(seed, HarnessState.new(), HostContinuationState())


def initialize_empty_thread_state(seed: RunStateSeed, *, thread_id: str) -> RunStateEnvelope:
    harness = HarnessState(
        schema_version="1",
        thread_id=thread_id,
        message_history=(),
        environment_states={},
    )
    return _initial_envelope(seed, harness, HostContinuationState())


def initialize_completed_continuation_state(
    seed: RunStateSeed,
    parent: RunStateEnvelope,
) -> RunStateEnvelope:
    _require_parent(parent, checkpoint_kind="completed")
    return _initial_envelope(seed, _clone_harness(parent.harness), HostContinuationState())


def initialize_waiting_continuation_state(
    seed: RunStateSeed,
    parent: RunStateEnvelope,
) -> RunStateEnvelope:
    _require_parent(parent, checkpoint_kind="waiting")
    if parent.host.deferred is None:
        raise ValueError("waiting parent state must contain deferred continuation")
    host = HostContinuationState(
        deferred=parent.host.deferred.model_copy(deep=True),
        consumed_inbox_entries=(),
    )
    return _initial_envelope(seed, _clone_harness(parent.harness), host)


def initialize_fork_state(seed: RunStateSeed, parent: RunStateEnvelope) -> RunStateEnvelope:
    _require_parent(parent, checkpoint_kind="completed")
    return _initial_envelope(seed, parent.harness.fork(), HostContinuationState())


def initialize_retry_state(
    seed: RunStateSeed,
    *,
    thread_id: str,
    source_lineage_kind: RunLineageKind,
    source_input_kind: RunInputKind,
    parent: RunStateEnvelope | None,
) -> RunStateEnvelope:
    if source_lineage_kind is RunLineageKind.root:
        if parent is not None:
            raise ValueError("root retry cannot have parent state")
        return initialize_empty_thread_state(seed, thread_id=thread_id)
    if parent is None:
        raise ValueError("non-root retry requires the original state parent")
    if source_lineage_kind is RunLineageKind.fork:
        _require_parent(parent, checkpoint_kind="completed")
        harness = _fork_harness_for_existing_thread(parent.harness, thread_id=thread_id)
        return _initial_envelope(seed, harness, HostContinuationState())
    if source_input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}:
        state = initialize_waiting_continuation_state(seed, parent)
    else:
        state = initialize_completed_continuation_state(seed, parent)
    if state.thread_id != thread_id:
        raise ValueError("retry parent state does not preserve the source Thread identity")
    return state


def _initial_envelope(
    seed: RunStateSeed,
    harness: HarnessState,
    host: HostContinuationState,
) -> RunStateEnvelope:
    harness = HarnessState(
        schema_version=harness.schema_version,
        thread_id=harness.thread_id,
        message_history=harness.message_history,
        agent_context_state=harness.agent_context_state,
        environment_states={},
    )
    return RunStateEnvelope(
        run_id=seed.run_id,
        thread_id=harness.thread_id,
        checkpoint_seq=0,
        checkpoint_kind="initial",
        input_disposition="pending",
        last_checkpoint_run_attempt_id=None,
        last_checkpoint_fence=0,
        agent_id=seed.agent_id,
        agent_revision_id=seed.agent_revision_id,
        effective_agent_config=seed.effective_agent_config,
        runtime_lock_digest=seed.effective_agent_config.runtime_lock_digest,
        harness_schema_version=harness.schema_version,
        harness=harness,
        host=host,
        outcome_candidate=None,
    )


def _require_parent(parent: RunStateEnvelope, *, checkpoint_kind: str) -> None:
    if parent.checkpoint_kind != checkpoint_kind or parent.outcome_candidate is None:
        raise ValueError(f"Run state parent must be a sealed {checkpoint_kind} candidate")


def _clone_harness(value: HarnessState) -> HarnessState:
    return HarnessState.model_validate(value.model_dump(mode="json", by_alias=True))


def _fork_harness_for_existing_thread(value: HarnessState, *, thread_id: ThreadId) -> HarnessState:
    return HarnessState(
        schema_version="1",
        thread_id=thread_id,
        message_history=value.message_history,
        agent_context_state=value.agent_context_state,
        environment_states={},
    )


__all__ = [
    "RunStateSeed",
    "initialize_completed_continuation_state",
    "initialize_empty_thread_state",
    "initialize_fork_state",
    "initialize_retry_state",
    "initialize_start_state",
    "initialize_waiting_continuation_state",
]
