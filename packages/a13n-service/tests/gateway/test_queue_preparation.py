from __future__ import annotations

import asyncio

import pytest
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.interactions.control_domain import (
    ConsumeQueuedSubmissionRequest,
    InterruptRequest,
    QueuedSubmissionState,
    ReorderQueuedSubmissionsRequest,
    ThreadRunSubmissionRequest,
    UpdateQueuedSubmissionRequest,
)
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.submissions import DeleteQueuedSubmissionRequest
from a13n_service.storage import short_session
from anyio import Event, fail_after
from sqlalchemy import func, select

from tests.gateway.test_commands import _actor, _request
from tests.gateway.test_queue import _submission_setup
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.test_environment_runtime import template_config

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("change", ["intent", "environment", "reorder", "delete"])
async def test_prepared_queue_snapshot_is_rechecked_before_consumption(
    lifecycle_interaction_sessions, tmp_path, monkeypatch, change
):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    service, commands, _, source = await _submission_setup(sessions, tmp_path)
    submissions = []
    for text in ("first", "second"):
        receipt = await service.submit(
            actor=_actor(),
            thread_id=source.thread_id,
            request=ThreadRunSubmissionRequest(expected_thread_version=1, input=_request(text).input),
            idempotency_key=text,
        )
        assert receipt.queued_submission is not None
        submissions.append(receipt.queued_submission)
    first, second = submissions
    await commands.active.interrupt(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="cancel",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    prepare = commands.queued.prepare_queued_run
    changed = False

    async def change_after_preparation(**kwargs):
        nonlocal changed
        prepared = await prepare(**kwargs)
        assert not changed
        changed = True
        if change in {"intent", "environment"}:
            updates = (
                {"input": _request("edited after preparation").input} if change == "intent" else {"environment": None}
            )
            await service.update(
                actor=_actor(),
                queued_submission_id=first.queued_submission_id,
                idempotency_key="edit-after-preparation",
                request=UpdateQueuedSubmissionRequest(
                    expected_version=1, submission=first.submission.model_copy(update=updates)
                ),
            )
        elif change == "reorder":
            await service.reorder(
                actor=_actor(),
                thread_id=source.thread_id,
                idempotency_key="reorder-after-preparation",
                request=ReorderQueuedSubmissionsRequest(
                    expected_queue_version=2,
                    queued_submission_ids=(second.queued_submission_id, first.queued_submission_id),
                ),
            )
        else:
            await service.delete(
                actor=_actor(),
                queued_submission_id=first.queued_submission_id,
                idempotency_key="delete-after-preparation",
                request=DeleteQueuedSubmissionRequest(expected_version=1),
            )
        return prepared

    monkeypatch.setattr(commands.queued, "prepare_queued_run", change_after_preparation)
    with pytest.raises(InteractionCommandError) as caught:
        await service.consume(
            actor=_actor(),
            thread_id=source.thread_id,
            idempotency_key="consume-stale-intent",
            # Match the post-edit version so the locked head/digest check must
            # reject the snapshot, independently of the queue version guard.
            request=ConsumeQueuedSubmissionRequest(expected_thread_version=2, expected_queue_version=3),
        )
    assert changed and caught.value.code == "queue_consumption_conflict"
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1
        thread = await database.get(ThreadRecord, source.thread_id)
        assert (thread.version, thread.queue_version, thread.current_run_id) == (2, 3, source.run_id)
    remaining = await service.list(
        actor=_actor(), thread_id=source.thread_id, state=QueuedSubmissionState.queued, limit=100
    )
    assert all(item.consumed_run_id is None for item in remaining.items)
    expected = (
        [second.queued_submission_id]
        if change == "delete"
        else (
            [second.queued_submission_id, first.queued_submission_id]
            if change == "reorder"
            else [first.queued_submission_id, second.queued_submission_id]
        )
    )
    assert [item.queued_submission_id for item in remaining.items] == expected


@pytest.mark.parametrize("explicit_null", [False, True])
async def test_queued_environment_omission_and_null_survive_preparation(
    lifecycle_interaction_sessions, tmp_path, explicit_null
):
    sessions = lifecycle_interaction_sessions
    await template_config(sessions, tmp_path, "on_use")
    service, commands, _, source = await _submission_setup(sessions, tmp_path)
    async with short_session(sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        default_environment_id = thread.default_environment_id
        assert default_environment_id is not None
    request = ThreadRunSubmissionRequest(expected_thread_version=1, input=_request("queued").input)
    if explicit_null:
        request = request.model_copy(update={"environment": None})
    await service.submit(
        actor=_actor(), thread_id=source.thread_id, request=request, idempotency_key="queue-environment"
    )
    await commands.active.interrupt(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="cancel",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    consumed = await service.consume(
        actor=_actor(),
        thread_id=source.thread_id,
        idempotency_key="consume-environment",
        request=ConsumeQueuedSubmissionRequest(expected_thread_version=2, expected_queue_version=1),
    )
    assert consumed.run is not None
    async with short_session(sessions) as database:
        run = await database.get(RunRecord, consumed.run.run_id)
        assert run.environment_id == (None if explicit_null else default_environment_id)
        assert await database.scalar(select(func.count()).select_from(EnvironmentRecord)) == 1


async def test_concurrent_consumers_accept_one_prepared_queue_snapshot(
    lifecycle_interaction_sessions, tmp_path, monkeypatch
):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    service, commands, _, source = await _submission_setup(sessions, tmp_path)
    await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        request=ThreadRunSubmissionRequest(expected_thread_version=1, input=_request("queued").input),
        idempotency_key="queue",
    )
    await commands.active.interrupt(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="cancel",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    prepare = commands.queued.prepare_queued_run
    both_prepared = Event()
    arrivals = 0

    async def prepare_together(**kwargs):
        nonlocal arrivals
        prepared = await prepare(**kwargs)
        arrivals += 1
        if arrivals == 2:
            both_prepared.set()
        await both_prepared.wait()
        return prepared

    monkeypatch.setattr(commands.queued, "prepare_queued_run", prepare_together)

    async def consume(key):
        return await service.consume(
            actor=_actor(),
            thread_id=source.thread_id,
            idempotency_key=key,
            request=ConsumeQueuedSubmissionRequest(expected_thread_version=2, expected_queue_version=1),
        )

    keys = ("consumer-one", "consumer-two")
    with fail_after(10):
        results = await asyncio.gather(*(consume(key) for key in keys), return_exceptions=True)
    assert sum(isinstance(result, InteractionCommandError) for result in results) == 1
    receipts = [result for result in results if not isinstance(result, BaseException)]
    assert len(receipts) == 1 and receipts[0].run is not None
    receipt = receipts[0]
    assert await consume(keys[results.index(receipt)]) == receipt
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 2
        thread = await database.get(ThreadRecord, source.thread_id)
        assert (thread.version, thread.queue_version, thread.current_run_id) == (3, 2, receipt.run.run_id)
    assert receipt.queued_submission.consumed_run_id == receipt.run.run_id
