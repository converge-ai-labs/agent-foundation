from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from a13n_service.agents.domain import (
    ChildAgentExecution,
    ChildEnvironmentPolicy,
    EffectiveAgentConfig,
    ResolvedSubagentEdge,
    canonical_digest,
)
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.connectivity.selection_domain import (
    ConnectorConnectionRunSelection,
    MCPConnectionToolSelection,
)
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.attempts import AttemptExecutionService, AttemptPreparationAccepted
from a13n_service.interactions.domain import Run, Session, Thread, ThreadOriginKind, ThreadRole
from a13n_service.interactions.initialization import RunStateSeed, initialize_start_state
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.interactions.state import CompletedOutcomeCandidate
from a13n_service.storage import ObjectNotFound, ObjectStore, short_session, transaction
from a13n_service.subagents import (
    ChildCancellationPolicy,
    ChildRunAcceptanceError,
    ChildRunAcceptanceService,
    prepare_child_resume,
    prepare_child_run,
)
from a13n_service.subagents.models import ChildRunRelationshipRecord
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    ORGANIZATION_ID,
    SESSION_ID,
    THREAD_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)
from .test_acceptance import _accepted_run, _inline_hooks
from .test_attempt_execution import _authority, _completed_state, _worker

pytestmark = pytest.mark.anyio

CHILD_AGENT_ID = "agt_2222222222222222"
CHILD_REVISION_ID = "agtr_2222222222222222"
CHILD_DEFINITION_ID = f"agent-config-{'3' * 24}"
CONNECTOR_SELECTION = ConnectorConnectionRunSelection(
    connector_connection_id="cconn_2222222222222222",
    connector_provider_id="cprv_2222222222222222",
    defer_loading=False,
    tools=("find_order",),
)
MCP_SELECTION = MCPConnectionToolSelection(
    mcp_connection_id="mcpc_2222222222222222",
    defer_loading=True,
    tools=("search_docs",),
)


async def test_child_acceptance_is_fenced_atomic_and_non_idempotent(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await _grant_and_seed_child(interaction_sessions)
    states, parent, parent_state = await _accept_parent(
        interaction_sessions,
        interaction_object_store,
        with_shared_environment=True,
        connector_connection_selections=(CONNECTOR_SELECTION,),
        mcp_connection_selections=(MCP_SELECTION,),
    )
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "parent-lease",
        attempt_id_factory=lambda: "rat_2222222222222222",
        lifecycle=test_lifecycle_writer(),
    )
    claim = await scheduler.claim(parent.id, _worker())
    assert claim is not None
    authority = _authority(claim)
    execution = AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-parent",
    )
    async with short_session(interaction_sessions) as database:
        parent_record = await database.get(RunRecord, parent.id)
        assert parent_record is not None
        running_parent = parent_record.to_resource()

    assert running_parent.native_tool_contexts
    child_config = effective_agent_config()
    first = _prepared_child(
        running_parent,
        parent_state,
        authority.run_attempt_id,
        authority.attempt_number,
        child_config,
        suffix="3",
        connector_connection_selections=(CONNECTOR_SELECTION,),
        mcp_connection_selections=(MCP_SELECTION,),
    )
    service = ChildRunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=3),
    )

    altered_config = child_config.model_copy(update={"instructions": "Changed after parent acceptance"})
    altered_config = altered_config.model_copy(
        update={
            "content_digest": canonical_digest(
                altered_config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}),
            )
        }
    )
    altered = _prepared_child(
        running_parent,
        parent_state,
        authority.run_attempt_id,
        authority.attempt_number,
        altered_config,
        suffix="9",
        connector_connection_selections=(CONNECTOR_SELECTION,),
        mcp_connection_selections=(MCP_SELECTION,),
    )
    with pytest.raises(ChildRunAcceptanceError, match="accepted execution snapshot"):
        await service.accept(altered, authority)

    accepted = await service.accept(first, authority)
    with pytest.raises(ChildRunAcceptanceError, match="already contains accepted state"):
        await service.accept(first, authority)
    second = _prepared_child(
        running_parent,
        parent_state,
        authority.run_attempt_id,
        authority.attempt_number,
        child_config,
        suffix="4",
        connector_connection_selections=(CONNECTOR_SELECTION,),
        mcp_connection_selections=(MCP_SELECTION,),
    )
    second_accepted = await service.accept(second, authority)

    assert second_accepted.relationship.id != accepted.relationship.id
    assert second_accepted.child_run_id != accepted.child_run_id
    assert accepted.relationship.child_run_id == first.run.id
    assert accepted.relationship.child_thread_id == first.thread.id
    async with short_session(interaction_sessions) as database:
        child = await database.get(RunRecord, accepted.child_run_id)
        child_thread = await database.get(ThreadRecord, accepted.child_thread_id)
        relationships = await database.scalar(select(func.count()).select_from(ChildRunRelationshipRecord))
        threads = await database.scalar(
            select(func.count()).select_from(ThreadRecord).where(ThreadRecord.role == "child")
        )
        relationship = await database.get(ChildRunRelationshipRecord, accepted.relationship.id)
        assert relationship is not None
        assert relationship.parent_run_attempt_fence == authority.attempt_number
        assert relationship.to_resource() == accepted.relationship
        assert child is not None and child_thread is not None
        child_resource = child.to_resource()
        assert child_resource.native_tool_contexts == ()
        assert child_resource.authority_principal == running_parent.authority_principal
        assert child_resource.connector_connection_selections == (CONNECTOR_SELECTION.model_dump(mode="json"),)
        assert child_resource.mcp_connection_selections == (MCP_SELECTION.model_dump(mode="json"),)
        assert (child.agent_id, child.agent_revision_id) == (CHILD_AGENT_ID, CHILD_REVISION_ID)
        assert (child_thread.session_id, child_thread.origin_run_id) == (SESSION_ID, running_parent.id)
        assert child.environment_id == running_parent.environment_id
        assert relationships == 2
        assert threads == 2

    child_claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=4),
        token_factory=lambda: "child-lease",
        attempt_id_factory=lambda: "rat_2323232323232323",
        lifecycle=test_lifecycle_writer(),
    ).claim(accepted.child_run_id, _worker())
    assert child_claim is not None


