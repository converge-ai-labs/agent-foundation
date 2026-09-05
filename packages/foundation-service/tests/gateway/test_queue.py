from __future__ import annotations

import pytest
from a13n_service.interactions.control_domain import (
    ConsumeQueuedSubmissionRequest,
    InterruptRequest,
    QueuedSubmissionState,
    ReorderQueuedSubmissionsRequest,
    ThreadRunSubmissionIntent,
    ThreadRunSubmissionRequest,
    UpdateQueuedSubmissionRequest,
    WaitingResolutionDefaults,
)
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.interactions.submissions import DeleteQueuedSubmissionRequest, QueuedSubmissionService
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.gateway.test_commands import (
    _actor,
    _commands,
    _complete_run,
    _Freezing,
    _frozen,
    _Preparation,
    _request,
    _wait_run,
)
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import NOW, WORKSPACE_ID
from tests.interactions.test_queue import _inline_hooks

pytestmark = pytest.mark.anyio


def _intent(text: str) -> ThreadRunSubmissionIntent:
    return ThreadRunSubmissionIntent(input=_request(text).input)


async def _service(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> QueuedSubmissionService:
    objects = await LocalObjectStore.create(tmp_path / "queue-service-objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    return QueuedSubmissionService(
        sessions,
        QueuedSubmissionStore(sessions, _inline_hooks(), clock=lambda: NOW),
        commands,
        clock=lambda: NOW,
    )


async def _active_thread(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> str:
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    accepted = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="queue-source",
        request=_request(),
    )
    return accepted.thread_id


async def _submission_setup(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path,
):
    objects = await LocalObjectStore.create(tmp_path / "submission-objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    accepted = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="submission-source",
        request=_request(),
    )
    service = QueuedSubmissionService(
        sessions,
        QueuedSubmissionStore(sessions, _inline_hooks(), clock=lambda: NOW),
        commands,
        clock=lambda: NOW,
    )
    return service, commands, objects, accepted


async def test_thread_submission_queues_while_current_run_is_active_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _commands_value, _objects, source = await _submission_setup(
        lifecycle_interaction_sessions,
        tmp_path,
    )
    request = ThreadRunSubmissionRequest(expected_thread_version=1, input=_request("later").input)

    first = await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="submit-queued",
    )
    repeated = await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="submit-queued",
    )

    assert repeated == first
    assert first.outcome == "queued"
    assert first.run is None
    assert first.queued_submission is not None
    assert first.queued_submission.submission.input == request.input
    assert first.queue_version == 1
    async with short_session(lifecycle_interaction_sessions) as database:
        runs = tuple((await database.scalars(select(RunRecord))).all())
    assert [run.id for run in runs] == [source.run_id]


async def test_thread_submission_immediately_continues_completed_head_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _commands_value, objects, source = await _submission_setup(lifecycle_interaction_sessions, tmp_path)
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=source.run_id)
    request = ThreadRunSubmissionRequest(expected_thread_version=2, input=_request("next").input)

    first = await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="submit-continue",
    )
    repeated = await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="submit-continue",
    )

    assert repeated == first
    assert first.outcome == "run_accepted"
    assert first.run is not None and first.run.thread_version == 3
    assert first.queued_submission is None
    async with short_session(lifecycle_interaction_sessions) as database:
        successor = await database.get(RunRecord, first.run.run_id)
    assert successor is not None
    assert successor.parent_run_id == source.run_id
    assert successor.lineage_kind == "continue"


async def test_thread_submission_defaults_waiting_head_and_preserves_new_input(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _commands_value, objects, source = await _submission_setup(lifecycle_interaction_sessions, tmp_path)
    digest = await _wait_run(lifecycle_interaction_sessions, objects, run_id=source.run_id)
    request = ThreadRunSubmissionRequest(
        expected_thread_version=2,
        input=_request("instead").input,
        waiting_resolution=WaitingResolutionDefaults(sealed_state_digest_sha256=digest),
    )

    receipt = await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="submit-waiting",
    )

    assert receipt.run is not None
    async with short_session(lifecycle_interaction_sessions) as database:
        successor = await database.get(RunRecord, receipt.run.run_id)
    assert successor is not None
    assert successor.input_kind == "waiting_continue"
    assert successor.input_json["resolutions"][0]["outcome"] == "reject"
    assert successor.input_json["input"]["content"] == [{"type": "text", "text": "instead"}]


