from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.interactions import (
    AttemptExecutionService,
    AttemptPreparationAccepted,
    AttemptScheduler,
    ClaimedAttempt,
)
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.control_domain import QueuedSubmissionState, ThreadRunSubmissionIntent
from a13n_service.interactions.domain import RunLineageKind, RunStatus
from a13n_service.interactions.handoff import CompletionQueueHandoffService
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.inbox_persistence import ThreadInboxConflict
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_completed_continuation_state,
    initialize_empty_thread_state,
)
from a13n_service.interactions.input import AcceptedAgentInput, AgentInput, TextContent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.queue import (
    QueuedSubmissionConflict,
    QueuedSubmissionStore,
    ThreadSubmissionAdmission,
    classify_thread_submission,
)
from a13n_service.storage import ObjectStore, short_session, transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import AGENT_ID, AGENT_REVISION_ID, NOW, TENANT_ID, USER_ID, effective_agent_config
from .test_acceptance import _accepted_run
from .test_attempt_execution import _accept_root, _authority, _completed_state, _worker

pytestmark = pytest.mark.anyio


def _intent(text: str) -> ThreadRunSubmissionIntent:
    return ThreadRunSubmissionIntent(input=AgentInput(schema_version="1", content=(TextContent(text=text),)))


def _principal(principal_id: str = USER_ID) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType.user, principal_id=principal_id)


def test_existing_thread_submission_admission_order_is_explicit() -> None:
    source = _accepted_run(
        run_id="run_1010101010101010",
        thread_id="thread-10101010101010101010101010101010",
        idempotency_key="classify",
        request_fingerprint="1" * 64,
    )
    thread = ThreadRecord(
        id=source.thread_id,
        version=3,
        queue_version=0,
        tenant_id=source.tenant_id,
        session_id=source.session_id,
        role="root",
        origin_kind="new",
        origin_thread_id=None,
        origin_run_id=None,
        head_run_id=source.id,
        current_run_id=source.id,
        created_at=NOW,
        updated_at=NOW,
    ).to_resource()
    waiting = source.model_copy(update={"status": RunStatus.waiting})
    assert (
        classify_thread_submission(
            thread=thread,
            current=waiting,
            head=waiting,
            has_queued_submission=True,
            waiting_resolution_requested=True,
        )
        is ThreadSubmissionAdmission.waiting_continue
    )
    completed = source.model_copy(update={"status": RunStatus.completed})
    assert (
        classify_thread_submission(
            thread=thread,
            current=completed,
            head=completed,
            has_queued_submission=True,
            waiting_resolution_requested=False,
        )
        is ThreadSubmissionAdmission.queued
    )
    assert (
        classify_thread_submission(
            thread=thread,
            current=completed,
            head=completed,
            has_queued_submission=False,
            waiting_resolution_requested=False,
        )
        is ThreadSubmissionAdmission.continuation
    )
    failed = source.model_copy(update={"status": RunStatus.failed})
    no_head = thread.model_copy(update={"head_run_id": None})
    assert (
        classify_thread_submission(
            thread=no_head,
            current=failed,
            head=None,
            has_queued_submission=False,
            waiting_resolution_requested=False,
        )
        is ThreadSubmissionAdmission.root
    )


