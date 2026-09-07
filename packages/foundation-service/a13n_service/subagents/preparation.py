"""Pure construction and validation for one asynchronous child Run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from a13n_harness.usage import intersect_usage_limits
from pydantic import JsonValue
from pydantic_ai.usage import UsageLimits

from a13n_service.agents.domain import (
    EffectiveAgentConfig,
    ResolvedSubagentEdge,
    canonical_digest,
)
from a13n_service.connectivity.selection_domain import (
    ConnectorConnectionRunSelection,
    MCPConnectionToolSelection,
)
from a13n_service.interactions.domain import (
    JsonObject,
    RecoveryBudget,
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
    Thread,
    ThreadOriginKind,
    ThreadRole,
    accepted_run,
)
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_completed_continuation_state,
    initialize_start_state,
)
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.state import RunStateEnvelope

from .domain import (
    ChildCancellationPolicy,
    ChildResultVisibility,
    ChildRunRelationship,
    child_relationship_is_visible,
)


@dataclass(frozen=True, slots=True)
class PreparedChildRunAcceptance:
    """Complete state-first child acceptance candidate."""

    thread: Thread
    run: Run
    state: RunStateEnvelope
    relationship: ChildRunRelationship
    child_definition_id: str


@dataclass(frozen=True, slots=True)
class PreparedChildRunResume:
    """Complete state-first linked child-continuation candidate."""

    run: Run
    state: RunStateEnvelope
    relationship: ChildRunRelationship
    child_definition_id: str
    resumed_from_relationship_id: str
    resumed_from_child_run_id: str
    source_parent_run_id: str
    source_thread_version: int
    source_state: RunStateEnvelope


def prepare_child_run(
    *,
    parent_run: Run,
    parent_state: RunStateEnvelope,
    parent_run_attempt_id: str,
    parent_run_attempt_generation: int,
    parent_agent_instance_id: str,
    subagent_name: str,
    delegated_input: str,
    child_definition_id: str,
    child_agent_id: str,
    child_agent_revision_id: str,
    child_effective_config: EffectiveAgentConfig,
    connector_connection_selections: tuple[ConnectorConnectionRunSelection, ...],
    mcp_connection_selections: tuple[MCPConnectionToolSelection, ...],
    child_thread_id: str,
    child_run_id: str,
    relationship_id: str,
    recovery_budget: RecoveryBudget,
    created_at: datetime,
    cancellation_policy: ChildCancellationPolicy = ChildCancellationPolicy.independent,
    result_visibility: ChildResultVisibility = ChildResultVisibility.parent_thread,
    usage_limits: UsageLimits | None = None,
) -> PreparedChildRunAcceptance:
    """Construct the exact child records without mutable lookup or I/O."""

    edge = require_frozen_subagent_edge(parent_run, parent_state, subagent_name)
    if (edge.child_agent_id, edge.child_agent_revision_id) != (child_agent_id, child_agent_revision_id):
        raise ValueError("prepared child Agent does not match the frozen subagent edge")
    _validate_child_definition_id(child_definition_id)
    accepted_input = AcceptedAgentInput(
        schema_version="1",
        content=(TextContent(text=delegated_input),),
    )
    relationship = _relationship(
        relationship_id=relationship_id,
        parent_run=parent_run,
        parent_run_attempt_id=parent_run_attempt_id,
        parent_run_attempt_generation=parent_run_attempt_generation,
        subagent_name=subagent_name,
        child_run_id=child_run_id,
        child_thread_id=child_thread_id,
        cancellation_policy=cancellation_policy,
        result_visibility=result_visibility,
        created_at=created_at,
    )
    input_payload = accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True)
    request_fingerprint = canonical_digest(
        {
            "schema_version": "1",
            "parent_run_id": parent_run.id,
            "subagent_name": subagent_name,
            "child_agent_id": child_agent_id,
            "child_agent_revision_id": child_agent_revision_id,
            "child_effective_config_digest": child_effective_config.content_digest,
            "input": input_payload,
            "cancellation_policy": cancellation_policy.value,
            "result_visibility": result_visibility.value,
        }
    )
    thread = Thread(
        id=child_thread_id,
        version=1,
        queue_version=0,
        organization_id=parent_run.organization_id,
        session_id=parent_run.session_id,
        role=ThreadRole.child,
        origin_kind=ThreadOriginKind.child,
        origin_thread_id=parent_run.thread_id,
        origin_run_id=parent_run.id,
        current_run_id=child_run_id,
        created_at=created_at,
        updated_at=created_at,
    )
    state = initialize_start_state(
        RunStateSeed(
            run_id=child_run_id,
            agent_id=child_agent_id,
            agent_revision_id=child_agent_revision_id,
            effective_agent_config=child_effective_config,
            usage_limits=intersect_usage_limits(parent_state.usage_limits, edge.usage_limits, usage_limits),
        ),
        thread_id=child_thread_id,
    )
    run = _child_run(
        child_run_id=child_run_id,
        parent_run=parent_run,
        child_thread_id=child_thread_id,
        lineage_kind=RunLineageKind.root,
        lineage_parent_run_id=None,
        trigger_type="async_subagent",
        relationship=relationship,
        parent_agent_instance_id=parent_agent_instance_id,
        child_agent_id=child_agent_id,
        child_agent_revision_id=child_agent_revision_id,
        child_effective_config=child_effective_config,
        connector_connection_selections=tuple(
            item.model_dump(mode="json", by_alias=True) for item in connector_connection_selections
        ),
        mcp_connection_selections=tuple(
            item.model_dump(mode="json", by_alias=True) for item in mcp_connection_selections
        ),
        recovery_budget=recovery_budget,
        request_fingerprint=request_fingerprint,
        input_payload=input_payload,
        delegated_input=delegated_input,
        created_at=created_at,
    )
    return PreparedChildRunAcceptance(
        thread=thread,
        run=run,
        state=state,
        relationship=relationship,
        child_definition_id=child_definition_id,
    )


def prepare_child_resume(
    *,
    parent_run: Run,
    parent_state: RunStateEnvelope,
    parent_run_attempt_id: str,
    parent_run_attempt_generation: int,
    parent_agent_instance_id: str,
    subagent_name: str,
    delegated_input: str,
    child_definition_id: str,
    source_relationship: ChildRunRelationship,
    source_parent_run: Run,
    source_thread: Thread,
    source_run: Run,
    source_state: RunStateEnvelope,
    child_run_id: str,
    relationship_id: str,
    created_at: datetime,
    cancellation_policy: ChildCancellationPolicy = ChildCancellationPolicy.independent,
    result_visibility: ChildResultVisibility = ChildResultVisibility.parent_thread,
    usage_limits: UsageLimits | None = None,
) -> PreparedChildRunResume:
    """Construct a child continuation without adding non-contract relationship fields."""

    edge = require_frozen_subagent_edge(parent_run, parent_state, subagent_name)
    _validate_child_definition_id(child_definition_id)
    _validate_resume_source(
        parent_run=parent_run,
        subagent_name=subagent_name,
        source_relationship=source_relationship,
        source_parent_run=source_parent_run,
        source_thread=source_thread,
        source_run=source_run,
        source_state=source_state,
    )
    child_agent_id = source_run.agent_id
    child_agent_revision_id = source_run.agent_revision_id
    child_effective_config = source_state.effective_agent_config
    accepted_input = AcceptedAgentInput(
        schema_version="1",
        content=(TextContent(text=delegated_input),),
    )
    relationship = _relationship(
        relationship_id=relationship_id,
        parent_run=parent_run,
        parent_run_attempt_id=parent_run_attempt_id,
        parent_run_attempt_generation=parent_run_attempt_generation,
        subagent_name=subagent_name,
        child_run_id=child_run_id,
        child_thread_id=source_thread.id,
        cancellation_policy=cancellation_policy,
        result_visibility=result_visibility,
        created_at=created_at,
    )
    input_payload = accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True)
    request_fingerprint = canonical_digest(
        {
            "schema_version": "1",
            "parent_run_id": parent_run.id,
            "resumed_from_relationship_id": source_relationship.id,
            "source_child_run_id": source_run.id,
            "subagent_name": subagent_name,
            "child_agent_id": child_agent_id,
            "child_agent_revision_id": child_agent_revision_id,
            "child_effective_config_digest": child_effective_config.content_digest,
            "input": input_payload,
            "cancellation_policy": cancellation_policy.value,
            "result_visibility": result_visibility.value,
        }
    )
    state = initialize_completed_continuation_state(
        RunStateSeed(
            run_id=child_run_id,
            agent_id=child_agent_id,
            agent_revision_id=child_agent_revision_id,
            effective_agent_config=child_effective_config,
            usage_limits=intersect_usage_limits(parent_state.usage_limits, edge.usage_limits, usage_limits),
        ),
        source_state,
    )
    run = _child_run(
        child_run_id=child_run_id,
        parent_run=parent_run,
        child_thread_id=source_thread.id,
        lineage_kind=RunLineageKind.continue_,
        lineage_parent_run_id=source_run.id,
        trigger_type="async_subagent_resume",
        relationship=relationship,
        parent_agent_instance_id=parent_agent_instance_id,
        child_agent_id=child_agent_id,
        child_agent_revision_id=child_agent_revision_id,
        child_effective_config=child_effective_config,
        connector_connection_selections=source_run.connector_connection_selections,
        mcp_connection_selections=source_run.mcp_connection_selections,
        recovery_budget=parent_run.recovery_budget,
        request_fingerprint=request_fingerprint,
        input_payload=input_payload,
        delegated_input=delegated_input,
        created_at=created_at,
    )
    return PreparedChildRunResume(
        run=run,
        state=state,
        relationship=relationship,
        child_definition_id=child_definition_id,
        resumed_from_relationship_id=source_relationship.id,
        resumed_from_child_run_id=source_run.id,
        source_parent_run_id=source_parent_run.id,
        source_thread_version=source_thread.version,
        source_state=source_state,
    )


def _validate_child_definition_id(value: str) -> None:
    if not value.startswith("agent-config-") or len(value) != 37:
        raise ValueError("Harness child definition identity is not canonical")


def _relationship(
    *,
    relationship_id: str,
    parent_run: Run,
    parent_run_attempt_id: str,
    parent_run_attempt_generation: int,
    subagent_name: str,
    child_run_id: str,
    child_thread_id: str,
    cancellation_policy: ChildCancellationPolicy,
    result_visibility: ChildResultVisibility,
    created_at: datetime,
) -> ChildRunRelationship:
    return ChildRunRelationship(
        id=relationship_id,
        parent_run_id=parent_run.id,
        parent_run_attempt_id=parent_run_attempt_id,
        parent_run_attempt_generation=parent_run_attempt_generation,
        subagent_name=subagent_name,
        child_run_id=child_run_id,
        child_thread_id=child_thread_id,
        cancellation_policy=cancellation_policy,
        result_visibility=result_visibility,
        created_at=created_at,
    )


def _child_run(
    *,
    child_run_id: str,
    parent_run: Run,
    child_thread_id: str,
    lineage_kind: RunLineageKind,
    lineage_parent_run_id: str | None,
    trigger_type: str,
    relationship: ChildRunRelationship,
    parent_agent_instance_id: str,
    child_agent_id: str,
    child_agent_revision_id: str,
    child_effective_config: EffectiveAgentConfig,
    connector_connection_selections: tuple[JsonObject, ...],
    mcp_connection_selections: tuple[JsonObject, ...],
    recovery_budget: RecoveryBudget,
    request_fingerprint: str,
    input_payload: JsonValue,
    delegated_input: str,
    created_at: datetime,
) -> Run:
    return accepted_run(
        now=created_at,
        id=child_run_id,
        organization_id=parent_run.organization_id,
        authority_principal=parent_run.authority_principal,
        session_id=parent_run.session_id,
        thread_id=child_thread_id,
        parent_run_id=lineage_parent_run_id,
        lineage_kind=lineage_kind,
        trigger_type=trigger_type,
        trigger_entity_type="child_run_relationship",
        trigger_entity_id=relationship.id,
        parent_agent_instance_id=parent_agent_instance_id,
        delegation_id=relationship.id,
        agent_id=child_agent_id,
        agent_revision_id=child_agent_revision_id,
        effective_agent_config_digest=child_effective_config.content_digest,
        runtime_lock_digest=child_effective_config.runtime_lock_digest,
        model_execution_observation=child_effective_config.resolved_model.execution.observation(),
        connector_connection_selections=connector_connection_selections,
        mcp_connection_selections=mcp_connection_selections,
        priority=parent_run.priority,
        queue_name=parent_run.queue_name,
        recovery_budget=recovery_budget,
        request_fingerprint=request_fingerprint,
        input_kind=RunInputKind.agent_input,
        input=input_payload,
        input_text=delegated_input if len(delegated_input) <= 65_536 else None,
    )


def _validate_resume_source(
    *,
    parent_run: Run,
    subagent_name: str,
    source_relationship: ChildRunRelationship,
    source_parent_run: Run,
    source_thread: Thread,
    source_run: Run,
    source_state: RunStateEnvelope,
) -> None:
    if (
        source_relationship.subagent_name != subagent_name
        or source_relationship.child_thread_id != source_thread.id
        or source_relationship.child_run_id != source_run.id
        or not child_relationship_is_visible(
            source_relationship,
            origin_parent=source_parent_run,
            requesting_parent=parent_run,
        )
        or source_thread.organization_id != parent_run.organization_id
        or source_thread.session_id != parent_run.session_id
        or source_thread.role is not ThreadRole.child
        or source_thread.origin_kind is not ThreadOriginKind.child
        or source_thread.current_run_id != source_run.id
        or source_thread.head_run_id != source_run.id
        or source_run.organization_id != parent_run.organization_id
        or source_run.session_id != parent_run.session_id
        or source_run.thread_id != source_thread.id
        or source_run.status is not RunStatus.completed
        or source_state.run_id != source_run.id
        or source_state.thread_id != source_thread.id
        or source_state.checkpoint_kind != "completed"
        or source_state.agent_id != source_run.agent_id
        or source_state.agent_revision_id != source_run.agent_revision_id
        or source_state.effective_agent_config.content_digest != source_run.effective_agent_config_digest
        or source_state.runtime_lock_digest != source_run.runtime_lock_digest
    ):
        raise ValueError("resumed child execution is not a selected completed child head")


def require_frozen_subagent_edge(
    parent_run: Run,
    parent_state: RunStateEnvelope,
    subagent_name: str,
) -> ResolvedSubagentEdge:
    if (
        parent_state.run_id != parent_run.id
        or parent_state.thread_id != parent_run.thread_id
        or parent_state.agent_id != parent_run.agent_id
        or parent_state.agent_revision_id != parent_run.agent_revision_id
        or parent_state.effective_agent_config.content_digest != parent_run.effective_agent_config_digest
    ):
        raise ValueError("parent Run state does not match relational authority")
    edges = tuple(edge for edge in parent_state.effective_agent_config.resolved_subagents if edge.name == subagent_name)
    if len(edges) != 1:
        raise ValueError("subagent name is not one exact frozen parent edge")
    return edges[0]


__all__ = [
    "PreparedChildRunAcceptance",
    "PreparedChildRunResume",
    "prepare_child_resume",
    "prepare_child_run",
    "require_frozen_subagent_edge",
]
