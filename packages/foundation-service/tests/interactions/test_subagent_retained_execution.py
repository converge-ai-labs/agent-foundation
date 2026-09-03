from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncResumeRequest,
    SubagentInfoRequest,
    SubagentOperatorContext,
)
from a13n_service.agents.domain import EffectiveAgentConfig, canonical_digest
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions import (
    AttemptScheduler,
    Run,
    RunAcceptanceService,
    RunLineageKind,
    RunOutcomeService,
    RunPayloadStore,
    RunStateSeed,
    RunStateStore,
    Thread,
    ThreadOriginKind,
    ThreadRole,
    initialize_completed_continuation_state,
    initialize_fork_state,
)
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.models import ThreadRecord
from a13n_service.storage import ObjectStore, short_session, transaction
from a13n_service.subagents import (
    ChildRunAcceptanceService,
    ChildRunAdmissionProfile,
    FoundationChildRunAdmissionPreparer,
    FoundationSubagentOperator,
    FoundationSubagentOperatorError,
)
from a13n_service.subagents.models import ChildRunRelationshipRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    TENANT_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)
from .test_acceptance import _accepted_run
from .test_attempt_execution import _authority, _worker
from .test_subagent_acceptance import (
    CHILD_AGENT_ID,
    CHILD_DEFINITION_ID,
    CHILD_REVISION_ID,
    _complete_run,
)
from .test_subagent_operator import AuthorityBox, _operator, _plan, _run

pytestmark = pytest.mark.anyio

REPLACEMENT_CHILD_REVISION_ID = "agtr_7373737373737373"
REPLACEMENT_CHILD_DEFINITION_ID = f"agent-config-{'4' * 24}"


async def test_later_parent_run_can_resume_retained_child_from_same_thread(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    operator, context, delegate_plan, states, _, authority_box = await _operator(
        interaction_sessions,
        interaction_object_store,
    )
    delegated = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
    )
    await _complete_delegated_child(
        interaction_sessions,
        interaction_object_store,
        states,
        delegated.child_run_id,
        attempt_id="rat_6969696969696969",
    )
    later_operator, later_context = await _continue_parent(
        interaction_sessions,
        interaction_object_store,
        states,
        context,
        authority_box,
    )

    visible = await later_operator.info(
        later_context,
        SubagentInfoRequest(execution_id=delegated.execution_id),
    )
    resumed = await later_operator.resume(
        _plan(later_context, operation_id="later-resume", delegated_input='{"delegated_task":"continue"}'),
        AsyncResumeRequest(execution_id=delegated.execution_id, prompt="continue"),
    )

    assert visible.executions[0].resumable is True
    assert resumed.resumed_from == delegated.execution_id
    assert resumed.thread_id == delegated.thread_id
    async with short_session(interaction_sessions) as database:
        relationship = await database.get(ChildRunRelationshipRecord, resumed.execution_id)
        assert relationship is not None
        assert relationship.parent_run_id == later_context.parent_run_id


async def test_session_visibility_controls_cross_thread_retained_child_reads(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    operator, context, delegate_plan, states, _, authority_box = await _operator(
        interaction_sessions,
        interaction_object_store,
    )
    delegated = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
    )
    other_operator, other_context = await _additional_parent_thread(
        interaction_sessions,
        interaction_object_store,
        states,
        context,
        authority_box,
    )

    hidden = await other_operator.info(
        other_context,
        SubagentInfoRequest(execution_id=delegated.execution_id),
    )
    async with transaction(interaction_sessions) as database:
        relationship = await database.get(ChildRunRelationshipRecord, delegated.execution_id)
        assert relationship is not None
        relationship.result_visibility = "session"
    visible = await other_operator.info(
        other_context,
        SubagentInfoRequest(execution_id=delegated.execution_id),
    )

    assert hidden.executions == () and hidden.total == 0
    assert tuple(item.execution_id for item in visible.executions) == (delegated.execution_id,)
    assert visible.total == 1


async def test_retained_child_read_reauthorizes_current_parent_principal(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    operator, context, delegate_plan, _, _, _ = await _operator(
        interaction_sessions,
        interaction_object_store,
    )
    delegated = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
    )
    async with transaction(interaction_sessions) as database:
        binding = await database.scalar(
            select(RoleBindingRecord).where(
                RoleBindingRecord.principal_id == USER_ID,
                RoleBindingRecord.role_key == "runner",
            )
        )
        assert binding is not None
        await database.delete(binding)

    with pytest.raises(FoundationSubagentOperatorError) as denied:
        await operator.info(context, SubagentInfoRequest(execution_id=delegated.execution_id))

    assert denied.value.code == "subagent_authorization_denied"


