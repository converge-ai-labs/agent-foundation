from __future__ import annotations

import pytest
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
from a13n_service.interactions.lifecycle_queries import read_workspace_lifecycle_events
from a13n_service.interactions.records import run_record, session_record, thread_record
from a13n_service.lifecycle import (
    LifecycleEntityType,
    LifecycleEventDraft,
    LifecycleProjectionState,
    LifecycleReplayGap,
    append_lifecycle_event,
    read_resource_events,
)
from a13n_service.storage import short_session, transaction
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    SESSION_ID,
    TENANT_ID,
    THREAD_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)

pytestmark = pytest.mark.anyio

RUN_ID = "run_1111111111111111"


def _draft(**changes: object) -> LifecycleEventDraft:
    values: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "entity_type": "run",
        "entity_id": RUN_ID,
        "entity_version": 1,
        "event_type": "run.accepted",
        "mutation_id": "mut_1234567890abcdef",
        "session_id": SESSION_ID,
        "thread_id": THREAD_ID,
        "run_id": RUN_ID,
        "payload": {"status": "accepted"},
        "actor_type": "user",
        "actor_id": USER_ID,
        "occurred_at": NOW,
    }
    values.update(changes)
    return LifecycleEventDraft.model_validate(values)


def test_validates_event_registry_and_entity_correlation() -> None:
    assert _draft().entity_type is LifecycleEntityType.run
    attempt = _draft(
        entity_type="run_attempt",
        entity_id="rat_1234567890abcdef",
        run_attempt_id="rat_1234567890abcdef",
        event_type="run_attempt.leased",
    )
    assert attempt.entity_type is LifecycleEntityType.run_attempt

    with pytest.raises(ValueError, match="does not belong"):
        _draft(event_type="run_attempt.leased")
    with pytest.raises(ValueError, match="sole entity"):
        _draft(run_attempt_id="rat_1234567890abcdef")


def test_rejects_unbounded_payload() -> None:
    with pytest.raises(ValueError, match="encoded size limit"):
        _draft(payload={"value": "x" * (64 * 1024)})


async def _seed_run(sessions: async_sessionmaker[AsyncSession]) -> None:
    session = Session(
        id=SESSION_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    thread = Thread(
        id=THREAD_ID,
        version=1,
        queue_version=0,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        role=ThreadRole.root,
        origin_kind=ThreadOriginKind.new,
        current_run_id=RUN_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    run = Run(
        id=RUN_ID,
        version=1,
        tenant_id=TENANT_ID,
        authority_principal={"principal_type": "user", "principal_id": USER_ID},
        session_id=SESSION_ID,
        thread_id=THREAD_ID,
        lineage_kind=RunLineageKind.root,
        trigger_type="user_input",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config_digest="a" * 64,
        runtime_lock_digest="b" * 64,
        model_execution_observation=effective_agent_config().resolved_model.execution.observation(),
        mcp_tool_snapshot=MCPToolSnapshotRef(
            digest_sha256="c" * 64,
            size_bytes=2,
            content_type="application/vnd.a13n.mcp-tool-snapshot+json",
            schema_version="1",
        ),
        priority=0,
        queue_name="default",
        available_at=NOW,
        next_attempt_fence=1,
        recovery_budget=RecoveryBudget(policy_version="1", max_recovery_attempts=1, max_handoffs=1),
        attempts_started=0,
        recovery_attempts_started=0,
        handoffs_completed=0,
        usage_charged=RecoveryUsage(),
        request_fingerprint="d" * 64,
        status=RunStatus.accepted,
        input_kind=RunInputKind.agent_input,
        input={"message": "hello"},
        created_at=NOW,
        updated_at=NOW,
    )
    async with transaction(sessions) as database:
        database.add(session_record(session))
        database.add(thread_record(thread))
        database.add(run_record(run))


async def test_appends_contiguous_resource_sequence_and_reads_pages(
    interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        first = await append_lifecycle_event(
            database,
            _draft(mutation_id="mut_1111111111111111"),
        )
        second = await append_lifecycle_event(
            database,
            _draft(event_type="run.running", mutation_id="mut_2222222222222222", entity_version=2),
        )
        assert (first.resource_seq, second.resource_seq) == (1, 2)

    async with short_session(interaction_sessions) as database:
        page = await read_resource_events(
            database,
            tenant_id=TENANT_ID,
            entity_type=LifecycleEntityType.run,
            entity_id=RUN_ID,
            after_resource_seq=0,
            limit=1,
        )
        assert tuple(item.event_type for item in page.items) == ("run.accepted",)
        assert page.next_resource_seq == 1
        assert page.retained_resource_seq_floor == 1
        assert page.high_watermark_resource_seq == 2
        assert page.items[0].projection_state is LifecycleProjectionState.pending


async def test_reports_resource_replay_gap_after_retention(
    interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        first = await append_lifecycle_event(database, _draft(mutation_id="mut_1111111111111111"))
        await append_lifecycle_event(
            database,
            _draft(event_type="run.running", mutation_id="mut_2222222222222222", entity_version=2),
        )
        await database.execute(delete(type(first)).where(type(first).seq == first.seq))

    async with short_session(interaction_sessions) as database:
        with pytest.raises(LifecycleReplayGap) as captured:
            await read_resource_events(
                database,
                tenant_id=TENANT_ID,
                entity_type=LifecycleEntityType.run,
                entity_id=RUN_ID,
                after_resource_seq=0,
                limit=50,
            )
    assert captured.value.retained_floor == 2
    assert captured.value.high_watermark == 2


async def test_reads_workspace_events_by_global_sequence(
    interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        first = await append_lifecycle_event(database, _draft(mutation_id="mut_1111111111111111"))
        second = await append_lifecycle_event(
            database,
            _draft(event_type="run.running", mutation_id="mut_2222222222222222", entity_version=2),
        )

    async with short_session(interaction_sessions) as database:
        page = await read_workspace_lifecycle_events(
            database,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            after_seq=first.seq,
            limit=50,
        )

    assert tuple(item.id for item in page.items) == (second.id,)
    assert page.next_seq == second.seq
    assert page.retained_floor == first.seq
    assert page.high_watermark == second.seq


async def test_rolls_back_lifecycle_fact_with_owning_mutation(
    interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_run(interaction_sessions)
    with pytest.raises(RuntimeError, match="abort owning mutation"):
        async with transaction(interaction_sessions) as database:
            await append_lifecycle_event(database, _draft(mutation_id="mut_1111111111111111"))
            raise RuntimeError("abort owning mutation")

    async with short_session(interaction_sessions) as database:
        page = await read_resource_events(
            database,
            tenant_id=TENANT_ID,
            entity_type=LifecycleEntityType.run,
            entity_id=RUN_ID,
            after_resource_seq=0,
            limit=50,
        )

    assert page.items == ()
