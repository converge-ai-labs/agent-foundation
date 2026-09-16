from __future__ import annotations

import asyncio

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.gateway import router as gateway_router
from a13n_service.iam import authenticate_request
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
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.interactions.submissions import DeleteQueuedSubmissionRequest, QueuedSubmissionService
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from anyio import Event
from fastapi import FastAPI
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
from tests.interactions.conftest import interaction_sessions as interaction_sessions
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
    accepted = await commands.runs.start(
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
    accepted = await commands.runs.start(
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
    request = ThreadRunSubmissionRequest(
        expected_thread_version=2, input=_request("next").input, labels={"batch": "next"}
    )

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
    assert successor.labels == {"batch": "next"}


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
        labels={"batch": "waiting"},
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
    assert successor.labels == {"batch": "waiting"}
    assert successor.input_json["resolutions"][0]["outcome"] == "reject"
    assert successor.input_json["input"]["content"] == [{"type": "text", "text": "instead"}]


async def test_thread_submission_accepts_root_like_run_after_cancelled_empty_head(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, commands, _objects, source = await _submission_setup(lifecycle_interaction_sessions, tmp_path)
    await commands.active.interrupt(
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
        request=ThreadRunSubmissionRequest(
            expected_thread_version=1, input=_request("queued next").input, labels={"batch": "queued"}
        ),
        idempotency_key="submit-before-consume",
    )
    assert queued_receipt.queued_submission is not None
    await commands.active.interrupt(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="cancel-before-consume",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    async with transaction(lifecycle_interaction_sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        thread.labels = {"team": "latest-before-consume", "batch": "parent"}
    request = ConsumeQueuedSubmissionRequest(expected_thread_version=2, expected_queue_version=1)

    first = await service.consume(
        actor=_actor(),
        thread_id=source.thread_id,
        request=request,
        idempotency_key="consume-first",
    )
    async with transaction(lifecycle_interaction_sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        thread.labels = {"team": "after-acceptance"}
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
    assert successor.labels == {"team": "latest-before-consume", "batch": "queued"}


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


@pytest.mark.parametrize("branch", ["queued", "completed", "waiting", "root"])
async def test_concurrent_thread_submission_owns_one_receipt_and_replays_after_queue_changes(
    interaction_sessions, tmp_path, monkeypatch, branch
):
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    service, commands, objects, source = await _submission_setup(sessions, tmp_path)
    waiting_resolution = None
    if branch == "completed":
        await _complete_run(sessions, objects, run_id=source.run_id)
    elif branch == "waiting":
        digest = await _wait_run(sessions, objects, run_id=source.run_id)
        waiting_resolution = WaitingResolutionDefaults(sealed_state_digest_sha256=digest)
    elif branch == "root":
        await commands.active.interrupt(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="prepare-root",
            request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
        )
    request = ThreadRunSubmissionRequest(
        expected_thread_version=1 if branch == "queued" else 2,
        input=_request("submit once").input,
        waiting_resolution=waiting_resolution,
    )
    async with short_session(sessions) as database:
        original_evidence = set(await database.scalars(select(IdempotencyEvidenceRecord.id)))
    admission = service._submission_admission
    ready = Event()
    arrivals = 0

    async def overlapping_admission(**kwargs):
        nonlocal arrivals
        selected = await admission(**kwargs)
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await ready.wait()
        return selected

    monkeypatch.setattr(service, "_submission_admission", overlapping_admission)

    async def submit():
        return await service.submit(
            actor=_actor(), thread_id=source.thread_id, request=request, idempotency_key="same-thread-submit"
        )

    first, second = await asyncio.gather(submit(), submit())
    assert first == second
    async with short_session(sessions) as database:
        evidence = list(await database.scalars(select(IdempotencyEvidenceRecord)))
        added = [row for row in evidence if row.id not in original_evidence]
        assert len(added) == 1
        assert added[0].operation == "thread.submit"
        assert added[0].scope_id == source.thread_id
        assert added[0].receipt_json == first.model_dump(mode="json", by_alias=True)
        assert len(list(await database.scalars(select(RunRecord)))) == (1 if branch == "queued" else 2)
    monkeypatch.setattr(service, "_submission_admission", admission)
    if first.queued_submission is not None:
        await service.update(
            actor=_actor(),
            queued_submission_id=first.queued_submission.queued_submission_id,
            request=UpdateQueuedSubmissionRequest(expected_version=1, submission=_intent("edited later")),
            idempotency_key="edit-after-submit",
        )
    else:
        assert first.run is not None
        await service.enqueue(
            actor=_actor(),
            thread_id=source.thread_id,
            expected_thread_version=first.run.thread_version,
            submission=_intent("later queue item"),
            idempotency_key="enqueue-after-submit",
        )
    # Replay returns the original queue generation and body, before reclassifying the current Thread.
    assert await submit() == first
    with pytest.raises(InteractionCommandError) as conflict:
        await service.submit(
            actor=_actor(),
            thread_id=source.thread_id,
            idempotency_key="same-thread-submit",
            request=request.model_copy(update={"input": _request("different request").input}),
        )
    assert conflict.value.code == "idempotency_conflict"


async def test_thread_submission_retry_after_lost_response_returns_original_receipt(
    lifecycle_interaction_sessions, tmp_path, monkeypatch
):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    service, _, objects, source = await _submission_setup(sessions, tmp_path)
    await _complete_run(sessions, objects, run_id=source.run_id)
    request = ThreadRunSubmissionRequest(expected_thread_version=2, input=_request("next").input)
    accept = service._submit
    committed = []

    async def lose_response(**kwargs):
        committed.append(await accept(**kwargs))
        raise ConnectionError("response lost after COMMIT")

    monkeypatch.setattr(service, "_submit", lose_response)
    with pytest.raises(ConnectionError):
        await service.submit(actor=_actor(), thread_id=source.thread_id, request=request, idempotency_key="lost")
    replay = await service.submit(actor=_actor(), thread_id=source.thread_id, request=request, idempotency_key="lost")
    assert committed == [replay]
    async with short_session(sessions) as database:
        assert len(list(await database.scalars(select(RunRecord)))) == 2
        assert (
            len(
                list(
                    await database.scalars(
                        select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation == "thread.submit")
                    )
                )
            )
            == 1
        )


async def test_delete_http_query_contract_and_empty_replay_after_row_removal(
    lifecycle_interaction_sessions, tmp_path, monkeypatch
):
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    thread_id = await _active_thread(lifecycle_interaction_sessions, tmp_path)
    service = await _service(lifecycle_interaction_sessions, tmp_path)
    entries = [
        await service.enqueue(
            actor=_actor(),
            thread_id=thread_id,
            expected_thread_version=1,
            submission=_intent(text),
            idempotency_key=f"enqueue-{text}",
        )
        for text in ("first", "second")
    ]
    entry_id = entries[0].queued_submission.queued_submission_id
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(gateway_router.router)
    app.dependency_overrides[authenticate_request] = _actor
    monkeypatch.setattr(gateway_router, "_queued_submissions", lambda _request: service)
    path = f"/api/v1/queued-submissions/{entry_id}"
    async with httpx2.AsyncClient(base_url="http://testserver", transport=httpx2.ASGITransport(app=app)) as client:
        headers = {"Idempotency-Key": "delete-query"}
        # A JSON body is not a substitute for the required query precondition.
        body_only = await client.request("DELETE", path, headers=headers, json={"expected_version": 1})
        assert body_only.status_code == 400
        for version in ("0", "-1", "invalid"):
            invalid = await client.delete(path, params={"expected_version": version}, headers=headers)
            assert invalid.status_code == 400
        missing_key = await client.delete(path, params={"expected_version": 1})
        assert missing_key.status_code == 400
        stale = await client.delete(path, params={"expected_version": 2}, headers=headers)
        assert stale.status_code == 409
        assert (await service.get(actor=_actor(), queued_submission_id=entry_id)).version == 1
        # One mutation, including when identical HTTP requests race; both return no content.
        replies = await asyncio.gather(
            *(client.delete(path, params={"expected_version": 1}, headers=headers) for _ in range(2))
        )
        for reply in replies:
            assert reply.status_code == 204, reply.text
            assert reply.content == b"" and "content-type" not in reply.headers
        assert (await client.get(path)).status_code == 404
        replay = await client.delete(path, params={"expected_version": 1}, headers=headers)
        assert replay.status_code == 204 and replay.content == b""
        changed = await client.delete(path, params={"expected_version": 2}, headers=headers)
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "idempotency_conflict"
        absent = await client.delete(path, params={"expected_version": 1}, headers={"Idempotency-Key": "new-delete"})
        assert absent.status_code == 404
    remaining = await service.list(actor=_actor(), thread_id=thread_id, state=QueuedSubmissionState.queued, limit=100)
    assert len(remaining.items) == 1 and remaining.items[0].position == 1
    async with short_session(lifecycle_interaction_sessions) as database:
        thread = await database.get(ThreadRecord, thread_id)
        assert thread is not None and thread.queue_version == 3 and thread.version == 1
        evidence = (
            await database.scalars(
                select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation == "queue.delete")
            )
        ).all()
        assert len(evidence) == 1


async def test_delete_replays_concurrent_commit_after_preflight(lifecycle_interaction_sessions, tmp_path, monkeypatch):

    await seed_hook_actor_access(lifecycle_interaction_sessions)
    thread_id = await _active_thread(lifecycle_interaction_sessions, tmp_path)
    service = await _service(lifecycle_interaction_sessions, tmp_path)
    queued = await service.enqueue(
        actor=_actor(),
        thread_id=thread_id,
        expected_thread_version=1,
        submission=_intent("delete-race"),
        idempotency_key="enqueue-race",
    )
    reached, release = Event(), Event()
    original = service._submission_scope
    pause = True

    async def delayed(*args, **kwargs):
        nonlocal pause
        if pause:
            pause = False
            reached.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "_submission_scope", delayed)
    kwargs = {
        "actor": _actor(),
        "queued_submission_id": queued.queued_submission.queued_submission_id,
        "request": DeleteQueuedSubmissionRequest(expected_version=1),
        "idempotency_key": "same-delete",
    }
    pending = asyncio.create_task(service.delete(**kwargs))
    try:
        async with asyncio.timeout(10):
            await reached.wait()
            committed = await service.delete(**kwargs)
            release.set()
            assert await pending == committed
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
