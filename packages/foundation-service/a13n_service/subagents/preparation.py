"""Pure construction and validation for one asynchronous child Run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from a13n_service.agents.domain import (
    EffectiveAgentConfig,
    EnvironmentExecutionConfig,
    ResolvedSubagentEdge,
    canonical_digest,
)
from a13n_service.interactions.domain import (
    EncryptedRunConfigPayloadRef,
    MCPToolSnapshotRef,
    RecoveryBudget,
    RecoveryUsage,
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.initialization import RunStateSeed, initialize_start_state
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.state import RunStateEnvelope

from .domain import (
    ChildCancellationPolicy,
    ChildResultVisibility,
    ChildRunRelationship,
)


@dataclass(frozen=True, slots=True)
class PreparedChildRunAcceptance:
    """Complete state-first child acceptance candidate."""

    thread: Thread
    run: Run
    state: RunStateEnvelope
    relationship: ChildRunRelationship


def prepare_child_run(
    *,
    parent_run: Run,
    parent_state: RunStateEnvelope,
    parent_run_attempt_id: str,
    parent_run_attempt_generation: int,
    parent_agent_instance_id: str,
    spawn_operation_id: str,
    subagent_name: str,
    delegated_input: str,
    child_definition_id: str,
    child_agent_id: str,
    child_agent_revision_id: str,
    child_effective_config: EffectiveAgentConfig,
    child_thread_id: str,
    child_run_id: str,
    relationship_id: str,
    mcp_tool_snapshot: MCPToolSnapshotRef,
    recovery_budget: RecoveryBudget,
    created_at: datetime,
    encrypted_config_payload: EncryptedRunConfigPayloadRef | None = None,
    cancellation_policy: ChildCancellationPolicy = ChildCancellationPolicy.independent,
    result_visibility: ChildResultVisibility = ChildResultVisibility.parent_thread,
) -> PreparedChildRunAcceptance:
    """Construct the exact child records without mutable lookup or I/O."""

    edge = require_frozen_subagent_edge(parent_run, parent_state, subagent_name)
    if (edge.child_agent_id, edge.child_agent_revision_id) != (child_agent_id, child_agent_revision_id):
        raise ValueError("prepared child Agent does not match the frozen subagent edge")
    expected_definition_id = f"agent-config-{child_effective_config.content_digest[:24]}"
    if child_definition_id != expected_definition_id:
        raise ValueError("Harness child definition does not match the frozen child configuration")
    validate_child_environment_policy(
        edge,
        parent=parent_state.effective_agent_config,
        child=child_effective_config,
    )
    accepted_input = AcceptedAgentInput(
        schema_version="1",
        content=(TextContent(text=delegated_input),),
    )
    relationship = ChildRunRelationship(
        id=relationship_id,
        parent_run_id=parent_run.id,
        parent_run_attempt_id=parent_run_attempt_id,
        parent_run_attempt_generation=parent_run_attempt_generation,
        subagent_name=subagent_name,
        child_run_id=child_run_id,
        child_thread_id=child_thread_id,
        spawn_operation_id=spawn_operation_id,
        cancellation_policy=cancellation_policy,
        result_visibility=result_visibility,
        created_at=created_at,
    )
    input_payload = accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True)
    request_fingerprint = canonical_digest(
        {
            "schema_version": "1",
            "parent_run_id": parent_run.id,
            "spawn_operation_id": spawn_operation_id,
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
        tenant_id=parent_run.tenant_id,
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
        ),
        thread_id=child_thread_id,
    )
    run = Run(
        id=child_run_id,
        version=1,
        tenant_id=parent_run.tenant_id,
        authority_principal=parent_run.authority_principal,
        session_id=parent_run.session_id,
        thread_id=child_thread_id,
        lineage_kind=RunLineageKind.root,
        trigger_type="async_subagent",
        trigger_entity_type="child_run_relationship",
        trigger_entity_id=relationship_id,
        parent_agent_instance_id=parent_agent_instance_id,
        delegation_id=relationship_id,
        parent_tool_call_id=spawn_operation_id,
        agent_id=child_agent_id,
        agent_revision_id=child_agent_revision_id,
        effective_agent_config_digest=child_effective_config.content_digest,
        encrypted_config_payload=encrypted_config_payload,
        runtime_lock_digest=child_effective_config.runtime_lock_digest,
        model_execution_observation=child_effective_config.resolved_model.execution.observation(),
        connection_selections=tuple(
            item.model_dump(mode="json", by_alias=True) for item in child_effective_config.connector_tools
        ),
        mcp_connection_selections=tuple(
            item.model_dump(mode="json", by_alias=True) for item in child_effective_config.mcp_tools
        ),
        mcp_tool_snapshot=mcp_tool_snapshot,
        priority=parent_run.priority,
        queue_name=parent_run.queue_name,
        available_at=created_at,
        next_attempt_fence=1,
        recovery_budget=recovery_budget,
        attempts_started=0,
        recovery_attempts_started=0,
        handoffs_completed=0,
        usage_charged=RecoveryUsage(),
        request_fingerprint=request_fingerprint,
        status=RunStatus.accepted,
        input_kind=RunInputKind.agent_input,
        input=input_payload,
        input_text=delegated_input if len(delegated_input) <= 65_536 else None,
        created_at=created_at,
        updated_at=created_at,
    )
    return PreparedChildRunAcceptance(thread=thread, run=run, state=state, relationship=relationship)


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


def validate_child_environment_policy(
    edge: ResolvedSubagentEdge,
    *,
    parent: EffectiveAgentConfig,
    child: EffectiveAgentConfig,
) -> None:
    parent_environment = parent.resolved_environment
    child_environment = child.resolved_environment
    if edge.environment.mode == "none":
        if child_environment is not None:
            raise ValueError("none child Environment policy forbids a child binding")
        return
    if child_environment is None:
        raise ValueError("child Environment policy requires an exact child binding")
    if edge.environment.mode == "shared_root":
        if (
            parent_environment is None
            or _environment_target_identity(parent_environment) != _environment_target_identity(child_environment)
            or _access_rank(child_environment.access) > _access_rank(parent_environment.access)
        ):
            raise ValueError("shared_root child Environment is not equal to or narrower than its parent")
        return
    if parent_environment is not None and _environment_target_identity(
        parent_environment
    ) == _environment_target_identity(child_environment):
        raise ValueError("dedicated child Environment must use a different exact target")


def _environment_target_identity(environment: EnvironmentExecutionConfig) -> tuple[object, ...]:
    return (
        environment.connection,
        environment.provider_package_revision_id,
        environment.provider_lock,
        environment.target_key,
    )


def _access_rank(access: str) -> int:
    return {"read_only": 0, "read_write": 1, "full": 2}[access]


__all__ = [
    "PreparedChildRunAcceptance",
    "prepare_child_run",
    "require_frozen_subagent_edge",
    "validate_child_environment_policy",
]
