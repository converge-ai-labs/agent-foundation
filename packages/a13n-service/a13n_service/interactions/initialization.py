"""Pure state initialization for accepted Run lineage operations."""

from __future__ import annotations

from typing import TypedDict

from a13n_harness import HarnessState
from a13n_harness.usage import intersect_usage_limits
from pydantic_ai.usage import UsageLimits

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.invocation_resolution import FrozenAgentInvocation
from a13n_service.models.domain import ModelExecutionObservation
from a13n_service.secrets.domain import AgentSecretBinding

from .domain import JsonObject, ObjectId, RunInputKind, RunLineageKind, StrictModel, ThreadId
from .protocol_context import ProtocolInputContext
from .state import HostContinuationState, RunCheckpoint


class RunStateSeed(StrictModel):
    run_id: ObjectId
    agent_id: ObjectId
    agent_revision_id: ObjectId
    effective_agent_config: EffectiveAgentConfig
    usage_limits: UsageLimits | None = None
    protocol_context: ProtocolInputContext | None = None
    secret_bindings: tuple[AgentSecretBinding, ...] = ()


def initialize_start_state(seed: RunStateSeed, *, thread_id: ThreadId) -> RunCheckpoint:
    return _initial_envelope(seed, HarnessState.new(thread_id=thread_id), HostContinuationState())


def initialize_empty_thread_state(seed: RunStateSeed, *, thread_id: ThreadId) -> RunCheckpoint:
    return _initial_envelope(seed, HarnessState.new(thread_id=thread_id), HostContinuationState())


def initialize_completed_continuation_state(
    seed: RunStateSeed,
    parent: RunCheckpoint,
) -> RunCheckpoint:
    _require_parent(parent, checkpoint_kind="completed")
    return _initial_envelope(_retain_limits(seed, parent), _clone_harness(parent.harness), HostContinuationState())


def initialize_waiting_continuation_state(
    seed: RunStateSeed,
    parent: RunCheckpoint,
) -> RunCheckpoint:
    _require_parent(parent, checkpoint_kind="waiting")
    if parent.host.deferred is None:
        raise ValueError("waiting parent state must contain deferred continuation")
    host = HostContinuationState(
        deferred=parent.host.deferred.model_copy(deep=True),
        inbox_receipts=(),
    )
    return _initial_envelope(_retain_limits(seed, parent), _clone_harness(parent.harness), host)


def initialize_fork_state(
    seed: RunStateSeed,
    parent: RunCheckpoint,
    *,
    thread_id: ThreadId,
) -> RunCheckpoint:
    _require_parent(parent, checkpoint_kind="completed")
    return _initial_envelope(
        _retain_limits(seed, parent), parent.harness.fork(thread_id=thread_id), HostContinuationState()
    )


def initialize_retry_state(
    seed: RunStateSeed,
    *,
    thread_id: str,
    source_lineage_kind: RunLineageKind,
    source_input_kind: RunInputKind,
    parent: RunCheckpoint | None,
) -> RunCheckpoint:
    if source_lineage_kind is RunLineageKind.root:
        if parent is not None:
            raise ValueError("root retry cannot have parent state")
        return initialize_empty_thread_state(seed, thread_id=thread_id)
    if parent is None:
        raise ValueError("non-root retry requires the original state parent")
    if source_lineage_kind is RunLineageKind.fork:
        _require_parent(parent, checkpoint_kind="completed")
        harness = parent.harness.fork(thread_id=thread_id)
        return _initial_envelope(_retain_limits(seed, parent), harness, HostContinuationState())
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
) -> RunCheckpoint:
    if seed.protocol_context is not None:
        seed.protocol_context.validate_policy(seed.effective_agent_config.protocol)
    harness = HarnessState(
        schema_version=harness.schema_version,
        thread_id=harness.thread_id,
        message_history=harness.message_history,
        agent_context_state=harness.agent_context_state,
        environment_states={},
    )
    return RunCheckpoint(
        run_id=seed.run_id,
        thread_id=harness.thread_id,
        checkpoint_seq=0,
        checkpoint_kind="initial",
        last_checkpoint_run_attempt_id=None,
        last_checkpoint_fence=0,
        agent_id=seed.agent_id,
        agent_revision_id=seed.agent_revision_id,
        effective_agent_config=seed.effective_agent_config,
        protocol_context=seed.protocol_context,
        secret_bindings=seed.secret_bindings,
        usage_limits=seed.usage_limits,
        harness_schema_version=harness.schema_version,
        harness=harness,
        host=host,
        outcome_candidate=None,
    )


def _require_parent(parent: RunCheckpoint, *, checkpoint_kind: str) -> None:
    if parent.checkpoint_kind != checkpoint_kind or parent.outcome_candidate is None:
        raise ValueError(f"Run state parent must be a sealed {checkpoint_kind} candidate")


def _retain_limits(seed: RunStateSeed, parent: RunCheckpoint) -> RunStateSeed:
    return seed.model_copy(update={"usage_limits": intersect_usage_limits(seed.usage_limits, parent.usage_limits)})


def _clone_harness(value: HarnessState) -> HarnessState:
    return HarnessState.model_validate(value.model_dump(mode="json", by_alias=True))


class FrozenRunFields(TypedDict):
    agent_id: str
    agent_revision_id: str
    effective_agent_config_digest: str
    model_execution_observation: ModelExecutionObservation
    connector_connection_selections: tuple[JsonObject, ...]
    mcp_connection_selections: tuple[JsonObject, ...]


def frozen_run_fields(invocation: FrozenAgentInvocation) -> FrozenRunFields:
    """Project the same accepted configuration into every newly constructed Run."""
    config = invocation.effective_config
    return FrozenRunFields(
        agent_id=invocation.agent_id,
        agent_revision_id=invocation.agent_revision_id,
        effective_agent_config_digest=config.content_digest,
        model_execution_observation=config.resolved_model.execution.observation(),
        connector_connection_selections=tuple(
            item.model_dump(mode="json") for item in invocation.connector_connection_selections
        ),
        mcp_connection_selections=tuple(item.model_dump(mode="json") for item in invocation.mcp_connection_selections),
    )


__all__ = [
    "RunStateSeed",
    "frozen_run_fields",
    "initialize_completed_continuation_state",
    "initialize_empty_thread_state",
    "initialize_fork_state",
    "initialize_retry_state",
    "initialize_start_state",
    "initialize_waiting_continuation_state",
]