async def test_child_acceptance_rejects_stale_fence_before_publishing_state(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await _grant_and_seed_child(interaction_sessions)
    states, parent, parent_state = await _accept_parent(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "parent-lease",
        attempt_id_factory=lambda: "rat_5555555555555555",
        lifecycle=test_lifecycle_writer(),
    )
    claim = await scheduler.claim(parent.id, _worker())
    assert claim is not None
    authority = _authority(claim)
    async with short_session(interaction_sessions) as database:
        parent_record = await database.get(RunRecord, parent.id)
        assert parent_record is not None
        running_parent = parent_record.to_resource()
    prepared = _prepared_child(
        running_parent,
        parent_state,
        authority.run_attempt_id,
        authority.attempt_number + 1,
        effective_agent_config(),
        suffix="6",
    )

    with pytest.raises(ChildRunAcceptanceError, match="parent authority changed"):
        await ChildRunAcceptanceService(
            interaction_sessions,
            states,
            RunPayloadStore(interaction_object_store),
            clock=lambda: NOW + timedelta(seconds=2),
        ).accept(prepared, authority)

    with pytest.raises(ObjectNotFound):
        await states.read(ORGANIZATION_ID, prepared.run.id)


async def test_child_acceptance_reauthorizes_persisted_parent_principal(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await _grant_and_seed_child(interaction_sessions)
    states, parent, parent_state = await _accept_parent(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "parent-lease",
        attempt_id_factory=lambda: "rat_7777777777777777",
        lifecycle=test_lifecycle_writer(),
    )
    claim = await scheduler.claim(parent.id, _worker())
    assert claim is not None
    authority = _authority(claim)
    async with short_session(interaction_sessions) as database:
        parent_record = await database.get(RunRecord, parent.id)
        assert parent_record is not None
        running_parent = parent_record.to_resource()
    prepared = _prepared_child(
        running_parent,
        parent_state,
        authority.run_attempt_id,
        authority.attempt_number,
        effective_agent_config(),
        suffix="8",
    )
    service = ChildRunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=2),
    )
    async with transaction(interaction_sessions) as database:
        binding = await database.scalar(select(RoleBindingRecord).where(RoleBindingRecord.principal_id == USER_ID))
        assert binding is not None
        await database.delete(binding)

    with pytest.raises(ChildRunAcceptanceError, match="no longer authorized"):
        await service.accept(prepared, authority)

    with pytest.raises(ObjectNotFound):
        await states.read(ORGANIZATION_ID, prepared.run.id)


