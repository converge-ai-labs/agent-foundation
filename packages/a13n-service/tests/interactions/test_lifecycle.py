from __future__ import annotations

from datetime import datetime, timedelta

import anyio
import pytest
from a13n_service.interactions.domain import (
    ExecutionBudget,
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
    RunUsage,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.lifecycle_queries import read_workspace_lifecycle_events
from a13n_service.interactions.records import run_record, session_record, thread_record
from a13n_service.lifecycle import (
    LifecycleEntityType,
    LifecycleEvent,
    LifecycleEventDraft,
    LifecycleEventRecord,
    LifecycleProjectionState,
    LifecycleReplayGap,
    append_lifecycle_event,
    claim_lifecycle_projections,
    complete_lifecycle_projection,
    read_resource_events,
)
from a13n_service.run_stream import (
    CompleteRunStream,
    LifecycleRunStreamProjector,
    RedisRunStream,
    RetainedReplayUnavailable,
    RunReplaySnapshot,
    RunReplayStore,
    RunStreamEvent,
    RunStreamReplayGap,
    deterministic_item_id,
    deterministic_run_stream_event_id,
)
from a13n_service.storage import ObjectStore, short_session, transaction
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.run_stream.support import opening_event, publication_failure

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

pytestmark = pytest.mark.anyio

RUN_ID = "run_1111111111111111"


class _FailOnceCloseRunStream(RedisRunStream):
    def __init__(self, redis: Redis) -> None:
        super().__init__(redis)
        self._fail_close = True

    async def close(self, organization_id: str, run_id: str, *, closed_at: datetime) -> None:
        if self._fail_close:
            self._fail_close = False
            raise RuntimeError("transient close failure")
        await super().close(organization_id, run_id, closed_at=closed_at)


class _FailingAppendRunStream(RedisRunStream):
    async def initialize(
        self, organization_id: str, accepted: RunStreamEvent, *, allow_create: bool, expected_server_id: str
    ) -> str:
        await super().initialize(
            organization_id, accepted, allow_create=allow_create, expected_server_id=expected_server_id
        )
        raise RuntimeError("persistent append failure")

    async def append_lifecycle(self, organization_id: str, event: RunStreamEvent) -> str:
        raise RuntimeError("persistent append failure")


class _FailingAppendAndMarkerRunStream(_FailingAppendRunStream):
    async def mark_lifecycle_incomplete(self, organization_id: str, run_id: str) -> None:
        raise RuntimeError("persistent marker failure")


class _FailingRunReplayStore(RunReplayStore):
    async def publish(
        self,
        organization_id: str,
        run_id: str,
        source: CompleteRunStream,
    ) -> RunReplaySnapshot:
        raise RuntimeError("persistent object-store failure")


def _draft(**changes: object) -> LifecycleEventDraft:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
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
        organization_id=ORGANIZATION_ID,
        workspace_id=WORKSPACE_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    thread = Thread(
        id=THREAD_ID,
        version=1,
        queue_version=0,
        organization_id=ORGANIZATION_ID,
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
        organization_id=ORGANIZATION_ID,
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
        priority=0,
        queue_name="default",
        available_at=NOW,
        execution_budget=ExecutionBudget(policy_version="1", max_attempts=1, max_handoffs=1),
        attempts_started=0,
        attempts_charged=0,
        handoffs_completed=0,
        usage_charged=RunUsage(),
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
            organization_id=ORGANIZATION_ID,
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
                organization_id=ORGANIZATION_ID,
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
            organization_id=ORGANIZATION_ID,
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
            organization_id=ORGANIZATION_ID,
            entity_type=LifecycleEntityType.run,
            entity_id=RUN_ID,
            after_resource_seq=0,
            limit=50,
        )

    assert page.items == ()


async def test_projects_lifecycle_in_order_and_publishes_terminal_replay(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        await append_lifecycle_event(database, _draft(mutation_id="mut_1111111111111111"))
        terminal = await append_lifecycle_event(
            database,
            _draft(
                event_type="run.completed",
                mutation_id="mut_2222222222222222",
                entity_version=2,
                payload={"status": "completed"},
            ),
        )

    stream = RedisRunStream(redis_client)
    replay = RunReplayStore(interaction_object_store)
    terminal_projections: list[tuple[LifecycleEvent, CompleteRunStream]] = []

    async def project_terminal(event: LifecycleEvent, source: CompleteRunStream) -> None:
        terminal_projections.append((event, source))

    projector = LifecycleRunStreamProjector(
        interaction_sessions,
        stream,
        replay,
        worker_id="projection-worker-1",
        terminal_projection=project_terminal,
        clock=lambda: NOW,
    )

    assert await projector.project_once(limit=16) == 1
    first_page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert tuple(entry.event.event_type for entry in first_page.items) == ("run.accepted",)
    assert not first_page.closed
    assert await projector.project_once(limit=16) == 1

    terminal_page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    snapshot = await replay.read(ORGANIZATION_ID, RUN_ID)
    assert terminal_page.closed
    assert tuple(entry.event.event_type for entry in terminal_page.items) == ("run.accepted", "run.completed")
    assert tuple(entry.event.event_type for entry in snapshot.events) == ("run.accepted", "run.completed")
    assert len(terminal_projections) == 1
    projected_event, projected_source = terminal_projections[0]
    assert projected_event.id == terminal.id
    assert projected_source.entries == terminal_page.items
    assert projected_source.closed_at == snapshot.closed_at
    async with short_session(interaction_sessions) as database:
        projection_states = tuple(
            await database.scalars(select(LifecycleEventRecord.projection_state).order_by(LifecycleEventRecord.seq))
        )
    assert projection_states == ("projected", "projected")


@pytest.mark.parametrize("close_failure", ["before", "receipts", "retention"])
async def test_terminal_projection_interrupts_open_items_before_stream_close(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
    close_failure: str,
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        await append_lifecycle_event(database, _draft(mutation_id="mut_1111111111111111"))
        terminal = await append_lifecycle_event(
            database,
            _draft(
                event_type="run.completed",
                mutation_id="mut_2222222222222222",
                entity_version=2,
                payload={"status": "completed"},
            ),
        )
    stream = _FailOnceCloseRunStream(redis_client) if close_failure == "before" else RedisRunStream(redis_client)
    replay = RunReplayStore(interaction_object_store)
    projector = LifecycleRunStreamProjector(
        interaction_sessions,
        stream,
        replay,
        worker_id="projection-worker-1",
        retry_after=timedelta(0),
        clock=lambda: NOW,
    )
    assert await projector.project_once() == 1
    item_id = deterministic_item_id(RUN_ID, "text_message", "message-1")
    await stream.activate(
        ORGANIZATION_ID,
        opening_event(RUN_ID, THREAD_ID, attempt_id="rat_1234567890abcdef"),
        attempt_number=1,
        reason=None,
        allow_create=True,
    )
    first_item_stream_id = await stream.append(
        ORGANIZATION_ID,
        RunStreamEvent(
            event_id=deterministic_run_stream_event_id("test", "open-item"),
            event_type="agui.text_message_start",
            run_id=RUN_ID,
            thread_id=THREAD_ID,
            run_attempt_id="rat_1234567890abcdef",
            harness_run_id="harness-run-1",
            item_id=item_id,
            occurred_at=NOW,
            payload={"item_kind": "text_message"},
        ),
        attempt_number=1,
    )

    healthy = stream._script
    if close_failure != "before":
        stream._script = redis_client.register_script(publication_failure("close", after=close_failure))
    assert await projector.project_once() == 1
    stream._script = healthy
    if close_failure != "before":
        with pytest.raises(RunStreamReplayGap):
            await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    else:
        failed_page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
        assert not failed_page.closed
        assert failed_page.items[-1].event.event_type == "item.interrupted"
    assert await projector.project_once() == 1

    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    snapshot = await replay.read(ORGANIZATION_ID, RUN_ID)
    interrupted = page.items[-1]
    assert page.closed
    assert tuple(entry.event.event_type for entry in page.items) == (
        "run.accepted",
        "run_attempt.leased",
        "agui.text_message_start",
        "run.completed",
        "item.interrupted",
    )
    assert interrupted.event.lifecycle_event_id == terminal.id
    assert interrupted.event.payload["first_stream_id"] == first_item_stream_id
    assert interrupted.event.payload["last_content_stream_id"] == first_item_stream_id
    assert snapshot.items[0].state == "interrupted"
    assert snapshot.items[0].last_stream_id == interrupted.stream_id


async def test_background_projector_drains_successive_claim_batches(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        await append_lifecycle_event(database, _draft(mutation_id="mut_3131313131313131"))
        await append_lifecycle_event(
            database,
            _draft(
                event_type="run.running",
                mutation_id="mut_3232323232323232",
                entity_version=2,
                payload={"status": "running"},
            ),
        )
    stream = RedisRunStream(redis_client)
    projector = LifecycleRunStreamProjector(
        interaction_sessions,
        stream,
        RunReplayStore(interaction_object_store),
        worker_id="projection-worker-1",
        poll_interval_seconds=0.01,
        claim_limit=1,
        clock=lambda: NOW,
    )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(projector.run)
        with anyio.fail_after(2):
            while True:
                async with short_session(interaction_sessions) as database:
                    states = tuple(
                        await database.scalars(
                            select(LifecycleEventRecord.projection_state).order_by(LifecycleEventRecord.seq)
                        )
                    )
                if states == ("projected", "projected"):
                    break
                await anyio.sleep(0.01)
        tasks.cancel_scope.cancel()

    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert tuple(entry.event.event_type for entry in page.items) == ("run.accepted", "run.running")


async def test_expired_projection_owner_cannot_settle_reclaimed_fact(
    interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        await append_lifecycle_event(database, _draft(mutation_id="mut_1111111111111111"))
    async with transaction(interaction_sessions) as database:
        first = (
            await claim_lifecycle_projections(
                database,
                lease_owner="projection-worker-old",
                now=NOW,
                lease_duration=timedelta(seconds=1),
                limit=1,
            )
        )[0]
    async with transaction(interaction_sessions) as database:
        assert not await complete_lifecycle_projection(
            database,
            first,
            projected_at=NOW + timedelta(seconds=2),
        )
    async with transaction(interaction_sessions) as database:
        replacement = (
            await claim_lifecycle_projections(
                database,
                lease_owner="projection-worker-new",
                now=NOW + timedelta(seconds=2),
                lease_duration=timedelta(seconds=30),
                limit=1,
            )
        )[0]
        assert await complete_lifecycle_projection(
            database,
            replacement,
            projected_at=NOW + timedelta(seconds=3),
        )


async def test_projection_failure_retries_then_abandons_without_mutating_fact(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        source = await append_lifecycle_event(database, _draft(mutation_id="mut_1111111111111111"))
    stream = _FailingAppendRunStream(redis_client)
    times = iter((NOW, NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=2)))
    projector = LifecycleRunStreamProjector(
        interaction_sessions,
        stream,
        RunReplayStore(interaction_object_store),
        worker_id="projection-worker-1",
        retry_after=timedelta(seconds=1),
        max_attempts=2,
        clock=lambda: next(times),
    )

    assert await projector.project_once() == 1
    async with short_session(interaction_sessions) as database:
        retrying = await database.get(LifecycleEventRecord, source.seq)
        assert retrying is not None
        assert (retrying.projection_state, retrying.projection_attempts) == ("retry_wait", 1)
        assert retrying.event_type == "run.accepted"

    assert await projector.project_once() == 1
    async with short_session(interaction_sessions) as database:
        abandoned = await database.get(LifecycleEventRecord, source.seq)
        assert abandoned is not None
        assert (abandoned.projection_state, abandoned.projection_attempts) == ("abandoned", 2)
        assert abandoned.projection_error_json is not None
        assert abandoned.projection_error_json["code"] == "run_stream_projection_failed"
    with pytest.raises(RunStreamReplayGap):
        await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)


async def test_abandoned_projection_prevents_complete_snapshot_from_later_terminal_event(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        await append_lifecycle_event(database, _draft(mutation_id="mut_4141414141414141"))
    failed_stream = _FailingAppendRunStream(redis_client)
    replay = RunReplayStore(interaction_object_store)
    failed_projector = LifecycleRunStreamProjector(
        interaction_sessions,
        failed_stream,
        replay,
        worker_id="projection-worker-1",
        max_attempts=1,
        clock=lambda: NOW,
    )

    assert await failed_projector.project_once() == 1
    async with transaction(interaction_sessions) as database:
        await append_lifecycle_event(
            database,
            _draft(
                event_type="run.completed",
                mutation_id="mut_4242424242424242",
                entity_version=2,
                payload={"status": "completed"},
            ),
        )

    stream = RedisRunStream(redis_client)
    terminal_projector = LifecycleRunStreamProjector(
        interaction_sessions,
        stream,
        replay,
        worker_id="projection-worker-2",
        max_attempts=1,
        clock=lambda: NOW,
    )
    assert await terminal_projector.project_once() == 1

    with pytest.raises(RunStreamReplayGap):
        await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    with pytest.raises(RetainedReplayUnavailable):
        await replay.read(ORGANIZATION_ID, RUN_ID)
    async with short_session(interaction_sessions) as database:
        states = tuple(
            await database.scalars(select(LifecycleEventRecord.projection_state).order_by(LifecycleEventRecord.seq))
        )
    assert states == ("abandoned", "projected")


async def test_projection_abandons_after_bounded_retries_when_gap_marker_is_unavailable(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        source = await append_lifecycle_event(database, _draft(mutation_id="mut_4343434343434343"))
    projector = LifecycleRunStreamProjector(
        interaction_sessions,
        _FailingAppendAndMarkerRunStream(redis_client),
        RunReplayStore(interaction_object_store),
        worker_id="projection-worker-1",
        retry_after=timedelta(0),
        max_attempts=1,
        clock=lambda: NOW,
    )

    assert await projector.project_once() == 1

    async with short_session(interaction_sessions) as database:
        record = await database.get(LifecycleEventRecord, source.seq)
        assert record is not None
        assert (record.projection_state, record.projection_attempts) == ("abandoned", 1)


async def test_replay_publication_failure_preserves_complete_live_source(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    await _seed_run(interaction_sessions)
    async with transaction(interaction_sessions) as database:
        await append_lifecycle_event(database, _draft(mutation_id="mut_5151515151515151"))
        terminal = await append_lifecycle_event(
            database,
            _draft(
                event_type="run.completed",
                mutation_id="mut_5252525252525252",
                entity_version=2,
                payload={"status": "completed"},
            ),
        )
    stream = RedisRunStream(redis_client)
    projector = LifecycleRunStreamProjector(
        interaction_sessions,
        stream,
        _FailingRunReplayStore(interaction_object_store),
        worker_id="projection-worker-1",
        max_attempts=1,
        clock=lambda: NOW,
    )

    assert await projector.project_once() == 1
    assert await projector.project_once() == 1

    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert page.closed
    assert tuple(entry.event.event_type for entry in page.items) == ("run.accepted", "run.completed")
    with pytest.raises(RetainedReplayUnavailable):
        await RunReplayStore(interaction_object_store).read(ORGANIZATION_ID, RUN_ID)
    async with short_session(interaction_sessions) as database:
        record = await database.get(LifecycleEventRecord, terminal.seq)
        assert record is not None
        assert record.projection_state == "abandoned"
        assert record.projection_error_json is not None
        assert record.projection_error_json["code"] == "run_replay_publication_failed"
