from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.interactions import (
    MCPToolSnapshotRef,
    RecoveryBudget,
    RecoveryUsage,
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.acceptance import RunAcceptanceError, RunAcceptanceService
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_retry_state,
    initialize_start_state,
)
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.storage import ObjectStore, short_session, transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    SESSION_ID,
    TENANT_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)

pytestmark = pytest.mark.anyio


def _accepted_run(
    *,
    run_id: str,
    thread_id: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> Run:
    config = effective_agent_config()
    return Run(
        id=run_id,
        version=1,
        tenant_id=TENANT_ID,
        authority_principal=PrincipalRef(
            principal_type=PrincipalType.user,
            principal_id=USER_ID,
        ),
        session_id=SESSION_ID,
        thread_id=thread_id,
        lineage_kind=RunLineageKind.root,
        trigger_type="user_input",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config_digest=config.content_digest,
        runtime_lock_digest=config.runtime_lock_digest,
        model_execution_observation=config.resolved_model.execution.observation(),
        mcp_tool_snapshot=MCPToolSnapshotRef(
            digest_sha256="d" * 64,
            size_bytes=2,
            content_type="application/vnd.a13n.mcp-tool-snapshot+json",
            schema_version="1",
        ),
        priority=0,
        queue_name="default",
        available_at=NOW,
        next_attempt_fence=1,
        recovery_budget=RecoveryBudget(
            policy_version="1",
            max_recovery_attempts=3,
            max_handoffs=2,
        ),
        attempts_started=0,
        recovery_attempts_started=0,
        handoffs_completed=0,
        usage_charged=RecoveryUsage(),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        status=RunStatus.accepted,
        input_kind=RunInputKind.agent_input,
        input={"schema_version": "1", "content": "hello"},
        created_at=NOW,
        updated_at=NOW,
    )


async def test_accepts_prepared_root_state_and_round_trips_the_run(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states = RunStateStore(interaction_object_store)
    service = RunAcceptanceService(interaction_sessions, states, clock=lambda: NOW)
    seed = RunStateSeed(
        run_id="run_1111111111111111",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    state = initialize_start_state(seed)
    run = _accepted_run(
        run_id=seed.run_id,
        thread_id=state.thread_id,
        idempotency_key="start-1",
        request_fingerprint="1" * 64,
    )
    session = Session(
        id=SESSION_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    thread = Thread(
        id=state.thread_id,
        version=1,
        queue_version=0,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        role=ThreadRole.root,
        origin_kind=ThreadOriginKind.new,
        current_run_id=run.id,
        created_at=NOW,
        updated_at=NOW,
    )

    receipt = await service.accept_new_thread(session=session, thread=thread, run=run, state=state)

    assert receipt.thread_version == 1
    assert await states.read(TENANT_ID, run.id, expected_thread_id=thread.id)
    async with short_session(interaction_sessions) as database:
        record = await database.get(RunRecord, run.id)
        assert record is not None
        assert record.to_resource() == run


async def test_root_retry_is_atomic_exact_and_idempotent(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states = RunStateStore(interaction_object_store)
    service = RunAcceptanceService(interaction_sessions, states, clock=lambda: NOW + timedelta(seconds=2))
    first_seed = RunStateSeed(
        run_id="run_2222222222222222",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    first_state = initialize_start_state(first_seed)
    first = _accepted_run(
        run_id=first_seed.run_id,
        thread_id=first_state.thread_id,
        idempotency_key="start-2",
        request_fingerprint="2" * 64,
    )
    await service.accept_new_thread(
        session=Session(
            id=SESSION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW,
        ),
        thread=Thread(
            id=first_state.thread_id,
            version=1,
            queue_version=0,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            current_run_id=first.id,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=first,
        state=first_state,
    )
    async with transaction(interaction_sessions) as database:
        first_record = await database.get(RunRecord, first.id)
        thread_record = await database.get(ThreadRecord, first.thread_id)
        assert first_record is not None and thread_record is not None
        first_record.status = RunStatus.failed.value
        first_record.failure_json = {
            "code": "preparation_failed",
            "message": "Preparation failed.",
            "details": {},
            "retry_hint": "none",
        }
        first_record.sealed_at = NOW + timedelta(seconds=1)
        thread_record.version = 2
        thread_record.updated_at = NOW + timedelta(seconds=1)

    second_seed = RunStateSeed(
        run_id="run_3333333333333333",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    second_state = initialize_retry_state(
        second_seed,
        thread_id=first.thread_id,
        source_lineage_kind=RunLineageKind.root,
        source_input_kind=RunInputKind.agent_input,
        parent=None,
    )
    second = _accepted_run(
        run_id=second_seed.run_id,
        thread_id=first.thread_id,
        idempotency_key="continue-after-failure",
        request_fingerprint="3" * 64,
    ).model_copy(update={"retry_of_run_id": first.id})
    await states.create(TENANT_ID, second_state)

    receipt = await service.advance_thread(
        run=second,
        state=second_state,
        expected_thread_version=2,
        expected_current_run_id=first.id,
        expected_head_run_id=None,
        next_head_run_id=None,
    )
    replay = await service.advance_thread(
        run=second,
        state=second_state,
        expected_thread_version=2,
        expected_current_run_id=first.id,
        expected_head_run_id=None,
        next_head_run_id=None,
    )

    assert receipt == replay
    assert receipt.thread_version == 3
    async with short_session(interaction_sessions) as database:
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == first.thread_id))
        assert thread is not None
        assert (thread.version, thread.current_run_id, thread.head_run_id) == (3, second.id, None)

    conflicting = second.model_copy(
        update={
            "id": "run_4444444444444444",
            "request_fingerprint": "4" * 64,
        }
    )
    conflicting_state = second_state.model_copy(update={"run_id": conflicting.id})
    with pytest.raises(RunAcceptanceError, match="different Run intent"):
        await service.advance_thread(
            run=conflicting,
            state=conflicting_state,
            expected_thread_version=3,
            expected_current_run_id=second.id,
            expected_head_run_id=None,
            next_head_run_id=None,
        )