async def test_later_parent_roster_revision_can_resume_retained_child_checkpoint(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    operator, context, delegate_plan, states, _, authority_box = await _operator(
        interaction_sessions,
        interaction_object_store,
    )
    delegated = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
    )
    await _complete_delegated_child(
        interaction_sessions,
        interaction_object_store,
        states,
        delegated.child_run_id,
        attempt_id="rat_7373737373737373",
    )
    parent_state = await states.read(TENANT_ID, context.parent_run_id)
    await _seed_replacement_child_revision(interaction_sessions)
    later_operator, later_context = await _continue_parent(
        interaction_sessions,
        interaction_object_store,
        states,
        context,
        authority_box,
        parent_config=_replace_parent_child_revision(parent_state.envelope.effective_agent_config),
        child_revision_id=REPLACEMENT_CHILD_REVISION_ID,
        child_definition_id=REPLACEMENT_CHILD_DEFINITION_ID,
    )

    resumed = await later_operator.resume(
        _plan(
            later_context,
            operation_id="replacement-resume",
            delegated_input='{"delegated_task":"continue"}',
            child_definition_id=REPLACEMENT_CHILD_DEFINITION_ID,
        ),
        AsyncResumeRequest(execution_id=delegated.execution_id, prompt="continue"),
    )

    assert resumed.child_definition_id == REPLACEMENT_CHILD_DEFINITION_ID
    assert resumed.resumed_from == delegated.execution_id
    assert resumed.child_run_id is not None
    assert (await _run(interaction_sessions, resumed.child_run_id)).agent_revision_id == REPLACEMENT_CHILD_REVISION_ID


async def _complete_delegated_child(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    states: RunStateStore,
    child_run_id: str | None,
    *,
    attempt_id: str,
) -> None:
    assert child_run_id is not None
    child_claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "child-lease",
        attempt_id_factory=lambda: attempt_id,
    ).claim(child_run_id, _worker())
    assert child_claim is not None
    await _complete_run(
        sessions,
        objects,
        states,
        await _run(sessions, child_run_id),
        _authority(child_claim),
    )


async def _continue_parent(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    states: RunStateStore,
    prior_context: SubagentOperatorContext,
    prior_authority: AuthorityBox,
    *,
    parent_config: EffectiveAgentConfig | None = None,
    child_revision_id: str = CHILD_REVISION_ID,
    child_definition_id: str = CHILD_DEFINITION_ID,
) -> tuple[FoundationSubagentOperator, SubagentOperatorContext]:
    parent = await _run(sessions, prior_context.parent_run_id)
    completed = await _complete_run(sessions, objects, states, parent, prior_authority.current_context)
    completed_state = await states.read(completed.tenant_id, completed.id, expected_thread_id=completed.thread_id)
    next_config = parent_config or completed_state.envelope.effective_agent_config
    next_run_id = "run_7070707070707070"
    next_state = initialize_completed_continuation_state(
        RunStateSeed(
            run_id=next_run_id,
            agent_id=completed.agent_id,
            agent_revision_id=completed.agent_revision_id,
            effective_agent_config=next_config,
        ),
        completed_state.envelope,
    )
    next_run = _accepted_run(
        run_id=next_run_id,
        thread_id=completed.thread_id,
        idempotency_key="continue-parent-after-child",
        request_fingerprint="7" * 64,
        config=next_config,
    ).model_copy(update={"parent_run_id": completed.id, "lineage_kind": RunLineageKind.continue_})
    async with short_session(sessions) as database:
        parent_thread = await database.get(ThreadRecord, completed.thread_id)
        assert parent_thread is not None
        expected_thread_version = parent_thread.version
    await RunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=6),
    ).advance_thread(
        run=next_run,
        state=next_state,
        expected_thread_version=expected_thread_version,
        expected_current_run_id=completed.id,
        expected_head_run_id=completed.id,
        next_head_run_id=completed.id,
    )
    next_claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=7),
        token_factory=lambda: "next-parent-lease",
        attempt_id_factory=lambda: "rat_7070707070707070",
    ).claim(next_run_id, _worker())
    assert next_claim is not None
    context = SubagentOperatorContext(
        parent_thread_id=completed.thread_id,
        parent_run_id=next_run_id,
        parent_agent_instance_id="agent-parent-next",
        host_refs={"session_id": completed.session_id},
    )
    return _operator_for_parent(
        sessions,
        objects,
        states,
        next_run,
        context,
        AuthorityBox(_authority(next_claim)),
        child_revision_id=child_revision_id,
        child_definition_id=child_definition_id,
    )


