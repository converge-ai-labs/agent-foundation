from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.environments.domain import (
    EnvironmentTargetIdentity,
    EnvironmentTargetRetentionBehavior,
    environment_target_identity_digest,
)
from a13n_service.environments.keepalive import (
    EnvironmentKeepaliveLoop,
    EnvironmentKeepaliveStore,
    KeepaliveSourceBinding,
    PreparedEnvironmentKeepalive,
)
from a13n_service.environments.models import EnvironmentTargetRecord
from a13n_service.environments.targets import upsert_environment_target
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions import Run, Session, Thread, ThreadOriginKind, ThreadRole
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.initialization import RunStateSeed, initialize_start_state
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeError, RunOutcomeService
from a13n_service.storage import ObjectStore, short_session, transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    ENVIRONMENT_TARGET_ID,
    NOW,
    SESSION_ID,
    TENANT_ID,
    THREAD_ID,
    WORKSPACE_ID,
    effective_agent_config,
    environment_execution_config,
)
from .test_acceptance import _accepted_run

pytestmark = pytest.mark.anyio


class _Sources:
    def __init__(self, callback) -> None:
        self._callback = callback
        self.seen: list[KeepaliveSourceBinding] = []

    async def prepare(self, source: KeepaliveSourceBinding) -> PreparedEnvironmentKeepalive:
        self.seen.append(source)
        return PreparedEnvironmentKeepalive(
            target_id=source.target_id,
            binding_id=source.binding_id,
            provider_key=source.provider_key,
            ensure_retained_until=self._callback,
        )


async def test_run_membership_activates_idles_retires_and_reactivates_target(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    run = await _accept_environment_run(interaction_sessions, interaction_object_store)
    async with short_session(interaction_sessions) as database:
        target = await database.get(EnvironmentTargetRecord, ENVIRONMENT_TARGET_ID)
        assert target is not None
        assert (target.status, target.active_run_count, target.idle_at) == ("active", 1, None)

    sealed_at = NOW + timedelta(seconds=1)
    outcome = RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: sealed_at,
    )
    await outcome.cancel(
        tenant_id=TENANT_ID,
        run_id=run.id,
        expected_run_version=1,
        expected_thread_version=1,
        failure=SafeFailure(code="cancelled_by_user", message="The Run was cancelled."),
    )
    async with short_session(interaction_sessions) as database:
        target = await database.get(EnvironmentTargetRecord, ENVIRONMENT_TARGET_ID)
        assert target is not None
        assert target.status == "idle"
        assert target.active_run_count == 0
        assert target.idle_at is not None and _aware(target.idle_at) == sealed_at
        assert target.retire_after is not None
        retire_after = target.retire_after

    with pytest.raises(RunOutcomeError):
        await outcome.cancel(
            tenant_id=TENANT_ID,
            run_id=run.id,
            expected_run_version=1,
            expected_thread_version=1,
            failure=SafeFailure(code="cancelled_by_user", message="The Run was cancelled."),
        )
    retirement_clock = [_aware(retire_after) + timedelta(seconds=1)]
    store = EnvironmentKeepaliveStore(interaction_sessions, clock=lambda: retirement_clock[0])
    assert await store.retire_due() == 1
    retirement_clock[0] += timedelta(seconds=2)
    assert await store.delete_unreferenced_retired(audit_retention=timedelta(seconds=1)) == 0

    await _accept_environment_run(
        interaction_sessions,
        interaction_object_store,
        session_id="sess_2222222222222222",
        thread_id="thread-22222222222222222222222222222222",
        run_id="run_2222222222222222",
    )
    async with short_session(interaction_sessions) as database:
        target = await database.get(EnvironmentTargetRecord, ENVIRONMENT_TARGET_ID)
        assert target is not None
        assert (target.status, target.active_run_count, target.retire_after) == ("active", 1, None)