async def test_concurrent_child_acceptance_keeps_distinct_relationships_on_postgresql(
    postgres_interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    sessions = postgres_interaction_sessions
    await _grant_and_seed_child(sessions)
    states, parent, parent_state = await _accept_parent(sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "parent-lease",
        attempt_id_factory=lambda: "rat_9999999999999999",
        lifecycle=test_lifecycle_writer(),
    )
    claim = await scheduler.claim(parent.id, _worker())
    assert claim is not None
    authority = _authority(claim)
    async with short_session(sessions) as database:
        parent_record = await database.get(RunRecord, parent.id)
        assert parent_record is not None
        running_parent = parent_record.to_resource()
    child_config = effective_agent_config()
    candidates = tuple(
        _prepared_child(
            running_parent,
            parent_state,
            authority.run_attempt_id,
            authority.attempt_number,
            child_config,
            suffix=suffix,
        )
        for suffix in ("a", "b")
    )
    service = ChildRunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=2),
    )

    receipts = await asyncio.gather(*(service.accept(candidate, authority) for candidate in candidates))

    assert receipts[0].relationship.id != receipts[1].relationship.id
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(ChildRunRelationshipRecord)) == 2
        assert (
            await database.scalar(select(func.count()).select_from(ThreadRecord).where(ThreadRecord.role == "child"))
            == 2
        )


async def test_completed_child_can_resume_as_linked_continuation(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await _grant_and_seed_child(interaction_sessions)
    states, parent, parent_state = await _accept_parent(
        interaction_sessions,
        interaction_object_store,
        connector_connection_selections=(CONNECTOR_SELECTION,),
        mcp_connection_selections=(MCP_SELECTION,),
    )
    parent_claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "parent-lease",
        attempt_id_factory=lambda: "rat_3434343434343434",
        lifecycle=test_lifecycle_writer(),
    ).claim(parent.id, _worker())
    assert parent_claim is not None
    parent_authority = _authority(parent_claim)
    async with short_session(interaction_sessions) as database:
        parent_record = await database.get(RunRecord, parent.id)
        assert parent_record is not None
        running_parent = parent_record.to_resource()
    child_config = effective_agent_config()
    first = _prepared_child(
        running_parent,
        parent_state,
        parent_authority.run_attempt_id,
        parent_authority.attempt_number,
        child_config,
        suffix="d",
        connector_connection_selections=(CONNECTOR_SELECTION,),
        mcp_connection_selections=(MCP_SELECTION,),
    )
    service = ChildRunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=2),
    )
    accepted = await service.accept(first, parent_authority)
    child_claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "child-lease",
        attempt_id_factory=lambda: "rat_4545454545454545",
        lifecycle=test_lifecycle_writer(),
    ).claim(accepted.child_run_id, _worker())
    assert child_claim is not None
    completed_child = await _complete_run(
        interaction_sessions,
        interaction_object_store,
        states,
        first.run,
        _authority(child_claim),
    )
    source_state = await states.read(ORGANIZATION_ID, completed_child.id, expected_thread_id=first.thread.id)
    async with short_session(interaction_sessions) as database:
        source_thread_record = await database.get(ThreadRecord, first.thread.id)
        source_relationship_record = await database.get(ChildRunRelationshipRecord, accepted.relationship.id)
        assert source_thread_record is not None and source_relationship_record is not None
        source_thread = source_thread_record.to_resource()
        source_relationship = source_relationship_record.to_resource()

    resumed = prepare_child_resume(
        parent_run=running_parent,
        parent_state=parent_state,
        parent_run_attempt_id=parent_authority.run_attempt_id,
        parent_run_attempt_fence=parent_authority.attempt_number,
        parent_agent_instance_id="agent-parent",
        subagent_name="researcher",
        delegated_input='{"delegated_task":"continue"}',
        child_definition_id=CHILD_DEFINITION_ID,
        source_relationship=source_relationship,
        source_parent_run=running_parent,
        source_thread=source_thread,
        source_run=completed_child,
        source_state=source_state.envelope,
        child_run_id="run_eeeeeeeeeeeeeeee",
        relationship_id="crr_eeeeeeeeeeeeeeee",
        created_at=NOW + timedelta(seconds=5),
    )

    receipt = await service.accept_resume(resumed, parent_authority)

    assert receipt.child_thread_id == source_thread.id
    assert receipt.child_run_id == resumed.run.id
    resumed_state = await states.read(ORGANIZATION_ID, resumed.run.id, expected_thread_id=source_thread.id)
    assert resumed_state.envelope.checkpoint_seq == 0
    assert resumed_state.envelope.harness.message_history == source_state.envelope.harness.message_history
    async with short_session(interaction_sessions) as database:
        child_thread = await database.get(ThreadRecord, source_thread.id)
        child_run = await database.get(RunRecord, resumed.run.id)
        assert child_thread is not None and child_run is not None
        assert (child_thread.version, child_thread.current_run_id, child_thread.head_run_id) == (
            source_thread.version + 1,
            resumed.run.id,
            completed_child.id,
        )
        assert child_run.parent_run_id == completed_child.id
        assert child_run.lineage_kind == "continue"
        child_resource = child_run.to_resource()
        assert child_resource.connector_connection_selections == (CONNECTOR_SELECTION.model_dump(mode="json"),)
        assert child_resource.mcp_connection_selections == (MCP_SELECTION.model_dump(mode="json"),)


