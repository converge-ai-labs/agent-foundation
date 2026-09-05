from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.domain import RecoveryUsage, RunAttempt, RunAttemptStatus
from a13n_service.interactions.lifecycle import append_run_attempt_lifecycle, append_run_lifecycle
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.records import run_attempt_record
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.lifecycle.reconciliation import read_workspace_events
from a13n_service.lifecycle.service import LifecycleEventError, LifecycleEventService
from a13n_service.storage import transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import AGENT_ID, ATTEMPT_ID, NOW, TENANT_ID, USER_ID, WORKSPACE_ID


async def _prepare_events(sessions: async_sessionmaker[AsyncSession]) -> LifecycleEventService:
    await seed_run_and_secret(sessions)
    await seed_hook_actor_access(sessions)
    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        await append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id="mut_8181818181818181",
            occurred_at=NOW + timedelta(seconds=1),
            actor_type="user",
            actor_id=USER_ID,
        )
        await append_run_lifecycle(
            database,
            run,
            "run.running",
            mutation_id="mut_8282828282828282",
            occurred_at=NOW + timedelta(seconds=2),
            actor_type="worker",
            actor_id="worker-1",
        )
    return LifecycleEventService(sessions)


@pytest.mark.anyio
async def test_workspace_lifecycle_pagination_uses_principal_bound_cursor(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = await _prepare_events(lifecycle_interaction_sessions)
    first = await service.list_workspace_events(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        limit=1,
        cursor=None,
    )
    assert [event.event_type for event in first.items] == ["run.accepted"]
    assert first.next_cursor is not None
    assert first.retained_floor != first.high_watermark

    second = await service.list_workspace_events(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        limit=1,
        cursor=first.next_cursor,
    )
    assert [event.event_type for event in second.items] == ["run.running"]
    assert second.next_cursor is None

    other_actor = replace(
        hook_actor(),
        boundary_workspace_id="ws_9999999999999999",
    )
    with pytest.raises(LifecycleEventError) as invalid:
        await service.list_workspace_events(
            actor=other_actor,
            workspace_id="ws_9999999999999999",
            limit=1,
            cursor=first.next_cursor,
        )
    assert invalid.value.code == "invalid_cursor"


@pytest.mark.anyio
async def test_direct_agent_viewer_can_reconcile_agent_owned_lifecycle(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = await _prepare_events(lifecycle_interaction_sessions)
    async with transaction(lifecycle_interaction_sessions) as database:
        binding = await database.scalar(
            select(RoleBindingRecord).where(
                RoleBindingRecord.principal_id == USER_ID,
                RoleBindingRecord.resource_type == "workspace",
            )
        )
        assert binding is not None
        binding.resource_type = "agent"
        binding.resource_id = AGENT_ID
        binding.role_key = "viewer"

    page = await service.list_workspace_events(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
    )
    assert [event.run_id for event in page.items] == [RUN_ID, RUN_ID]


@pytest.mark.anyio
async def test_workspace_boundaries_use_tenant_sequence_before_visibility_filter(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _prepare_events(lifecycle_interaction_sessions)
    async with lifecycle_interaction_sessions() as database:
        full_page = await read_workspace_events(
            database,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            visible_agent_ids=None,
            after_seq=0,
            limit=10,
        )
        filtered_page = await read_workspace_events(
            database,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            visible_agent_ids=frozenset({"agt_not_visible_123456"}),
            after_seq=0,
            limit=10,
        )

    assert filtered_page.items == ()
    assert filtered_page.retained_floor == full_page.retained_floor
    assert filtered_page.high_watermark == full_page.high_watermark


@pytest.mark.anyio
async def test_workspace_lifecycle_reports_retention_gap_with_opaque_boundaries(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = await _prepare_events(lifecycle_interaction_sessions)
    initial = await service.list_workspace_events(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
    )
    async with transaction(lifecycle_interaction_sessions) as database:
        first = await database.scalar(select(LifecycleEventRecord).order_by(LifecycleEventRecord.seq))
        assert first is not None
        await database.delete(first)

    with pytest.raises(LifecycleEventError) as gap:
        await service.list_workspace_events(
            actor=hook_actor(),
            workspace_id=WORKSPACE_ID,
            limit=10,
            cursor=initial.retained_floor,
        )
    assert gap.value.code == "lifecycle_replay_gap"
    assert set(gap.value.details) == {"retained_floor", "high_watermark"}


@pytest.mark.anyio
async def test_resource_lifecycle_pages_and_replay_gap_are_explicit(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = await _prepare_events(lifecycle_interaction_sessions)
    page = await service.list_run_events(
        actor=hook_actor(),
        run_id=RUN_ID,
        after_resource_seq=0,
        limit=1,
    )
    assert page.resource_type == "run"
    assert [event.resource_seq for event in page.items] == [1]
    assert page.next_resource_seq == 1
    assert page.retained_resource_seq_floor == 1
    assert page.high_watermark_resource_seq == 2

    async with transaction(lifecycle_interaction_sessions) as database:
        first = await database.scalar(
            select(LifecycleEventRecord)
            .where(LifecycleEventRecord.run_id == RUN_ID)
            .order_by(LifecycleEventRecord.resource_seq)
        )
        assert first is not None
        await database.delete(first)

    with pytest.raises(LifecycleEventError) as gap:
        await service.list_run_events(
            actor=hook_actor(),
            run_id=RUN_ID,
            after_resource_seq=0,
            limit=10,
        )
    assert gap.value.code == "lifecycle_resource_replay_gap"
    assert gap.value.details == {
        "retained_resource_seq_floor": 2,
        "high_watermark_resource_seq": 2,
        "current_resource": f"/api/v1/runs/{RUN_ID}",
    }


@pytest.mark.anyio
async def test_run_attempt_lifecycle_resolves_authority_through_owning_run(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with transaction(lifecycle_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        attempt = run_attempt_record(
            RunAttempt(
                id=ATTEMPT_ID,
                version=1,
                tenant_id=run.tenant_id,
                run_id=run.id,
                attempt_number=1,
                fence=1,
                status=RunAttemptStatus.leased,
                worker_id="worker-1",
                worker_generation="generation-1",
                worker_build_id="build-1",
                runtime_lock_digest=run.runtime_lock_digest,
                model_execution_observation=run.to_resource().model_execution_observation,
                lease_token_digest="d" * 64,
                lease_expires_at=NOW + timedelta(minutes=5),
                heartbeat_at=NOW,
                usage=RecoveryUsage(),
                created_at=NOW,
                claimed_at=NOW,
                updated_at=NOW,
            )
        )
        database.add(attempt)
        await database.flush()
        await append_run_attempt_lifecycle(
            database,
            run,
            attempt,
            "run_attempt.leased",
            mutation_id="mut_8383838383838383",
            occurred_at=NOW + timedelta(seconds=1),
        )

    page = await LifecycleEventService(lifecycle_interaction_sessions).list_run_attempt_events(
        actor=hook_actor(),
        run_attempt_id=ATTEMPT_ID,
        after_resource_seq=0,
        limit=10,
    )
    assert page.resource_type == "run_attempt"
    assert [event.event_type for event in page.items] == ["run_attempt.leased"]
    assert page.high_watermark_resource_seq == 1