async def test_queue_crud_reorder_and_versions_are_independent_from_thread_advancement(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    queue = QueuedSubmissionStore(interaction_sessions, clock=lambda: NOW)
    ids = (
        "qsub_1111111111111111",
        "qsub_2222222222222222",
        "qsub_3333333333333333",
    )
    receipts = []
    for index, entry_id in enumerate(ids, start=1):
        receipts.append(
            await queue.enqueue(
                tenant_id=TENANT_ID,
                thread_id=run.thread_id,
                expected_thread_version=1,
                authority_principal=_principal(),
                submission=_intent(str(index)),
                queued_submission_id=entry_id,
            )
        )
    assert [item.queue_version for item in receipts] == [1, 2, 3]
    assert [item.queued_submission.position for item in receipts] == [1, 2, 3]

    unchanged = await queue.reorder(
        tenant_id=TENANT_ID,
        thread_id=run.thread_id,
        expected_queue_version=3,
        queued_submission_ids=ids,
    )
    assert unchanged.queue_version == 3
    reordered = await queue.reorder(
        tenant_id=TENANT_ID,
        thread_id=run.thread_id,
        expected_queue_version=3,
        queued_submission_ids=(ids[2], ids[0], ids[1]),
    )
    assert reordered.queue_version == 4
    assert [
        item.queued_submission_id for item in (await queue.list(tenant_id=TENANT_ID, thread_id=run.thread_id)).items
    ] == [
        ids[2],
        ids[0],
        ids[1],
    ]

    updated = await queue.update(
        tenant_id=TENANT_ID,
        queued_submission_id=ids[0],
        expected_version=1,
        actor_principal=_principal(),
        submission=_intent("updated"),
    )
    assert updated.queue_version == 5
    assert updated.queued_submission.version == 2
    assert updated.queued_submission.submission == _intent("updated")

    deleted = await queue.delete(
        tenant_id=TENANT_ID,
        queued_submission_id=ids[2],
        expected_version=1,
    )
    assert deleted.queue_version == 6
    remaining = (await queue.list(tenant_id=TENANT_ID, thread_id=run.thread_id)).items
    assert [(item.queued_submission_id, item.position) for item in remaining] == [
        (ids[0], 1),
        (ids[1], 2),
    ]
    async with short_session(interaction_sessions) as database:
        thread = await database.get(ThreadRecord, run.thread_id)
        assert thread is not None
        assert thread.version == 1
        assert thread.queue_version == 6


async def test_queue_update_requires_stored_principal_and_exact_version(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    queue = QueuedSubmissionStore(interaction_sessions, clock=lambda: NOW)
    accepted = await queue.enqueue(
        tenant_id=TENANT_ID,
        thread_id=run.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=_intent("first"),
        queued_submission_id="qsub_4444444444444444",
    )

    with pytest.raises(QueuedSubmissionConflict, match="authority Principal"):
        await queue.update(
            tenant_id=TENANT_ID,
            queued_submission_id=accepted.queued_submission.queued_submission_id,
            expected_version=1,
            actor_principal=_principal("usr_9999999999999999"),
            submission=_intent("stolen"),
        )
    with pytest.raises(QueuedSubmissionConflict, match="version"):
        await queue.update(
            tenant_id=TENANT_ID,
            queued_submission_id=accepted.queued_submission.queued_submission_id,
            expected_version=2,
            actor_principal=_principal(),
            submission=_intent("stale"),
        )


async def test_enqueue_rejects_thread_that_can_accept_immediately(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as database:
        source = await database.get(RunRecord, run.id)
        thread = await database.get(ThreadRecord, run.thread_id)
        assert source is not None and thread is not None
        source.status = "failed"
        source.failure_json = SafeFailure(code="test_failure", message="failed").model_dump(mode="json")
        source.sealed_at = NOW
        source.version += 1
        thread.version += 1

    queue = QueuedSubmissionStore(interaction_sessions, clock=lambda: NOW)
    with pytest.raises(QueuedSubmissionConflict, match="immediate Run acceptance"):
        await queue.enqueue(
            tenant_id=TENANT_ID,
            thread_id=run.thread_id,
            expected_thread_version=2,
            authority_principal=_principal(),
            submission=_intent("must become a Run"),
            queued_submission_id="qsub_4545454545454545",
        )

    assert (await queue.list(tenant_id=TENANT_ID, thread_id=run.thread_id)).items == ()
    async with short_session(interaction_sessions) as database:
        thread = await database.get(ThreadRecord, run.thread_id)
        assert thread is not None
        assert (thread.version, thread.queue_version) == (2, 0)


async def test_postgresql_concurrent_enqueues_allocate_distinct_fifo_positions(
    postgres_interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(postgres_interaction_sessions, interaction_object_store)
    queue = QueuedSubmissionStore(postgres_interaction_sessions, clock=lambda: NOW)

    receipts = await asyncio.gather(
        queue.enqueue(
            tenant_id=TENANT_ID,
            thread_id=run.thread_id,
            expected_thread_version=1,
            authority_principal=_principal(),
            submission=_intent("first concurrent submission"),
            queued_submission_id="qsub_4646464646464646",
        ),
        queue.enqueue(
            tenant_id=TENANT_ID,
            thread_id=run.thread_id,
            expected_thread_version=1,
            authority_principal=_principal(),
            submission=_intent("second concurrent submission"),
            queued_submission_id="qsub_4747474747474747",
        ),
    )

    assert sorted(receipt.queue_version for receipt in receipts) == [1, 2]
    queued = (await queue.list(tenant_id=TENANT_ID, thread_id=run.thread_id)).items
    assert [item.position for item in queued] == [1, 2]
    assert {item.queued_submission_id for item in queued} == {
        "qsub_4646464646464646",
        "qsub_4747474747474747",
    }


async def test_queue_consumption_and_run_acceptance_commit_together(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, source, _ = await _accept_root(interaction_sessions, interaction_object_store)
    queue = QueuedSubmissionStore(interaction_sessions, clock=lambda: NOW)
    first = await queue.enqueue(
        tenant_id=TENANT_ID,
        thread_id=source.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=_intent("first"),
        queued_submission_id="qsub_5555555555555555",
    )
    second = await queue.enqueue(
        tenant_id=TENANT_ID,
        thread_id=source.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=_intent("second"),
        queued_submission_id="qsub_6666666666666666",
    )
    async with transaction(interaction_sessions) as database:
        source_record = await database.get(RunRecord, source.id)
        thread = await database.get(ThreadRecord, source.thread_id)
        assert source_record is not None and thread is not None
        source_record.status = "failed"
        source_record.failure_json = SafeFailure(code="test_failure", message="failed").model_dump(mode="json")
        source_record.sealed_at = NOW
        source_record.version += 1
        thread.version += 1
    assert await queue.scan_drainable(tenant_id=TENANT_ID) == (source.thread_id,)
    accepted_input = AcceptedAgentInput(schema_version="1", content=(TextContent(text="first"),))
    config = effective_agent_config()
    seed = RunStateSeed(
        run_id="run_6666666666666666",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=config,
    )
    state = initialize_empty_thread_state(seed, thread_id=source.thread_id)
    run = _accepted_run(
        run_id=seed.run_id,
        thread_id=source.thread_id,
        idempotency_key="consume-first",
        request_fingerprint="6" * 64,
    ).model_copy(update={"input": accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True)})
    receipt = await RunAcceptanceService(
        interaction_sessions,
        RunStateStore(interaction_object_store),
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW,
    ).consume_queued(
        run=run,
        state=state,
        queued_submission_id=first.queued_submission.queued_submission_id,
        submission_digest_sha256=first.queued_submission.submission_digest_sha256,
        accepted_input=accepted_input,
        expected_thread_version=2,
        expected_queue_version=2,
        expected_current_run_id=source.id,
        expected_head_run_id=None,
        next_head_run_id=None,
    )

    assert receipt.run_id == run.id
    consumed = await queue.get(
        tenant_id=TENANT_ID,
        queued_submission_id=first.queued_submission.queued_submission_id,
    )
    assert consumed.state is QueuedSubmissionState.consumed
    assert consumed.consumed_run_id == run.id
    remaining = (await queue.list(tenant_id=TENANT_ID, thread_id=source.thread_id)).items
    assert [(item.queued_submission_id, item.position) for item in remaining] == [
        (second.queued_submission.queued_submission_id, 1)
    ]
    async with short_session(interaction_sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        accepted = await database.get(RunRecord, run.id)
        assert thread is not None and accepted is not None
        assert (thread.version, thread.queue_version, thread.current_run_id) == (3, 3, run.id)
    assert await queue.scan_drainable(tenant_id=TENANT_ID) == ()


async def test_completion_time_handoff_seals_source_and_consumes_queue_atomically(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, source, initial = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_7171717171717171",
    )
    claimed = await scheduler.claim(source.id, _worker())
    assert isinstance(claimed, ClaimedAttempt)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))
    authority = _authority(claimed)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="combined-handoff",
    )
    authority = _authority(
        claimed,
        run_version=entered.run_version,
        attempt_version=entered.attempt_version,
    )
    completed = _completed_state(initial, claimed.attempt.id, claimed.attempt.fence)
    stored = await execution.publish_checkpoint(
        authority,
        states,
        await states.read(TENANT_ID, source.id),
        completed,
    )
    queue = QueuedSubmissionStore(interaction_sessions, clock=lambda: NOW + timedelta(seconds=3))
    queued = await queue.enqueue(
        tenant_id=TENANT_ID,
        thread_id=source.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=_intent("next"),
        queued_submission_id="qsub_7171717171717171",
    )
    accepted_input = AcceptedAgentInput(schema_version="1", content=(TextContent(text="next"),))
    successor_seed = RunStateSeed(
        run_id="run_7171717171717171",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    successor_state = initialize_completed_continuation_state(successor_seed, completed)
    successor = _accepted_run(
        run_id=successor_seed.run_id,
        thread_id=source.thread_id,
        idempotency_key="combined-next",
        request_fingerprint="7" * 64,
    ).model_copy(
        update={
            "parent_run_id": source.id,
            "lineage_kind": RunLineageKind.continue_,
            "input": accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True),
        }
    )

    receipt = await CompletionQueueHandoffService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=4),
    ).complete_and_consume(
        authority=authority,
        source_state=stored,
        successor_run=successor,
        successor_state=successor_state,
        queued_submission_id=queued.queued_submission.queued_submission_id,
        submission_digest_sha256=queued.queued_submission.submission_digest_sha256,
        accepted_input=accepted_input,
        expected_thread_version=1,
        expected_queue_version=1,
        expected_head_run_id=None,
    )

    assert receipt.successor.thread_version == 3
    assert receipt.queue_version == 2
    async with short_session(interaction_sessions) as database:
        source_row = await database.get(RunRecord, source.id)
        successor_row = await database.get(RunRecord, successor.id)
        attempt = await database.get(RunAttemptRecord, claimed.attempt.id)
        thread = await database.get(ThreadRecord, source.thread_id)
        assert source_row is not None and successor_row is not None and attempt is not None and thread is not None
        assert source_row.status == "completed"
        assert successor_row.status == "accepted"
        assert attempt.status == "succeeded"
        assert (thread.version, thread.queue_version, thread.head_run_id, thread.current_run_id) == (
            3,
            2,
            source.id,
            successor.id,
        )