async def _complete_run(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    states: RunStateStore,
    run: Run,
    authority,
    *,
    outcome: CompletedOutcomeCandidate | None = None,
    time_offset_seconds: int = 3,
    expected_thread_version: int = 1,
) -> Run:
    execution = AttemptExecutionService(
        sessions, clock=lambda: NOW + timedelta(seconds=time_offset_seconds), lifecycle=test_lifecycle_writer()
    )
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="completed-child",
    )

    current = await states.read(ORGANIZATION_ID, run.id)
    candidate = _completed_state(
        current.envelope,
        authority.run_attempt_id,
        authority.attempt_number,
        outcome=outcome,
    )
    stored = await execution.publish_checkpoint(authority, states, current, candidate)
    await RunOutcomeService(
        sessions,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=time_offset_seconds + 1),
        lifecycle=test_lifecycle_writer(),
    ).commit_state_outcome(authority, stored)
    async with short_session(sessions) as database:
        row = await database.get(RunRecord, run.id)
        assert row is not None
        return row.to_resource()


def _prepared_child(
    parent: Run,
    parent_state,
    attempt_id: str,
    attempt_number: int,
    child_config: EffectiveAgentConfig,
    *,
    suffix: str,
    cancellation_policy: ChildCancellationPolicy = ChildCancellationPolicy.independent,
    connector_connection_selections: tuple[ConnectorConnectionRunSelection, ...] = (),
    mcp_connection_selections: tuple[MCPConnectionToolSelection, ...] = (),
):
    return prepare_child_run(
        parent_run=parent,
        parent_state=parent_state,
        parent_run_attempt_id=attempt_id,
        parent_run_attempt_fence=attempt_number,
        parent_agent_instance_id="agent-parent",
        subagent_name="researcher",
        delegated_input='{"delegated_task":"research"}',
        child_definition_id=CHILD_DEFINITION_ID,
        child_agent_id=CHILD_AGENT_ID,
        child_agent_revision_id=CHILD_REVISION_ID,
        child_effective_config=child_config,
        connector_connection_selections=connector_connection_selections,
        mcp_connection_selections=mcp_connection_selections,
        child_thread_id=f"thread-{suffix * 32}",
        child_run_id=f"run_{suffix * 16}",
        relationship_id=f"crr_{suffix * 16}",
        execution_budget=parent.execution_budget,
        created_at=NOW + timedelta(seconds=2),
        cancellation_policy=cancellation_policy,
    )


