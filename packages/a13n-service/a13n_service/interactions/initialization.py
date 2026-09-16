"""Pure state initialization for accepted Run lineage operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from a13n_harness import HarnessState
from a13n_harness.usage import intersect_usage_limits
from pydantic_ai.usage import UsageLimits

from a13n_service.agent_configuration.context import ConfigurationRunContext
from a13n_service.agents.domain import EffectiveAgentConfig, PreparedAgentPlugins
from a13n_service.agents.invocation_resolution import FrozenAgentInvocation
from a13n_service.digests import digest_request
from a13n_service.iam import PrincipalRef
from a13n_service.models.domain import ModelExecutionObservation
from a13n_service.secrets.domain import AgentSecretBinding

from .domain import (
    ExecutionBudget,
    JsonObject,
    ObjectId,
    Run,
    RunInputKind,
    RunLineageKind,
    StrictModel,
    ThreadId,
    accepted_run,
)
from .input import AcceptedAgentInput, input_text
from .origin import SubmissionOrigin
from .protocol_context import ProtocolInputContext
from .state import HostContinuationState, RunCheckpoint


class RunStateSeed(StrictModel):
    run_id: ObjectId
    agent_id: ObjectId
    agent_revision_id: ObjectId | None
    effective_agent_config: EffectiveAgentConfig
    prepared_plugins: PreparedAgentPlugins | None = None
    usage_limits: UsageLimits | None = None
    protocol_context: ProtocolInputContext | None = None
    secret_bindings: tuple[AgentSecretBinding, ...] = ()

    @classmethod
    def from_invocation(
        cls,
        *,
        run_id: str,
        invocation: FrozenAgentInvocation,
        input: AcceptedAgentInput,
        protocol_context: ProtocolInputContext | None = None,
    ) -> RunStateSeed:
        return cls(
            run_id=run_id,
            agent_id=invocation.agent_id,
            agent_revision_id=invocation.agent_revision_id,
            effective_agent_config=invocation.effective_config,
            protocol_context=protocol_context,
            secret_bindings=input.secret_bindings,
        )


def initialize_start_state(seed: RunStateSeed, *, thread_id: ThreadId) -> RunCheckpoint:
    return _initial_envelope(seed, HarnessState.new(thread_id=thread_id), HostContinuationState())


def initialize_empty_thread_state(seed: RunStateSeed, *, thread_id: ThreadId) -> RunCheckpoint:
    return _initial_envelope(seed, HarnessState.new(thread_id=thread_id), HostContinuationState())


def initialize_completed_continuation_state(
    seed: RunStateSeed,
    parent: RunCheckpoint,
) -> RunCheckpoint:
    _require_parent(parent, checkpoint_kind="completed")
    return _initial_envelope(
        _retain_state_configuration(seed, parent), _clone_harness(parent.harness), HostContinuationState()
    )


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
    return _initial_envelope(_retain_state_configuration(seed, parent), _clone_harness(parent.harness), host)


def initialize_fork_state(
    seed: RunStateSeed,
    parent: RunCheckpoint,
    *,
    thread_id: ThreadId,
) -> RunCheckpoint:
    _require_parent(parent, checkpoint_kind="completed")
    return _initial_envelope(
        _retain_state_configuration(seed, parent), parent.harness.fork(thread_id=thread_id), HostContinuationState()
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
        return _initial_envelope(_retain_state_configuration(seed, parent), harness, HostContinuationState())
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
        prepared_plugins=seed.prepared_plugins,
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


def _retain_state_configuration(seed: RunStateSeed, parent: RunCheckpoint) -> RunStateSeed:
    prepared = seed.prepared_plugins
    if prepared is None and _same_plugin_graph(parent.effective_agent_config, seed.effective_agent_config):
        prepared = parent.prepared_plugins
    return seed.model_copy(
        update={
            "usage_limits": intersect_usage_limits(seed.usage_limits, parent.usage_limits),
            "prepared_plugins": prepared.model_copy(deep=True) if prepared is not None else None,
        }
    )


def _same_plugin_graph(previous: EffectiveAgentConfig, current: EffectiveAgentConfig) -> bool:
    return (
        tuple(map(digest_request, previous.plugins)) == tuple(map(digest_request, current.plugins))
        and previous.child_configs.keys() == current.child_configs.keys()
        and all(
            _same_plugin_graph(child.effective_config, current.child_configs[revision_id].effective_config)
            for revision_id, child in previous.child_configs.items()
        )
    )


def _clone_harness(value: HarnessState) -> HarnessState:
    return HarnessState.model_validate(value.model_dump(mode="json", by_alias=True))


class FrozenRunFields(TypedDict):
    agent_id: str
    agent_revision_id: str | None
    effective_agent_config_digest: str
    model_execution_observation: ModelExecutionObservation
    connection_selections: tuple[JsonObject, ...]


def frozen_run_fields(invocation: FrozenAgentInvocation) -> FrozenRunFields:
    """Project the same accepted configuration into every newly constructed Run."""
    config = invocation.effective_config
    return FrozenRunFields(
        agent_id=invocation.agent_id,
        agent_revision_id=invocation.agent_revision_id,
        effective_agent_config_digest=config.content_digest,
        model_execution_observation=config.resolved_model.execution.observation(),
        connection_selections=tuple(item.model_dump(mode="json") for item in invocation.connection_selections),
    )


@dataclass(frozen=True, slots=True)
class NewRunPolicy:
    """Current scheduling defaults for new input, never for inherited execution."""

    priority: int
    queue_name: str
    execution_budget: ExecutionBudget

    def create(
        self,
        *,
        now: datetime,
        id: str,
        organization_id: str,
        authority_principal: PrincipalRef,
        session_id: str,
        thread_id: str,
        parent_run_id: str | None,
        lineage_kind: RunLineageKind,
        invocation: FrozenAgentInvocation,
        input: AcceptedAgentInput,
        request_fingerprint: str,
        origin: SubmissionOrigin,
        configuration_context: ConfigurationRunContext | None = None,
    ) -> Run:
        return accepted_run(
            now=now,
            id=id,
            organization_id=organization_id,
            authority_principal=authority_principal,
            session_id=session_id,
            thread_id=thread_id,
            parent_run_id=parent_run_id,
            retry_of_run_id=None,
            lineage_kind=lineage_kind,
            trigger_type=origin.trigger_type,
            native_tool_contexts=origin.native_tool_contexts,
            configuration_context=configuration_context,
            bot_memory=origin.bot_memory,
            **frozen_run_fields(invocation),
            priority=self.priority,
            queue_name=self.queue_name,
            execution_budget=self.execution_budget,
            request_fingerprint=request_fingerprint,
            input_kind=RunInputKind.agent_input,
            input=input.model_dump(mode="json", by_alias=True, exclude_none=True),
            input_text=input_text(input),
        )


__all__ = [
    "NewRunPolicy",
    "RunStateSeed",
    "frozen_run_fields",
    "initialize_completed_continuation_state",
    "initialize_empty_thread_state",
    "initialize_fork_state",
    "initialize_retry_state",
    "initialize_start_state",
    "initialize_waiting_continuation_state",
]