async def test_thread_submission_accepts_root_like_run_after_cancelled_empty_head(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, commands, _objects, source = await _submission_setup(lifecycle_interaction_sessions, tmp_path)
    await commands.interrupt(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="cancel-for-root-like",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    request = ThreadRunSubmissionRequest(expected_thread_version=2, input=_request("restart").input)

    receipt = await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="submit-root-like",
    )

    assert receipt.run is not None
    async with short_session(lifecycle_interaction_sessions) as database:
        successor = await database.get(RunRecord, receipt.run.run_id)
    assert successor is not None
    assert successor.parent_run_id is None
    assert successor.lineage_kind == "root"


async def test_explicit_queue_consumption_accepts_under_retained_authority_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, commands, _objects, source = await _submission_setup(lifecycle_interaction_sessions, tmp_path)
    queued_receipt = await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        request=ThreadRunSubmissionRequest(expected_thread_version=1, input=_request("queued next").input),
        idempotency_key="submit-before-consume",
    )
    assert queued_receipt.queued_submission is not None
    await commands.interrupt(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="cancel-before-consume",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    request = ConsumeQueuedSubmissionRequest(expected_thread_version=2, expected_queue_version=1)

    first = await service.consume(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="consume-first",
    )
    repeated = await service.consume(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="consume-first",
    )

    assert repeated == first
    assert first.outcome == "run_accepted"
    assert first.run is not None
    assert first.run.thread_version == 3
    assert first.queue_version == 2
    assert first.queued_submission.state is QueuedSubmissionState.consumed
    assert first.queued_submission.consumed_run_id == first.run.run_id
    async with short_session(lifecycle_interaction_sessions) as database:
        successor = await database.get(RunRecord, first.run.run_id)
    assert successor is not None
    assert successor.authority_principal_id == queued_receipt.queued_submission.authority_principal.principal_id
    assert successor.parent_run_id is None
    assert successor.lineage_kind == "root"


async def test_queue_mutations_are_replayable_with_original_response(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    thread_id = await _active_thread(lifecycle_interaction_sessions, tmp_path)
    service = await _service(lifecycle_interaction_sessions, tmp_path)

    first = await service.enqueue(
        actor=_actor(),
        thread_id=thread_id,
        expected_thread_version=1,
        submission=_intent("first"),
        idempotency_key="enqueue-first",
    )
    replayed = await service.enqueue(
        actor=_actor(),
        thread_id=thread_id,
        expected_thread_version=1,
        submission=_intent("first"),
        idempotency_key="enqueue-first",
    )
    assert replayed == first
    assert first.queue_version == 1

    entry_id = first.queued_submission.queued_submission_id
    updated = await service.update(
        actor=_actor(),
        queued_submission_id=entry_id,
        request=UpdateQueuedSubmissionRequest(expected_version=1, submission=_intent("updated")),
        idempotency_key="update-first",
    )
    assert updated.queue_version == 2
    await service.update(
        actor=_actor(),
        queued_submission_id=entry_id,
        request=UpdateQueuedSubmissionRequest(expected_version=2, submission=_intent("updated again")),
        idempotency_key="update-second",
    )
    original_update = await service.update(
        actor=_actor(),
        queued_submission_id=entry_id,
        request=UpdateQueuedSubmissionRequest(expected_version=1, submission=_intent("updated")),
        idempotency_key="update-first",
    )
    assert original_update == updated
    assert original_update.queued_submission.version == 2

    deleted = await service.delete(
        actor=_actor(),
        queued_submission_id=entry_id,
        request=DeleteQueuedSubmissionRequest(expected_version=3),
        idempotency_key="delete-first",
    )
    replayed_delete = await service.delete(
        actor=_actor(),
        queued_submission_id=entry_id,
        request=DeleteQueuedSubmissionRequest(expected_version=3),
        idempotency_key="delete-first",
    )
    assert replayed_delete == deleted
    assert deleted.queue_version == 4
    assert (
        await service.list(
            actor=_actor(),
            thread_id=thread_id,
            state=QueuedSubmissionState.queued,
            limit=100,
        )
    ).items == ()


async def test_reorder_replay_precedes_changed_queue_version(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    thread_id = await _active_thread(lifecycle_interaction_sessions, tmp_path)
    service = await _service(lifecycle_interaction_sessions, tmp_path)
    first = await service.enqueue(
        actor=_actor(),
        thread_id=thread_id,
        expected_thread_version=1,
        submission=_intent("first"),
        idempotency_key="enqueue-one",
    )
    second = await service.enqueue(
        actor=_actor(),
        thread_id=thread_id,
        expected_thread_version=1,
        submission=_intent("second"),
        idempotency_key="enqueue-two",
    )
    request = ReorderQueuedSubmissionsRequest(
        expected_queue_version=2,
        queued_submission_ids=(
            second.queued_submission.queued_submission_id,
            first.queued_submission.queued_submission_id,
        ),
    )
    reordered = await service.reorder(
        actor=_actor(),
        thread_id=thread_id,
        request=request,
        idempotency_key="reorder-one",
    )
    await service.update(
        actor=_actor(),
        queued_submission_id=first.queued_submission.queued_submission_id,
        request=UpdateQueuedSubmissionRequest(expected_version=1, submission=_intent("changed")),
        idempotency_key="post-reorder-update",
    )

    replayed = await service.reorder(
        actor=_actor(),
        thread_id=thread_id,
        request=request,
        idempotency_key="reorder-one",
    )

    assert replayed == reordered
    assert replayed.queue_version == 3