async def _additional_parent_thread(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    states: RunStateStore,
    source_context: SubagentOperatorContext,
    source_authority: AuthorityBox,
) -> tuple[FoundationSubagentOperator, SubagentOperatorContext]:
    thread_id = "thread-72727272727272727272727272727272"
    run_id = "run_7272727272727272"
    source = await _complete_run(
        sessions,
        objects,
        states,
        await _run(sessions, source_context.parent_run_id),
        source_authority.current_context,
    )
    source_state = await states.read(source.tenant_id, source.id, expected_thread_id=source.thread_id)
    config = source_state.envelope.effective_agent_config
    state = initialize_fork_state(
        RunStateSeed(
            run_id=run_id,
            agent_id=AGENT_ID,
            agent_revision_id=AGENT_REVISION_ID,
            effective_agent_config=config,
        ),
        source_state.envelope,
        thread_id=thread_id,
    )
    run = _accepted_run(
        run_id=run_id,
        thread_id=thread_id,
        idempotency_key="other-parent",
        request_fingerprint="8" * 64,
        config=config,
    ).model_copy(update={"parent_run_id": source.id, "lineage_kind": RunLineageKind.fork})
    await RunAcceptanceService(sessions, states, RunPayloadStore(objects), clock=lambda: NOW).accept_new_thread(
        session=None,
        thread=Thread(
            id=thread_id,
            version=1,
            queue_version=0,
            tenant_id=run.tenant_id,
            session_id=run.session_id,
            role=ThreadRole.child,
            origin_kind=ThreadOriginKind.fork,
            origin_thread_id=source.thread_id,
            origin_run_id=source.id,
            current_run_id=run.id,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=run,
        state=state,
    )
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "other-parent-lease",
        attempt_id_factory=lambda: "rat_7272727272727272",
    ).claim(run.id, _worker())
    assert claim is not None
    context = SubagentOperatorContext(
        parent_thread_id=thread_id,
        parent_run_id=run_id,
        parent_agent_instance_id="agent-parent-other",
        host_refs={"session_id": run.session_id},
    )
    return _operator_for_parent(
        sessions,
        objects,
        states,
        run,
        context,
        AuthorityBox(_authority(claim)),
    )


def _operator_for_parent(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    states: RunStateStore,
    parent: Run,
    context: SubagentOperatorContext,
    authority: AuthorityBox,
    *,
    child_revision_id: str = CHILD_REVISION_ID,
    child_definition_id: str = CHILD_DEFINITION_ID,
) -> tuple[FoundationSubagentOperator, SubagentOperatorContext]:
    admission = FoundationChildRunAdmissionPreparer(
        sessions,
        states,
        {
            "researcher": ChildRunAdmissionProfile(
                agent_id=CHILD_AGENT_ID,
                agent_revision_id=child_revision_id,
                definition_id=child_definition_id,
                effective_config=effective_agent_config(),
                mcp_tool_snapshot=parent.mcp_tool_snapshot,
                recovery_budget=parent.recovery_budget,
            )
        },
        thread_id_factory=lambda: "thread-70707070707070707070707070707070",
        run_id_factory=lambda: "run_7171717171717171",
        relationship_id_factory=lambda: "crr_7171717171717171",
        clock=lambda: NOW + timedelta(seconds=8),
    )
    return (
        FoundationSubagentOperator(
            sessions,
            authority,
            admission,
            ChildRunAcceptanceService(
                sessions,
                states,
                RunPayloadStore(objects),
                clock=lambda: NOW + timedelta(seconds=8),
            ),
            ThreadInboxStore(sessions, clock=lambda: NOW + timedelta(seconds=8)),
            RunOutcomeService(sessions, RunPayloadStore(objects), clock=lambda: NOW + timedelta(seconds=8)),
            parent_agent_instance_id=context.parent_agent_instance_id,
            host_refs=dict(context.host_refs),
            default_wait_timeout_seconds=0.01,
            wait_poll_interval_seconds=0.001,
            clock=lambda: NOW + timedelta(seconds=8),
        ),
        context,
    )


def _replace_parent_child_revision(config: EffectiveAgentConfig) -> EffectiveAgentConfig:
    edge = config.resolved_subagents[0].model_copy(update={"child_agent_revision_id": REPLACEMENT_CHILD_REVISION_ID})
    candidate = config.model_copy(update={"resolved_subagents": (edge,), "content_digest": "0" * 64})
    return candidate.model_copy(
        update={
            "content_digest": canonical_digest(
                candidate.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
            )
        }
    )


async def _seed_replacement_child_revision(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(sessions) as database:
        database.add(
            AgentRevisionRecord(
                id=REPLACEMENT_CHILD_REVISION_ID,
                organization_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                agent_id=CHILD_AGENT_ID,
                version=2,
                plugin_runtime_mode="on_demand",
                config={},
                config_digest="4" * 64,
                resolved_model={},
                resolved_plugin_versions=[],
                runtime_lock_digest="a" * 64,
                resolved_skills=[],
                connector_tools=[],
                mcp_tools=[],
                resolved_environment=None,
                resolved_subagents=[],
                content_digest="4" * 64,
                source_revision_id=CHILD_REVISION_ID,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
            )
        )
