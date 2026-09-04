from __future__ import annotations

import pytest
from a13n_service.gateway.queue import DeleteQueuedSubmissionRequest, NativeQueuedSubmissionService
from a13n_service.interactions import (
    QueuedSubmissionState,
    QueuedSubmissionStore,
    ReorderQueuedSubmissionsRequest,
    ThreadRunSubmissionIntent,
    UpdateQueuedSubmissionRequest,
)
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.gateway.test_commands import _actor, _commands, _Freezing, _frozen, _Preparation, _request
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import NOW, WORKSPACE_ID
from tests.interactions.test_queue import _inline_hooks

pytestmark = pytest.mark.anyio


def _intent(text: str) -> ThreadRunSubmissionIntent:
    return ThreadRunSubmissionIntent(input=_request(text).input)


def _service(sessions: async_sessionmaker[AsyncSession]) -> NativeQueuedSubmissionService:
    return NativeQueuedSubmissionService(
        sessions,
        QueuedSubmissionStore(sessions, _inline_hooks(), clock=lambda: NOW),
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


async def test_queue_mutations_are_replayable_with_original_response(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    thread_id = await _active_thread(lifecycle_interaction_sessions, tmp_path)
    service = _service(lifecycle_interaction_sessions)

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
    service = _service(lifecycle_interaction_sessions)
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