async def test_keepalive_loop_acknowledges_and_schedules_refresh(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await _accept_environment_run(interaction_sessions, interaction_object_store)
    await _enable_retention(interaction_sessions)
    clock = [NOW]
    calls: list[tuple[datetime, str]] = []

    async def retain(deadline: datetime, operation_id: str) -> datetime:
        calls.append((deadline, operation_id))
        return deadline

    sources = _Sources(retain)
    store = EnvironmentKeepaliveStore(
        interaction_sessions,
        compatible_provider_keys=("test.attachment",),
        clock=lambda: clock[0],
    )
    loop = _loop(store, sources)

    assert await loop.reconcile_once() == 1
    assert len(calls) == 1
    assert calls[0][0] == NOW + timedelta(seconds=120)
    assert calls[0][1].startswith("envkop_")
    assert len(sources.seen) == 1
    async with short_session(interaction_sessions) as database:
        target = await database.get(EnvironmentTargetRecord, ENVIRONMENT_TARGET_ID)
        assert target is not None
        assert target.acknowledged_alive_until is not None
        assert _aware(target.acknowledged_alive_until) == calls[0][0]
        assert target.next_keepalive_at is not None
        assert _aware(target.next_keepalive_at) == NOW + timedelta(seconds=100)
        assert target.keeper_owner_worker_generation is None


async def test_unknown_outcome_retries_same_operation_identity(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await _accept_environment_run(interaction_sessions, interaction_object_store)
    await _enable_retention(interaction_sessions)
    clock = [NOW]
    calls: list[str] = []

    async def retain(deadline: datetime, operation_id: str) -> datetime:
        calls.append(operation_id)
        if len(calls) == 1:
            raise TimeoutError
        return deadline

    store = EnvironmentKeepaliveStore(
        interaction_sessions,
        compatible_provider_keys=("test.attachment",),
        clock=lambda: clock[0],
    )
    loop = _loop(store, _Sources(retain))
    assert await loop.reconcile_once() == 1
    clock[0] += timedelta(seconds=11)
    assert await loop.reconcile_once() == 1
    assert len(calls) == 2 and calls[0] == calls[1]
    async with short_session(interaction_sessions) as database:
        target = await database.get(EnvironmentTargetRecord, ENVIRONMENT_TARGET_ID)
        assert target is not None
        assert target.last_error is None
        assert target.acknowledged_alive_until is not None


async def test_expired_lease_takeover_fences_late_acknowledgement(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await _accept_environment_run(interaction_sessions, interaction_object_store)
    await _enable_retention(interaction_sessions)
    clock = [NOW]
    store = EnvironmentKeepaliveStore(
        interaction_sessions,
        compatible_provider_keys=("test.attachment",),
        clock=lambda: clock[0],
    )
    source = (await store.source_candidates(ENVIRONMENT_TARGET_ID))[0]

    async def retain(deadline: datetime, operation_id: str) -> datetime:
        del operation_id
        return deadline

    prepared = PreparedEnvironmentKeepalive(
        target_id=source.target_id,
        binding_id=source.binding_id,
        provider_key=source.provider_key,
        ensure_retained_until=retain,
    )
    first = await store.claim(
        prepared,
        worker_generation="worker-generation-1",
        lease_duration=timedelta(seconds=30),
        retention_window=timedelta(seconds=120),
    )
    assert first is not None
    clock[0] += timedelta(seconds=31)
    second = await store.claim(
        prepared,
        worker_generation="worker-generation-2",
        lease_duration=timedelta(seconds=30),
        retention_window=timedelta(seconds=120),
    )
    assert second is not None
    assert second.claim_generation > first.claim_generation
    assert second.operation_id == first.operation_id
    assert not await store.acknowledge(
        first,
        acknowledged_alive_until=first.requested_alive_until,
        refresh_margin=timedelta(seconds=20),
    )
    assert await store.acknowledge(
        second,
        acknowledged_alive_until=second.requested_alive_until,
        refresh_margin=timedelta(seconds=20),
    )


async def test_keeper_stably_skips_an_unusable_active_source_binding(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    await _accept_environment_run(interaction_sessions, interaction_object_store)
    await _accept_environment_run(
        interaction_sessions,
        interaction_object_store,
        session_id="sess_3333333333333333",
        thread_id="thread-33333333333333333333333333333333",
        run_id="run_3333333333333333",
    )
    await _enable_retention(interaction_sessions)
    store = EnvironmentKeepaliveStore(
        interaction_sessions,
        compatible_provider_keys=("test.attachment",),
        clock=lambda: NOW,
    )
    candidates = await store.source_candidates(ENVIRONMENT_TARGET_ID)
    assert len(candidates) == 2
    calls: list[str] = []

    class SelectiveSources:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def prepare(
            self,
            source: KeepaliveSourceBinding,
        ) -> PreparedEnvironmentKeepalive | None:
            self.seen.append(source.binding_id)
            if source.binding_id == candidates[0].binding_id:
                return None

            async def retain(deadline: datetime, operation_id: str) -> datetime:
                calls.append(operation_id)
                return deadline

            return PreparedEnvironmentKeepalive(
                target_id=source.target_id,
                binding_id=source.binding_id,
                provider_key=source.provider_key,
                ensure_retained_until=retain,
            )

    sources = SelectiveSources()
    loop = EnvironmentKeepaliveLoop(
        store,
        sources,
        worker_generation="worker-generation-1",
        lease_seconds=30,
        retention_window_seconds=120,
        refresh_margin_seconds=20,
        retry_backoff_seconds=10,
    )
    assert await loop.reconcile_once() == 1
    assert sources.seen == [candidates[0].binding_id, candidates[1].binding_id]
    assert len(calls) == 1
    async with short_session(interaction_sessions) as database:
        target = await database.get(EnvironmentTargetRecord, ENVIRONMENT_TARGET_ID)
        assert target is not None and target.active_run_count == 2


async def test_unreferenced_retired_target_is_deleted_only_after_audit_retention(
    interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    identity = EnvironmentTargetIdentity(target_key="unreferenced-target")
    async with transaction(interaction_sessions) as database:
        target = await upsert_environment_target(
            database,
            provider_key="test.attachment",
            identity_schema_version="1",
            target_key=identity.target_key,
            target_identity_digest_sha256=environment_target_identity_digest(
                provider_key="test.attachment",
                identity_schema_version="1",
                identity=identity,
            ),
            retention_behavior=EnvironmentTargetRetentionBehavior.none,
            now=NOW,
        )
        target_id = target.id
    clock = [NOW + timedelta(hours=1, seconds=1)]
    store = EnvironmentKeepaliveStore(interaction_sessions, clock=lambda: clock[0])
    assert await store.retire_due() >= 1
    assert await store.delete_unreferenced_retired(audit_retention=timedelta(seconds=10)) == 0
    clock[0] += timedelta(seconds=11)
    assert await store.delete_unreferenced_retired(audit_retention=timedelta(seconds=10)) >= 1
    async with short_session(interaction_sessions) as database:
        assert await database.get(EnvironmentTargetRecord, target_id) is None


async def _accept_environment_run(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    *,
    session_id: str = SESSION_ID,
    thread_id: str = THREAD_ID,
    run_id: str = "run_9191919191919191",
) -> Run:
    config = effective_agent_config(environment=environment_execution_config())
    state = initialize_start_state(
        RunStateSeed(
            run_id=run_id,
            agent_id=AGENT_ID,
            agent_revision_id=AGENT_REVISION_ID,
            effective_agent_config=config,
        ),
        thread_id=thread_id,
    )
    run = _accepted_run(
        run_id=run_id,
        thread_id=thread_id,
        idempotency_key=f"accept-{run_id}",
        request_fingerprint=("9" if run_id.endswith("91") else "2") * 64,
        config=config,
    ).model_copy(update={"session_id": session_id})
    await RunAcceptanceService(
        sessions,
        RunStateStore(objects),
        RunPayloadStore(objects),
        InlineHookValidator(EndpointPolicy()),
        clock=lambda: NOW,
    ).accept_new_thread(
        session=Session(
            id=session_id,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW,
        ),
        thread=Thread(
            id=thread_id,
            version=1,
            queue_version=0,
            tenant_id=TENANT_ID,
            session_id=session_id,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            current_run_id=run.id,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=run,
        state=state,
    )
    return run


async def _enable_retention(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(sessions) as database:
        target = await database.get(EnvironmentTargetRecord, ENVIRONMENT_TARGET_ID)
        assert target is not None
        target.retention_behavior = "while_execution_active"
        target.next_keepalive_at = NOW


def _loop(store: EnvironmentKeepaliveStore, sources: _Sources) -> EnvironmentKeepaliveLoop:
    return EnvironmentKeepaliveLoop(
        store,
        sources,
        worker_generation="worker-generation-1",
        poll_interval_seconds=1,
        lease_seconds=30,
        retention_window_seconds=120,
        refresh_margin_seconds=20,
        call_timeout_seconds=1,
        retry_backoff_seconds=10,
        max_concurrency=2,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=NOW.tzinfo)