async def test_completion_time_handoff_rolls_back_when_pending_delivery_blocks_completion(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, source, initial = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_8181818181818181",
    )
    claimed = await scheduler.claim(source.id, _worker())
    assert isinstance(claimed, ClaimedAttempt)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))
    authority = _authority(claimed)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="blocked-combined-handoff",
    )
    authority = _authority(
        claimed,
        run_version=entered.run_version,
        attempt_version=entered.attempt_version,
    )
    completed = _completed_state(initial, claimed.attempt.id, claimed.attempt.fence)
    stored = await execution.publish_checkpoint(
        authority,
        states,
        await states.read(TENANT_ID, source.id),
        completed,
    )
    await ThreadInboxStore(interaction_sessions, clock=lambda: NOW + timedelta(seconds=3)).append_steer(
        tenant_id=TENANT_ID,
        run_id=source.id,
        input=AcceptedAgentInput(
            schema_version="1",
            content=(TextContent(text="must be delivered first"),),
        ),
        entry_id="inb_8181818181818181",
    )
    queue = QueuedSubmissionStore(interaction_sessions, clock=lambda: NOW + timedelta(seconds=3))
    queued = await queue.enqueue(
        tenant_id=TENANT_ID,
        thread_id=source.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=_intent("next"),
        queued_submission_id="qsub_8181818181818181",
    )
    accepted_input = AcceptedAgentInput(schema_version="1", content=(TextContent(text="next"),))
    successor_seed = RunStateSeed(
        run_id="run_8181818181818181",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )
    successor_state = initialize_completed_continuation_state(successor_seed, completed)
    successor = _accepted_run(
        run_id=successor_seed.run_id,
        thread_id=source.thread_id,
        idempotency_key="blocked-combined-next",
        request_fingerprint="8" * 64,
    ).model_copy(
        update={
            "parent_run_id": source.id,
            "lineage_kind": RunLineageKind.continue_,
            "input": accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True),
        }
    )

    with pytest.raises(ThreadInboxConflict, match="pending inbox delivery"):
        await CompletionQueueHandoffService(
            interaction_sessions,
            states,
            RunPayloadStore(interaction_object_store),
            clock=lambda: NOW + timedelta(seconds=4),
        ).complete_and_consume(
            authority=authority,
            source_state=stored,
            successor_run=successor,
            successor_state=successor_state,
            queued_submission_id=queued.queued_submission.queued_submission_id,
            submission_digest_sha256=queued.queued_submission.submission_digest_sha256,
            accepted_input=accepted_input,
            expected_thread_version=1,
            expected_queue_version=1,
            expected_head_run_id=None,
        )

    async with short_session(interaction_sessions) as database:
        source_row = await database.get(RunRecord, source.id)
        successor_row = await database.get(RunRecord, successor.id)
        attempt = await database.get(RunAttemptRecord, claimed.attempt.id)
        thread = await database.get(ThreadRecord, source.thread_id)
        assert source_row is not None and attempt is not None and thread is not None
        assert source_row.status == "running"
        assert successor_row is None
        assert attempt.status == "running"
        assert (thread.version, thread.queue_version, thread.current_run_id) == (1, 1, source.id)
    assert (
        await queue.get(
            tenant_id=TENANT_ID,
            queued_submission_id=queued.queued_submission.queued_submission_id,
        )
    ).state is QueuedSubmissionState.queued