async def _accept_parent(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    *,
    with_shared_environment: bool = False,
    connector_connection_selections: tuple[ConnectorConnectionRunSelection, ...] = (),
    mcp_connection_selections: tuple[MCPConnectionToolSelection, ...] = (),
):
    edge = ResolvedSubagentEdge(
        name="researcher",
        child_agent_id=CHILD_AGENT_ID,
        child_agent_revision_id=CHILD_REVISION_ID,
        context={"include_task": True, "history": "none", "task_state": "shared"},
        environment=ChildEnvironmentPolicy(mode="shared" if with_shared_environment else "none"),
    )
    base = effective_agent_config()
    candidate = base.model_copy(
        update={
            "resolved_subagents": (edge,),
            "content_digest": "0" * 64,
            "subagent_mode": "async",
            "child_configs": {
                CHILD_REVISION_ID: ChildAgentExecution(
                    agent_id=CHILD_AGENT_ID,
                    revision_content_digest="3" * 64,
                    effective_config=base,
                    connector_connection_selections=connector_connection_selections,
                    mcp_connection_selections=mcp_connection_selections,
                )
            },
        }
    )
    config = candidate.model_copy(
        update={
            "content_digest": canonical_digest(
                candidate.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
            )
        }
    )
    seed = RunStateSeed(
        run_id="run_1111111111111111",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=config,
    )
    state = initialize_start_state(seed, thread_id=THREAD_ID)
    run = _accepted_run(
        run_id=seed.run_id,
        thread_id=THREAD_ID,
        idempotency_key="parent-start",
        request_fingerprint="1" * 64,
        config=config,
    ).model_copy(
        update={
            "native_tool_contexts": (
                {
                    "kind": "account",
                    "account_id": "acct_parent",
                    "provider_key": "slack",
                    "execution_principal_ref": {"principal_type": "user", "principal_id": USER_ID},
                    "allowed_actions": ["slack.send_message"],
                    "target_scope": {"channel_ids": ["C1"]},
                },
            )
        }
    )
    states = RunStateStore(objects)
    await RunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(objects),
        _inline_hooks(),
        clock=lambda: NOW,
        lifecycle=test_lifecycle_writer(),
    ).accept_new_thread(
        session=Session(
            id=SESSION_ID,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW,
        ),
        thread=Thread(
            id=THREAD_ID,
            version=1,
            queue_version=0,
            organization_id=ORGANIZATION_ID,
            session_id=SESSION_ID,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            current_run_id=run.id,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=run,
        state=state,
    )
    return states, run, state


async def _grant_and_seed_child(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(sessions) as database:
        database.add(
            UserRecord(
                id=USER_ID,
                email="runner@example.com",
                normalized_email="runner@example.com",
                name="Runner",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        database.add(
            RoleBindingRecord(
                id="rbac_2222222222222222",
                organization_id=ORGANIZATION_ID,
                workspace_id=None,
                principal_type="user",
                principal_id=USER_ID,
                resource_type="organization",
                resource_id=ORGANIZATION_ID,
                role_key="member",
                created_by_user_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        database.add(
            RoleBindingRecord(
                id="rbac_3333333333333333",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                principal_type="user",
                principal_id=USER_ID,
                resource_type="workspace",
                resource_id=WORKSPACE_ID,
                role_key="runner",
                created_by_user_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        database.add(
            AgentRecord(
                id=CHILD_AGENT_ID,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                source="custom",
                name="Child Agent",
                normalized_name="child agent",
                description=None,
                version=1,
                current_revision_id=CHILD_REVISION_ID,
                enabled=True,
                archived_at=None,
                duplicated_from_agent_id=None,
                duplicated_from_revision_id=None,
                created_by_type="user",
                created_by_id=USER_ID,
                updated_by_type="user",
                updated_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        database.add(
            AgentRevisionRecord(
                id=CHILD_REVISION_ID,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                agent_id=CHILD_AGENT_ID,
                version=1,
                plugin_runtime_mode="on_demand",
                config={},
                config_digest="2" * 64,
                resolved_model={},
                resolved_plugin_versions=[],
                runtime_lock_digest="a" * 64,
                resolved_skills=[],
                connector_tools=[],
                mcp_tools=[],
                resolved_subagents=[],
                content_digest="3" * 64,
                source_revision_id=None,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
            )
        )
